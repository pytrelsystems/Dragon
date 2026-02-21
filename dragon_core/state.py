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

    convo_reply_memory:
      {
        "<conversation_id>": {"count_24h": int, "last_reply_unix": int}
      }

    This memory is pruned opportunistically on save.
    """
    x_user_id: Optional[str] = None
    x_since_id: Optional[str] = None
    last_daily_post_unix_x: Optional[int] = None
    last_daily_post_unix_moltbook: Optional[int] = None
    last_run_unix: Optional[int] = None
    convo_reply_memory: Optional[Dict[str, Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x_user_id": self.x_user_id,
            "x_since_id": self.x_since_id,
            "last_daily_post_unix_x": self.last_daily_post_unix_x,
            "last_daily_post_unix_moltbook": self.last_daily_post_unix_moltbook,
            "last_run_unix": self.last_run_unix,
            "convo_reply_memory": self.convo_reply_memory or {},
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DragonState":
        return DragonState(
            x_user_id=d.get("x_user_id"),
            x_since_id=d.get("x_since_id"),
            last_daily_post_unix_x=d.get("last_daily_post_unix_x"),
            last_daily_post_unix_moltbook=d.get("last_daily_post_unix_moltbook"),
            last_run_unix=d.get("last_run_unix"),
            convo_reply_memory=d.get("convo_reply_memory") or {},
        )


def load_state(runtime_dir: Path) -> DragonState:
    p = runtime_dir / "dragon" / "state.json"
    if not p.exists():
        return DragonState(convo_reply_memory={})
    try:
        st = DragonState.from_dict(_read_json(p))
        if st.convo_reply_memory is None:
            st.convo_reply_memory = {}
        return st
    except Exception:
        return DragonState(convo_reply_memory={})


def touch_run(state: DragonState) -> None:
    state.last_run_unix = int(time.time())


def _prune_convo_memory(mem: Dict[str, Dict[str, Any]], *, ttl_sec: int = 48 * 3600) -> Dict[str, Dict[str, Any]]:
    """
    Keep memory small. Drop convos idle for > ttl_sec.
    """
    now = int(time.time())
    out: Dict[str, Dict[str, Any]] = {}
    for cid, info in mem.items():
        try:
            last = int(info.get("last_reply_unix") or 0)
            if last and (now - last) <= ttl_sec:
                out[str(cid)] = {"count_24h": int(info.get("count_24h") or 0), "last_reply_unix": last}
        except Exception:
            continue
    return out


def save_state(runtime_dir: Path, state: DragonState) -> None:
    p = runtime_dir / "dragon" / "state.json"
    mem = state.convo_reply_memory or {}
    state.convo_reply_memory = _prune_convo_memory(mem)
    _write_json_atomic(p, state.to_dict())