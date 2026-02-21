# dragon_core/state.py
"""
Persistent state surface (runtime/dragon/state.json).

Keeps:
- x_mentions_since_id (so we only reply to new mentions)
- last_daily_post_by_channel (so daily cadence is deterministic)
- counters (optional)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import json
import os
import time


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class DragonStateStore:
    runtime_dir: Path

    @property
    def path(self) -> Path:
        return self.runtime_dir / "dragon" / "state.json"

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {
                "ts_unix": int(time.time()),
                "x_mentions_since_id": None,
                "last_daily_post_by_channel": {"x": None, "moltbook": None},
            }
        try:
            return _read_json(self.path)
        except Exception:
            # fail-closed: reset to safe defaults if corrupted
            return {
                "ts_unix": int(time.time()),
                "x_mentions_since_id": None,
                "last_daily_post_by_channel": {"x": None, "moltbook": None},
            }

    def save(self, state: Dict[str, Any]) -> None:
        s = dict(state)
        s["ts_unix"] = int(time.time())
        _write_json_atomic(self.path, s)