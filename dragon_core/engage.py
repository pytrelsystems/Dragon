# dragon_core/engage.py
"""
Dragon Engage (industrial-safe)

- Executes social actions via outbox queue:
    runtime/dragon/outbox/*.json  -> execute -> runtime/dragon/sent/*.json
                                  blocked -> runtime/dragon/dead/*.json

- Supports channels:
    - moltbook (via MoltbookClient)
    - x       (via XClient)

- Safety:
    - policy gate for every action (before enqueue + before execute)
    - rate limit cooldown between sends
    - idempotency via action_id filename
    - fail-open on transient execution errors: job stays in outbox for retry
    - fail-closed on policy violations: move to dead
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

from .policy import validate_action
from .storage import QueuePaths, enqueue, list_outbox, move, read_json, write_json_atomic
from .moltbook_client import MoltbookClient
from .x_client import XClient


@dataclass(frozen=True)
class EngageConfig:
    # max jobs executed per run tick
    max_per_run: int = 4
    # seconds between posts/replies to prevent spam + rate-limits
    cooldown_sec: int = 30
    # if True, only execute when hawk freshness_ok == True (recommended)
    require_freshness_ok: bool = True


class DragonEngager:
    """
    Executes actions safely.

    Expected ledger interface:
      ledger.info(event_type, message, evidence_dict)
      ledger.warn(event_type, message, evidence_dict)
      ledger.error(event_type, message, evidence_dict)

    If ledger is missing these methods, we degrade to print.
    """

    def __init__(self, runtime_dir: Path, ledger: Any, cfg: EngageConfig) -> None:
        self.paths = QueuePaths(runtime_dir=runtime_dir)
        self.ledger = ledger
        self.cfg = cfg

    # -------------------------
    # Public API
    # -------------------------

    def enqueue_actions(self, actions: List[Dict[str, Any]]) -> None:
        """Policy-gate then enqueue actions into outbox."""
        allowed: List[Dict[str, Any]] = []
        blocked = 0

        for a in actions:
            if not isinstance(a, dict):
                blocked += 1
                continue
            ok, reasons, norm = validate_action(a)
            if not ok:
                blocked += 1
                self._warn("ENGAGE_ACTION_BLOCKED", "policy_block", {"reasons": reasons, "action": norm})
                continue
            allowed.append(norm)

        created = enqueue(self.paths, allowed)
        self._info(
            "ENGAGE_ENQUEUED",
            f"enqueued={len(created)} blocked={blocked}",
            {"created": [str(x) for x in created]},
        )

    def execute_outbox(self, *, freshness_ok: bool = True) -> int:
        """
        Execute queued jobs. Respects cfg.require_freshness_ok.
        Returns 0 always unless a hard unexpected exception escapes.
        """
        if self.cfg.require_freshness_ok and not freshness_ok:
            self._warn(
                "ENGAGE_SKIPPED_STALE",
                "freshness gate blocked engagement",
                {"require_freshness_ok": True, "freshness_ok": freshness_ok},
            )
            return 0

        jobs = list_outbox(self.paths, limit=self.cfg.max_per_run)
        if not jobs:
            return 0

        x_client: Optional[XClient] = None
        mb_client: Optional[MoltbookClient] = None

        executed = 0

        for job_path in jobs:
            try:
                job = read_json(job_path)

                # Second policy gate at execution time (defense-in-depth)
                ok, reasons, norm = validate_action(job)
                if not ok:
                    self._warn("ENGAGE_JOB_BLOCKED", "policy_block", {"reasons": reasons, "job": norm})
                    move(job_path, self.paths.dead_dir)
                    continue

                if executed > 0:
                    time.sleep(max(1, int(self.cfg.cooldown_sec)))

                channel = norm["channel"]
                action_type = norm["type"]
                text = norm["text"]

                receipt: Dict[str, Any]

                if channel == "x":
                    x_client = x_client or XClient.from_env()
                    if action_type == "post":
                        receipt = x_client.post(text)
                    else:
                        receipt = x_client.reply(norm["in_reply_to"], text)

                elif channel == "moltbook":
                    mb_client = mb_client or MoltbookClient.from_env()
                    if action_type == "post":
                        receipt = mb_client.create_post(text)
                    else:
                        receipt = mb_client.reply(norm["in_reply_to"], text)
                else:
                    # Should never happen because policy gate blocks invalid_channel
                    self._error("ENGAGE_EXEC_FAIL", "unknown_channel", {"channel": channel, "job": norm})
                    move(job_path, self.paths.dead_dir)
                    continue

                # Record receipt + move to sent
                norm["receipt"] = receipt
                norm["executed_unix"] = int(time.time())

                write_json_atomic(self.paths.sent_dir / job_path.name, norm)
                move(job_path, self.paths.sent_dir)

                executed += 1
                self._info(
                    "ENGAGE_EXECUTED",
                    f"{channel}:{action_type}",
                    {"action_id": norm.get("action_id"), "job_file": str(job_path.name)},
                )

            except Exception as e:
                # Fail-open: leave job in outbox for retry; log error
                self._error("ENGAGE_EXEC_FAIL", str(e), {"job": str(job_path)})

        return 0

    # -------------------------
    # Logging helpers
    # -------------------------

    def _info(self, event_type: str, message: str, evidence: Dict[str, Any]) -> None:
        fn = getattr(self.ledger, "info", None)
        if callable(fn):
            fn(event_type, message, evidence)
        else:
            print(f"[INFO] {event_type}: {message} :: {evidence}")

    def _warn(self, event_type: str, message: str, evidence: Dict[str, Any]) -> None:
        fn = getattr(self.ledger, "warn", None)
        if callable(fn):
            fn(event_type, message, evidence)
        else:
            print(f"[WARN] {event_type}: {message} :: {evidence}")

    def _error(self, event_type: str, message: str, evidence: Dict[str, Any]) -> None:
        fn = getattr(self.ledger, "error", None)
        if callable(fn):
            fn(event_type, message, evidence)
        else:
            print(f"[ERROR] {event_type}: {message} :: {evidence}")