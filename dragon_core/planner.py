# dragon_core/planner.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import time


@dataclass(frozen=True)
class PlanConfig:
    daily_post_cooldown_sec: int = 24 * 3600
    max_replies_per_run: int = 3


def _extract_user_map(mentions_payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Map author_id -> username from includes.users
    """
    m: Dict[str, str] = {}
    inc = mentions_payload.get("includes") or {}
    users = inc.get("users") or []
    if isinstance(users, list):
        for u in users:
            try:
                uid = str(u.get("id"))
                uname = str(u.get("username"))
                if uid and uname:
                    m[uid] = uname
            except Exception:
                continue
    return m


def _max_id(tweets: List[Dict[str, Any]]) -> Optional[str]:
    # X ids are numeric strings; max lexicographically works if same length, but safest cast to int
    best: Optional[int] = None
    for t in tweets:
        tid = t.get("id")
        if not tid:
            continue
        try:
            n = int(str(tid))
            best = n if best is None else max(best, n)
        except Exception:
            continue
    return str(best) if best is not None else None


def plan_actions(
    runtime_dir: Path,
    cfg: PlanConfig,
    *,
    state: Any,
    mentions_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Returns: (actions, new_since_id)
    """
    now = int(time.time())
    actions: List[Dict[str, Any]] = []

    # Daily posts using state timestamps
    for channel in ("x", "moltbook"):
        last = getattr(state, f"last_daily_post_unix_{channel}", None)
        if last is None or (now - int(last)) >= cfg.daily_post_cooldown_sec:
            actions.append(
                {
                    "action_id": f"daily:{channel}:{now // cfg.daily_post_cooldown_sec}",
                    "channel": channel,
                    "type": "post",
                    "text": "🐉 Dragon online. Deterministic automation, evidence-first. No hype—just disciplined execution and receipts.",
                    "metadata": {"kind": "daily_status"},
                }
            )

    # Mentions replies (X)
    new_since_id: Optional[str] = None
    if mentions_payload:
        tweets = mentions_payload.get("data") or []
        if isinstance(tweets, list) and tweets:
            new_since_id = _max_id(tweets)
            user_map = _extract_user_map(mentions_payload)

            replies = 0
            for t in tweets:
                if replies >= cfg.max_replies_per_run:
                    break
                tid = str(t.get("id") or "")
                if not tid:
                    continue

                author_id = str(t.get("author_id") or "")
                uname = user_map.get(author_id)
                prefix = f"@{uname} " if uname else ""

                # Deterministic reply (safe, questions invite engagement)
                actions.append(
                    {
                        "action_id": f"x_reply:{tid}",
                        "channel": "x",
                        "type": "reply",
                        "in_reply_to": tid,
                        "text": f"{prefix}Appreciate the ping. Dragon here—evidence-only automation, deterministic loops. What are you building?",
                        "metadata": {"kind": "mention_reply"},
                    }
                )
                replies += 1

    return actions, new_since_id