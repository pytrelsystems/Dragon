# dragon_core/storage.py
"""
File-based queue + receipts.

Dragon engages by writing/reading JSON artifacts. No hidden state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import os
import time
import uuid


@dataclass(frozen=True)
class QueuePaths:
    runtime_dir: Path

    @property
    def dragon_dir(self) -> Path:
        return self.runtime_dir / "dragon"

    @property
    def outbox_dir(self) -> Path:
        return self.dragon_dir / "outbox"

    @property
    def sent_dir(self) -> Path:
        return self.dragon_dir / "sent"

    @property
    def dead_dir(self) -> Path:
        return self.dragon_dir / "dead"

    @property
    def actions_next_path(self) -> Path:
        return self.dragon_dir / "dragon_actions_next.json"


def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    _safe_mkdir(path.parent)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def enqueue_actions(paths: QueuePaths, actions: List[Dict[str, Any]]) -> List[Path]:
    """
    Convert actions into per-job outbox files.
    Idempotency: caller should include a stable action_id; if absent we assign one.
    """
    _safe_mkdir(paths.outbox_dir)
    created: List[Path] = []

    for a in actions:
        action_id = str(a.get("action_id") or uuid.uuid4().hex)
        a = dict(a)
        a["action_id"] = action_id
        a.setdefault("created_unix", int(time.time()))

        job_path = paths.outbox_dir / f"{action_id}.json"
        if job_path.exists():
            continue

        write_json_atomic(job_path, a)
        created.append(job_path)

    return created


def list_outbox(paths: QueuePaths, limit: int = 20) -> List[Path]:
    _safe_mkdir(paths.outbox_dir)
    jobs = sorted(paths.outbox_dir.glob("*.json"), key=lambda p: p.name)
    return jobs[: max(1, int(limit))]


def move_job(src: Path, dst_dir: Path) -> Path:
    _safe_mkdir(dst_dir)
    dst = dst_dir / src.name
    os.replace(src, dst)
    return dst