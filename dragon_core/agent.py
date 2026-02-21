# dragon_core/agent.py
"""
Dragon Core — governed operational agent kernel.

This module intentionally provides:
- Deterministic runtime ingestion from a file-based handshake (hawk -> dragon)
- Auditable, append-only ledger
- Plugin "muscles" for deterministic analysis and draft-only outputs
- Fail-closed behavior (stale/invalid inputs => inert outputs)

CLI:
  python -m dragon_core.agent observe --runtime ./runtime --once
  python -m dragon_core.agent observe --runtime ./runtime --loop --interval 10

Runtime contract (default):
  Inputs:
    <runtime>/hawk/status.json
    <runtime>/hawk/positions.json
    <runtime>/hawk/orders.json
    <runtime>/hawk/risk.json
    <runtime>/hawk/performance.json

  Outputs:
    <runtime>/dragon/dragon_heartbeat.json
    <runtime>/dragon/dragon_flags.json
    <runtime>/dragon/dragon_digest.json
    <runtime>/dragon/dragon_actions_next.json
    <runtime>/dragon/dragon_ledger.jsonl (append-only)
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple


UTC = dt.timezone.utc


# -------------------------
# Public API (backward compatible)
# -------------------------

class DragonAgent:
    """
    Backward-compatible entry object.

    You can still do:
      agent = DragonAgent(mandate="...")
      agent.execute(payload={"runtime_dir": "...", ...})

    But the "real" power is:
      agent.observe(runtime_dir=Path("./runtime"), once=True)
    """

    def __init__(self, mandate: str) -> None:
        self.mandate = mandate

    def execute(self, payload: dict) -> dict:
        """
        Legacy-style entrypoint; deterministic and side-effect free unless runtime_dir provided.

        Expected payload keys (optional):
          - runtime_dir: str
          - mode: "observe" (default)
          - once: bool (default True)
          - interval: int (default 10)

        Returns an acknowledgement (and runs observe if runtime_dir is provided).
        """
        runtime_dir_raw = payload.get("runtime_dir")
        mode = str(payload.get("mode", "observe")).strip().lower()
        once = bool(payload.get("once", True))
        interval = int(payload.get("interval", 10))

        if runtime_dir_raw and mode == "observe":
            code = observe_loop(
                runtime_dir=Path(str(runtime_dir_raw)).expanduser().resolve(),
                once=once,
                interval_sec=interval,
                mandate=self.mandate,
            )
            return {"status": "completed" if code == 0 else "error", "exit_code": code, "mandate": self.mandate}

        return {
            "status": "acknowledged",
            "mandate": self.mandate,
            "payload_received": True,
            "note": "Provide payload.runtime_dir to run observer mode deterministically.",
        }

    def observe(self, runtime_dir: Path, *, once: bool = True, interval_sec: int = 10) -> int:
        return observe_loop(runtime_dir=runtime_dir, once=once, interval_sec=interval_sec, mandate=self.mandate)


# -------------------------
# Errors
# -------------------------

class DragonContractError(Exception):
    """Raised when runtime contract is violated."""


class DragonOutputError(Exception):
    """Raised when outputs cannot be written."""


# -------------------------
# Helpers
# -------------------------

def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def iso_utc(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise DragonContractError(f"Invalid JSON: {path} :: {e}") from e


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    safe_mkdir(path.parent)
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        raise DragonOutputError(f"Failed to write output: {path} :: {e}") from e
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def parse_last_tick_utc(value: Any) -> Optional[dt.datetime]:
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


# -------------------------
# Ledger
# -------------------------

@dataclass(frozen=True)
class LedgerEvent:
    ts_utc: str
    run_id: str
    mandate: str
    level: str
    event_type: str
    message: str
    evidence: Dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)


class Ledger:
    def __init__(self, path: Path, *, run_id: str, mandate: str) -> None:
        self._path = path
        self._run_id = run_id
        self._mandate = mandate
        safe_mkdir(self._path.parent)

    @property
    def run_id(self) -> str:
        return self._run_id

    def emit(self, *, level: str, event_type: str, message: str, evidence: Optional[Dict[str, Any]] = None) -> None:
        ev = LedgerEvent(
            ts_utc=iso_utc(now_utc()),
            run_id=self._run_id,
            mandate=self._mandate,
            level=level,
            event_type=event_type,
            message=message,
            evidence=evidence or {},
        )
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(ev.to_json() + "\n")
        except Exception as e:
            raise DragonOutputError(f"Failed to append ledger: {self._path} :: {e}") from e


# -------------------------
# Runtime snapshot
# -------------------------

REQUIRED_HAWK_FILES: Dict[str, Tuple[str, ...]] = {
    "status.json": ("last_tick_utc", "data_freshness_sec"),
    "positions.json": tuple(),
    "orders.json": tuple(),
    "risk.json": tuple(),
    "performance.json": tuple(),
}


@dataclass(frozen=True)
class FileEvidence:
    path: str
    sha256: str


@dataclass(frozen=True)
class HawkInputs:
    status: Dict[str, Any]
    positions: Dict[str, Any]
    orders: Dict[str, Any]
    risk: Dict[str, Any]
    performance: Dict[str, Any]


@dataclass(frozen=True)
class RuntimeSnapshot:
    runtime_dir: Path
    hawk_dir: Path
    dragon_dir: Path
    created_utc: dt.datetime
    hawk: HawkInputs
    evidence: Dict[str, FileEvidence]
    freshness_ok: bool
    freshness_reason: str


def validate_snapshot(runtime_dir: Path, ledger: Ledger, *, freshness_limit_sec: int = 180) -> RuntimeSnapshot:
    hawk_dir = runtime_dir / "hawk"
    dragon_dir = runtime_dir / "dragon"

    if not hawk_dir.exists():
        raise DragonContractError(f"Missing hawk runtime dir: {hawk_dir}")

    safe_mkdir(dragon_dir)

    evidence: Dict[str, FileEvidence] = {}
    loaded: Dict[str, Dict[str, Any]] = {}

    for fname, required_keys in REQUIRED_HAWK_FILES.items():
        p = hawk_dir / fname
        if not p.exists():
            raise DragonContractError(f"Missing required hawk input: {p}")
        obj = read_json(p)
        for k in required_keys:
            if k not in obj:
                raise DragonContractError(f"Missing key '{k}' in {p}")
        loaded[fname] = obj
        evidence[fname] = FileEvidence(path=str(p), sha256=sha256_file(p))

    status = loaded["status.json"]
    last_tick = parse_last_tick_utc(status.get("last_tick_utc"))
    freshness_sec = status.get("data_freshness_sec")

    if not isinstance(freshness_sec, (int, float)):
        raise DragonContractError("hawk/status.json: data_freshness_sec must be number")

    ok = True
    reason = "OK"
    now = now_utc()

    if last_tick is None:
        ok = False
        reason = "INVALID_LAST_TICK"
    else:
        age = (now - last_tick).total_seconds()
        if age > freshness_limit_sec:
            ok = False
            reason = f"STALE_LAST_TICK_{int(age)}s"

    if float(freshness_sec) > float(freshness_limit_sec):
        ok = False
        reason = f"STALE_FRESHNESS_{int(float(freshness_sec))}s"

    if not ok:
        ledger.emit(
            level="WARN",
            event_type="HAWK_STALE_OR_INVALID",
            message=f"Hawk inputs stale/invalid: {reason}",
            evidence={"status": status, "status_file": dataclasses.asdict(evidence["status.json"])},
        )

    hawk = HawkInputs(
        status=status,
        positions=loaded["positions.json"],
        orders=loaded["orders.json"],
        risk=loaded["risk.json"],
        performance=loaded["performance.json"],
    )

    return RuntimeSnapshot(
        runtime_dir=runtime_dir,
        hawk_dir=hawk_dir,
        dragon_dir=dragon_dir,
        created_utc=now,
        hawk=hawk,
        evidence=evidence,
        freshness_ok=ok,
        freshness_reason=reason,
    )


# -------------------------
# Muscles
# -------------------------

@dataclass
class DragonOutputDelta:
    flags: List[Dict[str, Any]]
    digest_blocks: List[Dict[str, Any]]
    actions_next: List[Dict[str, Any]]


def empty_delta() -> DragonOutputDelta:
    return DragonOutputDelta(flags=[], digest_blocks=[], actions_next=[])


class Muscle(Protocol):
    muscle_id: str

    def run(self, snap: RuntimeSnapshot, ledger: Ledger) -> DragonOutputDelta:
        ...


@dataclass(frozen=True)
class FreshnessMuscle:
    muscle_id: str = "freshness_v1"

    def run(self, snap: RuntimeSnapshot, ledger: Ledger) -> DragonOutputDelta:
        flags = [
            {
                "flag_id": "hawk_freshness",
                "severity": "OK" if snap.freshness_ok else "WARN",
                "reason": snap.freshness_reason,
                "evidence": {"status_file": dataclasses.asdict(snap.evidence["status.json"])},
            }
        ]

        blocks: List[Dict[str, Any]] = []
        if not snap.freshness_ok:
            blocks.append(
                {"type": "system", "title": "⚠️ Hawk data stale", "body": f"Dragon inert. Reason: {snap.freshness_reason}"}
            )

        ledger.emit(
            level="INFO",
            event_type="FRESHNESS_CHECK",
            message=f"freshness_ok={snap.freshness_ok} reason={snap.freshness_reason}",
            evidence={"status_file": dataclasses.asdict(snap.evidence["status.json"])},
        )
        return DragonOutputDelta(flags=flags, digest_blocks=blocks, actions_next=[])


@dataclass(frozen=True)
class RiskMuscle:
    muscle_id: str = "risk_governor_v1"

    def run(self, snap: RuntimeSnapshot, ledger: Ledger) -> DragonOutputDelta:
        if not snap.freshness_ok:
            return empty_delta()

        risk = snap.hawk.risk

        def num(x: Any) -> Optional[float]:
            return float(x) if isinstance(x, (int, float)) else None

        dd = num(risk.get("drawdown_pct"))
        exp = num(risk.get("gross_exposure_pct"))
        cash = num(risk.get("cash_pct"))

        flags: List[Dict[str, Any]] = []
        blocks: List[Dict[str, Any]] = []

        if dd is not None and dd >= 8.0:
            flags.append(
                {"flag_id": "drawdown_high", "severity": "WARN", "threshold": 8.0, "value": dd, "policy": "Draft-only."}
            )
            blocks.append({"type": "risk", "title": "🧯 Drawdown elevated", "body": f"Drawdown {dd:.2f}% ≥ 8.00%."})

        if exp is not None and exp >= 85.0:
            flags.append(
                {"flag_id": "exposure_high", "severity": "WARN", "threshold": 85.0, "value": exp, "policy": "Draft-only."}
            )

        if cash is not None and cash <= 10.0:
            flags.append({"flag_id": "cash_low", "severity": "WARN", "threshold": 10.0, "value": cash, "policy": "Draft-only."})

        if flags:
            ledger.emit(
                level="INFO",
                event_type="RISK_FLAGS_RAISED",
                message=f"Raised {len(flags)} risk flags.",
                evidence={"risk_file": dataclasses.asdict(snap.evidence["risk.json"])},
            )

        return DragonOutputDelta(flags=flags, digest_blocks=blocks, actions_next=[])


@dataclass(frozen=True)
class AnomalyMuscle:
    muscle_id: str = "anomaly_v1"

    def run(self, snap: RuntimeSnapshot, ledger: Ledger) -> DragonOutputDelta:
        if not snap.freshness_ok:
            return empty_delta()

        perf = snap.hawk.performance
        positions = snap.hawk.positions

        flags: List[Dict[str, Any]] = []
        blocks: List[Dict[str, Any]] = []

        eq = perf.get("equity")
        eq_prev = perf.get("equity_prev_close")
        if isinstance(eq, (int, float)) and isinstance(eq_prev, (int, float)) and eq_prev > 0:
            delta_pct = ((float(eq) - float(eq_prev)) / float(eq_prev)) * 100.0
            if abs(delta_pct) >= 3.5:
                flags.append(
                    {
                        "flag_id": "equity_jump",
                        "severity": "WARN",
                        "value_pct": round(delta_pct, 2),
                        "threshold_pct": 3.5,
                        "policy": "Draft-only; verify fills/news.",
                    }
                )
                blocks.append({"type": "anomaly", "title": "⚡ Equity moved hard", "body": f"Equity vs prev close: {delta_pct:+.2f}%."})

        pos_list = positions.get("positions")
        if isinstance(pos_list, list) and len(pos_list) >= 12:
            flags.append({"flag_id": "positions_many", "severity": "FYI", "value": len(pos_list), "threshold": 12})

        if flags:
            ledger.emit(
                level="INFO",
                event_type="ANOMALY_FLAGS",
                message=f"Raised {len(flags)} anomaly flags.",
                evidence={
                    "performance_file": dataclasses.asdict(snap.evidence["performance.json"]),
                    "positions_file": dataclasses.asdict(snap.evidence["positions.json"]),
                },
            )

        return DragonOutputDelta(flags=flags, digest_blocks=blocks, actions_next=[])


@dataclass(frozen=True)
class NarrativeMuscle:
    muscle_id: str = "narrative_v1"

    def run(self, snap: RuntimeSnapshot, ledger: Ledger) -> DragonOutputDelta:
        status = snap.hawk.status
        perf = snap.hawk.performance

        mode = "OBSERVER" if snap.freshness_ok else "INERT"

        def fmt_money(x: Any) -> str:
            return f"${float(x):,.2f}" if isinstance(x, (int, float)) else "n/a"

        last_tick = status.get("last_tick_utc", "UNKNOWN")
        eq = fmt_money(perf.get("equity"))
        pnl_day = fmt_money(perf.get("pnl_day"))

        block = {
            "type": "narrative",
            "title": "🐉 Dragon check-in",
            "body": f"Mode: {mode} | Last tick: {last_tick}\nEquity: {eq} | Day P/L: {pnl_day}\nFreshness: {snap.freshness_reason}",
            "constraints": ["evidence-first", "no-hype", "draft-only"],
        }

        ledger.emit(level="INFO", event_type="NARRATIVE_BUILT", message=f"Built narrative mode={mode}", evidence={})
        return DragonOutputDelta(flags=[], digest_blocks=[block], actions_next=[])


DEFAULT_MUSCLES: Tuple[Muscle, ...] = (
    FreshnessMuscle(),
    RiskMuscle(),
    AnomalyMuscle(),
    NarrativeMuscle(),
)


# -------------------------
# Engine
# -------------------------

def merge_deltas(deltas: Sequence[DragonOutputDelta]) -> DragonOutputDelta:
    flags: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    for d in deltas:
        flags.extend(d.flags)
        blocks.extend(d.digest_blocks)
        actions.extend(d.actions_next)
    return DragonOutputDelta(flags=flags, digest_blocks=blocks, actions_next=actions)


def write_outputs(snap: RuntimeSnapshot, delta: DragonOutputDelta, ledger: Ledger, *, mandate: str) -> None:
    heartbeat = {
        "ts_utc": iso_utc(now_utc()),
        "run_id": ledger.run_id,
        "mandate": mandate,
        "mode": "OBSERVER" if snap.freshness_ok else "INERT",
        "freshness_ok": snap.freshness_ok,
        "freshness_reason": snap.freshness_reason,
        "inputs": {k: dataclasses.asdict(v) for k, v in snap.evidence.items()},
    }

    write_json_atomic(snap.dragon_dir / "dragon_heartbeat.json", heartbeat)
    write_json_atomic(snap.dragon_dir / "dragon_flags.json", {"ts_utc": iso_utc(now_utc()), "flags": delta.flags})
    write_json_atomic(snap.dragon_dir / "dragon_digest.json", {"ts_utc": iso_utc(now_utc()), "blocks": delta.digest_blocks})
    write_json_atomic(snap.dragon_dir / "dragon_actions_next.json", {"ts_utc": iso_utc(now_utc()), "actions": delta.actions_next})

    ledger.emit(
        level="INFO",
        event_type="OUTPUTS_WRITTEN",
        message="Wrote heartbeat/flags/digest/actions_next",
        evidence={"dragon_dir": str(snap.dragon_dir)},
    )


def run_once(runtime_dir: Path, *, mandate: str, muscles: Iterable[Muscle] = DEFAULT_MUSCLES) -> int:
    run_id = uuid.uuid4().hex
    ledger = Ledger(path=runtime_dir / "dragon" / "dragon_ledger.jsonl", run_id=run_id, mandate=mandate)

    ledger.emit(level="INFO", event_type="RUN_START", message="Dragon run started", evidence={"runtime": str(runtime_dir)})

    try:
        snap = validate_snapshot(runtime_dir=runtime_dir, ledger=ledger)
    except DragonContractError as e:
        ledger.emit(level="ERROR", event_type="CONTRACT_VIOLATION", message=str(e), evidence={})
        return 2

    ordered = sorted(list(muscles), key=lambda m: getattr(m, "muscle_id", m.__class__.__name__))
    deltas: List[DragonOutputDelta] = []

    for m in ordered:
        mid = getattr(m, "muscle_id", m.__class__.__name__)
        ledger.emit(level="INFO", event_type="MUSCLE_START", message=f"Running {mid}", evidence={})
        try:
            deltas.append(m.run(snap, ledger))
        except Exception as e:
            ledger.emit(level="ERROR", event_type="MUSCLE_ERROR", message=f"{mid} failed: {e}", evidence={})
        ledger.emit(level="INFO", event_type="MUSCLE_END", message=f"Completed {mid}", evidence={})

    merged = merge_deltas(deltas)

    try:
        write_outputs(snap, merged, ledger, mandate=mandate)
    except DragonOutputError as e:
        ledger.emit(level="ERROR", event_type="OUTPUT_WRITE_FAIL", message=str(e), evidence={})
        return 3

    ledger.emit(level="INFO", event_type="RUN_END", message="Dragon run finished", evidence={"run_id": run_id})
    return 0


def observe_loop(runtime_dir: Path, *, once: bool, interval_sec: int, mandate: str) -> int:
    if once:
        return run_once(runtime_dir, mandate=mandate)
    while True:
        _ = run_once(runtime_dir, mandate=mandate)
        time.sleep(max(1, int(interval_sec)))


# -------------------------
# CLI
# -------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dragon-agent", description="Dragon Core agent kernel (deterministic)")
    sub = p.add_subparsers(dest="cmd", required=True)

    obs = sub.add_parser("observe", help="Observe hawk runtime and emit dragon outputs")
    obs.add_argument("--runtime", required=True, help="Runtime folder containing hawk/ and dragon/")
    obs.add_argument("--mandate", default="OBSERVER", help="Mandate string to stamp into ledger + heartbeat")
    obs.add_argument("--once", action="store_true", help="Run one tick and exit")
    obs.add_argument("--loop", action="store_true", help="Run continuously")
    obs.add_argument("--interval", type=int, default=10, help="Loop interval seconds (default 10)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    runtime_dir = Path(args.runtime).expanduser().resolve()

    if args.cmd == "observe":
        if args.once and args.loop:
            print("Choose either --once or --loop, not both.", file=sys.stderr)
            return 2
        once = True if (args.once or not args.loop) else False
        return observe_loop(runtime_dir, once=once, interval_sec=int(args.interval), mandate=str(args.mandate))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())