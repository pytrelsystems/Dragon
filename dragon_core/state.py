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
    x_user_id: Optional[str] = None
    x_since_id: Optional[str] = None  # mentions cursor

    # recent-search cursors keyed by label
    # ex: {"builders_ai": "1890000000000000000", "python_agents": "..."}
    x_search_since_ids: Optional[Dict[str, str]] = None

    # dedupe replies by tweet id with TTL
    # ex: {"189...": 1739999999}
    replied_tweet_ids: Optional[Dict[str, int]] = None

    last_daily_post_unix_x: Optional[int] = None
    last_daily_post_unix_moltbook: Optional[int] = None
    last_run_unix: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x_user_id": self.x_user_id,
            "x_since_id": self.x_since_id,
            "x_search_since_ids": self.x_search_since_ids or {},
            "replied_tweet_ids": self.replied_tweet_ids or {},
            "last_daily_post_unix_x": self.last_daily_post_unix_x,
            "last_daily_post_unix_moltbook": self.last_daily_post_unix_moltbook,
            "last_run_unix": self.last_run_unix,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "DragonState":
        return DragonState(
            x_user_id=d.get("x_user_id"),
            x_since_id=d.get("x_since_id"),
            x_search_since_ids=d.get("x_search_since_ids") or {},
            replied_tweet_ids=d.get("replied_tweet_ids") or {},
            last_daily_post_unix_x=d.get("last_daily_post_unix_x"),
            last_daily_post_unix_moltbook=d.get("last_daily_post_unix_moltbook"),
            last_run_unix=d.get("last_run_unix"),
        )


def load_state(runtime_dir: Path) -> DragonState:
    p = runtime_dir / "dragon" / "state.json"
    if not p.exists():
        return DragonState(x_search_since_ids={}, replied_tweet_ids={})
    try:
        st = DragonState.from_dict(_read_json(p))
        st.x_search_since_ids = st.x_search_since_ids or {}
        st.replied_tweet_ids = st.replied_tweet_ids or {}
        return st
    except Exception:
        return DragonState(x_search_since_ids={}, replied_tweet_ids={})


def touch_run(state: DragonState) -> None:
    state.last_run_unix = int(time.time())


def _prune_replied_ids(replied: Dict[str, int], *, ttl_sec: int = 7 * 24 * 3600) -> Dict[str, int]:
    now = int(time.time())
    out: Dict[str, int] = {}
    for tid, ts in replied.items():
        try:
            t = int(ts)
            if (now - t) <= ttl_sec:
                out[str(tid)] = t
        except Exception:
            continue
    return out


def save_state(runtime_dir: Path, state: DragonState) -> None:
    p = runtime_dir / "dragon" / "state.json"
    state.x_search_since_ids = state.x_search_since_ids or {}
    state.replied_tweet_ids = _prune_replied_ids(state.replied_tweet_ids or {})
    _write_json_atomic(p, state.to_dict())