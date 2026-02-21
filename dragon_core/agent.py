# dragon_core/agent.py
"""
Dragon Agent — Industrial Engage Kernel (Beast Mode)

What it does per tick:
1) Append-only ledger: runtime/dragon/dragon_ledger.jsonl
2) Optional hawk freshness gate (runtime/hawk/status.json)
3) Fetch X mentions using state.x_since_id (dedupe)
4) Plan actions (daily status posts + mention replies)
5) Hard rate-limit + policy gating (via engager)
6) Enqueue -> Execute (X + Moltbook)
7) Persist state (since_id + last daily post timestamps)

No dragon_actions_next file required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import datetime as dt
import json
import time
import uuid

from .engage import DragonEngager, EngageConfig
from .planner import plan_actions, PlanConfig
from .ratelimit import RateLimiter
from .state import load_state, save_state, touch_run
from .x_client import XClient


UTC = dt.timezone.utc


# -------------------------
# Ledger (append-only JSONL)
# -------------------------

@dataclass(frozen=True)
class LedgerEvent:
    ts_utc: str
    run_id: str
    level: str
    event_type: str
    message: str
    evidence: Dict[str, Any]


class Ledger:
    def __init__(self, path: Path, *, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_run_id() -> str:
        return uuid.uuid4().hex

    def _emit(self, level: str, event_type: str, message: str, evidence: Dict[str, Any]) -> None:
        ev = LedgerEvent(
            ts_utc=_iso_utc(_now_utc()),
            run_id=self.run_id,
            level=level,
            event_type=event_type,
            message=message,
            evidence=evidence or {},
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.__dict__, ensure_ascii=False) + "\n")

    def info(self, event_type: str, message: str, evidence: Dict[str, Any]) -> None:
        self._emit("INFO", event_type, message, evidence)

    def warn(self, event_type: str, message: str, evidence: Dict[str, Any]) -> None:
        self._emit("WARN", event_type, message, evidence)

    def error(self, event_type: str, message: str, evidence: Dict[str, Any]) -> None:
        self._emit("ERROR", event_type, message, evidence)


# -------------------------
# Hawk freshness (optional)
# -------------------------

def _now_utc() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def _iso_utc(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_last_tick_utc(value: Any) -> Optional[dt.datetime]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        ts = dt.datetime.fromisoformat(s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)
    except Exception:
        return None


def hawk_freshness(runtime_dir: Path, ledger: Ledger, *, limit_sec: int = 180) -> Dict[str, Any]:
    """
    Returns:
      {
        "hawk_present": bool,
        "freshness_ok": bool,
        "reason": str
      }
    """
    status_path = runtime_dir / "hawk" / "status.json"
    if not status_path.exists():
        return {"hawk_present": False, "freshness_ok": True, "reason": "NO_HAWK_STATUS"}

    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as e:
        ledger.warn("HAWK_STATUS_INVALID", str(e), {"path": str(status_path)})
        return {"hawk_present": True, "freshness_ok": False, "reason": "INVALID_STATUS_JSON"}

    last_tick = _parse_last_tick_utc(status.get("last_tick_utc"))
    freshness_sec = status.get("data_freshness_sec")

    if last_tick is None or not isinstance(freshness_sec, (int, float)):
        return {"hawk_present": True, "freshness_ok": False, "reason": "MISSING_FIELDS"}

    age = (_now_utc() - last_tick).total_seconds()
    if age > limit_sec:
        return {"hawk_present": True, "freshness_ok": False, "reason": f"STALE_LAST_TICK_{int(age)}s"}

    if float(freshness_sec) > float(limit_sec):
        return {"hawk_present": True, "freshness_ok": False, "reason": f"STALE_FRESHNESS_{int(float(freshness_sec))}s"}

    return {"hawk_present": True, "freshness_ok": True, "reason": "OK"}


# -------------------------
# Dragon Agent
# -------------------------

@dataclass(frozen=True)
class DragonAgent:
    mandate: str = "ENGAGE"

    def run_once(self, runtime_dir: Path) -> int:
        runtime_dir = runtime_dir.expanduser().resolve()
        dragon_dir = runtime_dir / "dragon"
        dragon_dir.mkdir(parents=True, exist_ok=True)

        run_id = Ledger.new_run_id()
        ledger = Ledger(path=dragon_dir / "dragon_ledger.jsonl", run_id=run_id)

        ledger.info("RUN_START", "Dragon tick started", {"runtime": str(runtime_dir), "mandate": self.mandate})

        # Load + touch state
        state = load_state(runtime_dir)
        touch_run(state)

        # Hawk freshness gate
        f = hawk_freshness(runtime_dir, ledger)
        freshness_ok = bool(f.get("freshness_ok", True))
        ledger.info("HAWK_FRESHNESS", str(f.get("reason")), {"freshness_ok": freshness_ok, "hawk_present": f.get("hawk_present")})

        # Fetch mentions with since_id
        mentions_payload: Optional[Dict[str, Any]] = None
        try:
            x = XClient.from_env()
            me = x.me()
            uid = me["data"]["id"]
            mentions_payload = x.mentions(uid, since_id=state.x_since_id, max_results=10)
            cnt = len((mentions_payload.get("data") or []))
            ledger.info("X_MENTIONS_FETCHED", f"count={cnt}", {"since_id": state.x_since_id})
        except Exception as e:
            ledger.warn("X_MENTIONS_SKIPPED", str(e), {"since_id": state.x_since_id})

        # Plan actions
        actions, new_since_id = plan_actions(runtime_dir, PlanConfig(), state=state, mentions_payload=mentions_payload)

        # Hard rate-limit (per channel)
        rl = RateLimiter(max_actions_per_window=5, window_sec=300)
        filtered: List[Dict[str, Any]] = []
        for a in actions:
            ch = str(a.get("channel") or "")
            if rl.allow(ch):
                filtered.append(a)
            else:
                ledger.warn("RATE_LIMIT_BLOCK", "blocked by channel window", {"channel": ch, "action_id": a.get("action_id")})

        ledger.info("ACTIONS_PLANNED", f"count={len(filtered)}", {"sample": filtered[:2]})

        # Enqueue + execute
        engager = DragonEngager(runtime_dir, ledger, EngageConfig(require_freshness_ok=True))
        engager.enqueue_actions(filtered)
        engager.execute_outbox(freshness_ok=freshness_ok)

        # Persist state (conservative: mark daily posts as attempted)
        now = int(time.time())
        for a in filtered:
            if a.get("type") == "post" and a.get("metadata", {}).get("kind") == "daily_status":
                if a.get("channel") == "x":
                    state.last_daily_post_unix_x = now
                if a.get("channel") == "moltbook":
                    state.last_daily_post_unix_moltbook = now

        if new_since_id:
            state.x_since_id = new_since_id

        save_state(runtime_dir, state)

        ledger.info("RUN_END", "Dragon tick finished", {"run_id": run_id})
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="dragon-agent", description="Dragon Agent (industrial engage kernel)")
    p.add_argument("--runtime", required=True, help="Runtime dir containing dragon/ (and optionally hawk/)")
    args = p.parse_args(list(argv) if argv is not None else None)

    agent = DragonAgent(mandate="ENGAGE")
    return agent.run_once(Path(args.runtime))


if __name__ == "__main__":
    raise SystemExit(main())