# dragon_core/state.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import json
import os
import time


def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    _safe_mkdir(path.parent)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class DragonState:
    """
    Stored at: runtime/dragon/state.json
    """
    x_user_id: Optional[str] = None
    x_since_id: Optional[str] = None
    last_daily_post_unix_x: Optional[int] = None
    last_daily_post_unix_moltbook: Optional[int] = None
    last_run_unix: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x_user_id": self.x_user_id,
            "x_since_id": self.x_since_id,
            "last_daily_post_unix_x": self.last_daily_post_unix_x,
            "last_daily_post_unix_moltbook": self.last_daily_post_unix_moltbook,
            "last_run_unix": self.last_run_unix,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DragonState":
        return DragonState(
            x_user_id=d.get("x_user_id"),
            x_since_id=d.get("x_since_id"),
            last_daily_post_unix_x=d.get("last_daily_post_unix_x"),
            last_daily_post_unix_moltbook=d.get("last_daily_post_unix_moltbook"),
            last_run_unix=d.get("last_run_unix"),
        )


def load_state(runtime_dir: Path) -> DragonState:
    p = runtime_dir / "dragon" / "state.json"
    if not p.exists():
        return DragonState()
    try:
        return DragonState.from_dict(_read_json(p))
    except Exception:
        return DragonState()


def save_state(runtime_dir: Path, state: DragonState) -> None:
    p = runtime_dir / "dragon" / "state.json"
    _write_json_atomic(p, state.to_dict())


def touch_run(state: DragonState) -> None:
    state.last_run_unix = int(time.time())