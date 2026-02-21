# dragon_core/engage.py
"""
Engagement runtime: consume actions_next -> outbox -> execute -> receipts.

This module does NOT decide WHAT to say.
It only enforces policy + executes permitted actions deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

from .ledger import Ledger
from .policy import validate_action
from .storage import QueuePaths, enqueue_actions, list_outbox, move_job, read_json, write_json_atomic
from .moltbook_client import MoltbookClient


@dataclass(frozen=True)
class EngageConfig:
    max_per_run: int = 3
    cooldown_sec: int = 45
    # add per-channel cooldowns later if needed


class DragonEngager:
    def __init__(self, runtime_dir: Path, ledger: Ledger, cfg: EngageConfig) -> None:
        self.paths = QueuePaths(runtime_dir=runtime_dir)
        self.ledger = ledger
        self.cfg = cfg

    def run_once(self) -> int:
        """
        1) Read dragon_actions_next.json (if present)
        2) Validate + enqueue to outbox
        3) Execute up to max_per_run with cooldown + receipts
        """
        self._ingest_actions_next()
        return self._execute_outbox()

    def _ingest_actions_next(self) -> None:
        p = self.paths.actions_next_path
        if not p.exists():
            return

        try:
            doc = read_json(p)
        except Exception as e:
            self.ledger.error("ENGAGE_ACTIONS_NEXT_INVALID", str(e), {"path": str(p)})
            return

        actions = doc.get("actions") or []
        if not isinstance(actions, list) or not actions:
            return

        allowed: List[Dict[str, Any]] = []
        blocked = 0

        for a in actions:
            if not isinstance(a, dict):
                blocked += 1
                continue
            ok, reasons, norm = validate_action(a)
            if not ok:
                blocked += 1
                self.ledger.warn("ENGAGE_ACTION_BLOCKED", "policy_block", {"reasons": reasons, "action": norm})
                continue
            allowed.append(norm)

        created = enqueue_actions(self.paths, allowed)
        self.ledger.info(
            "ENGAGE_ENQUEUED",
            f"enqueued={len(created)} blocked={blocked}",
            {"created": [str(x) for x in created]},
        )

        # Optional: clear actions_next after ingest to prevent re-enqueue spam
        write_json_atomic(p, {"ts_utc": doc.get("ts_utc"), "actions": []})

    def _execute_outbox(self) -> int:
        jobs = list_outbox(self.paths, limit=self.cfg.max_per_run)
        if not jobs:
            return 0

        client = None
        executed = 0

        for job_path in jobs:
            try:
                job = read_json(job_path)
                ok, reasons, norm = validate_action(job)
                if not ok:
                    self.ledger.warn("ENGAGE_JOB_BLOCKED", "policy_block", {"reasons": reasons, "job": norm})
                    move_job(job_path, self.paths.dead_dir)
                    continue

                channel = norm["channel"]
                action_type = norm["type"]
                text = norm["text"]

                # Cooldown between executions (spam guard)
                if executed > 0:
                    time.sleep(self.cfg.cooldown_sec)

                if channel == "moltbook":
                    if client is None:
                        client = MoltbookClient.from_env()
                    receipt = self._exec_moltbook(client, action_type, norm)
                else:
                    # X executor intentionally not implemented here until you wire API.
                    raise RuntimeError("X executor not configured")

                # Receipt
                norm["receipt"] = receipt
                norm["executed_unix"] = int(time.time())
                write_json_atomic(self.paths.sent_dir / job_path.name, norm)
                move_job(job_path, self.paths.sent_dir)

                executed += 1
                self.ledger.info("ENGAGE_EXECUTED", f"{channel}:{action_type}", {"action_id": norm.get("action_id")})
            except Exception as e:
                self.ledger.error("ENGAGE_EXEC_FAIL", str(e), {"job": str(job_path)})
                # leave job in outbox for retry; avoids silent drops

        return 0

    def _exec_moltbook(self, client: MoltbookClient, action_type: str, action: Dict[str, Any]) -> Dict[str, Any]:
        if action_type == "post":
            return client.create_post(action["text"])
        if action_type == "reply":
            return client.reply(action["in_reply_to"], action["text"])
        raise RuntimeError(f"Unknown action_type: {action_type}")