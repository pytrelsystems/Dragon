# dragon_core/planner.py
"""
Engagement planner (NO dragon_actions_next file).

Dragon decides actions internally:
- Max 1 daily status post per channel per 24h
- Reply to X mentions (dedupe)
- Emits actions as structured dicts for the engager to enqueue/execute
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import json
import time


@dataclass(frozen=True)
class PlanConfig:
    daily_post_cooldown_sec: int = 24 * 3600
    max_replies_per_run: int = 3


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sent_markers(sent_dir: Path) -> Set[str]:
    markers: Set[str] = set()
    if not sent_dir.exists():
        return markers
    for p in sent_dir.glob("*.json"):
        try:
            doc = _read_json(p)
            if isinstance(doc.get("executed_unix"), int):
                # Track daily posts per channel
                if doc.get("type") == "post" and doc.get("channel"):
                    markers.add(f"posted:{doc['channel']}:{doc['executed_unix']}")
            # Track replied-to targets
            target = str(doc.get("in_reply_to") or "")
            if target:
                markers.add(f"replied:{target}")
        except Exception:
            continue
    return markers


def _last_post_ts(sent_dir: Path, channel: str) -> Optional[int]:
    if not sent_dir.exists():
        return None
    newest: Optional[int] = None
    for p in sent_dir.glob("*.json"):
        try:
            doc = _read_json(p)
            if doc.get("channel") != channel:
                continue
            if doc.get("type") != "post":
                continue
            ts = doc.get("executed_unix")
            if isinstance(ts, int):
                newest = ts if newest is None else max(newest, ts)
        except Exception:
            continue
    return newest


def plan_actions(runtime_dir: Path, cfg: PlanConfig, *, x_mentions: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    sent_dir = runtime_dir / "dragon" / "sent"
    sent = _sent_markers(sent_dir)
    now = int(time.time())

    actions: List[Dict[str, Any]] = []

    # Daily status post (X + Moltbook)
    for channel in ("x", "moltbook"):
        last = _last_post_ts(sent_dir, channel)
        if last is None or (now - last) >= cfg.daily_post_cooldown_sec:
            actions.append(
                {
                    "action_id": f"daily:{channel}:{now // cfg.daily_post_cooldown_sec}",
                    "channel": channel,
                    "type": "post",
                    "text": "🐉 Dragon online. Deterministic automation, evidence-first. No hype—just disciplined execution and receipts.",
                    "metadata": {"kind": "daily_status"},
                }
            )

    # Replies to X mentions (deduped)
    if x_mentions:
        replies = 0
        for m in x_mentions:
            mid = str(m.get("id") or "")
            if not mid:
                continue
            if f"replied:{mid}" in sent:
                continue
            if replies >= cfg.max_replies_per_run:
                break

            actions.append(
                {
                    "action_id": f"x_reply:{mid}",
                    "channel": "x",
                    "type": "reply",
                    "in_reply_to": mid,
                    "text": "👋 Appreciate the ping. Dragon here—deterministic, evidence-only automation. What are you building?",
                    "metadata": {"kind": "mention_reply"},
                }
            )
            replies += 1

    return actions