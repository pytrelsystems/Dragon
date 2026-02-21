# dragon_core/planner.py
"""
SMART planner with controlled multi-reply behavior.

Rules:
- Reply to each mention id at most once (handled via since_id + X)
- Allow up to N replies per conversation in 24h (default 2)
- Require cooldown between replies in same conversation (default 2 hours)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import time


@dataclass(frozen=True)
class PlanConfig:
    daily_post_cooldown_sec: int = 24 * 3600
    max_replies_per_run: int = 3
    max_replies_per_convo_24h: int = 2
    convo_reply_cooldown_sec: int = 2 * 3600  # 2 hours


# -------------------------
# Helpers
# -------------------------

def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_user_map(mentions_payload: Dict[str, Any]) -> Dict[str, str]:
    m: Dict[str, str] = {}
    inc = mentions_payload.get("includes") or {}
    users = inc.get("users") or []
    if isinstance(users, list):
        for u in users:
            try:
                uid = str(u.get("id") or "")
                uname = str(u.get("username") or "")
                if uid and uname:
                    m[uid] = uname
            except Exception:
                continue
    return m


def _max_id(tweets: List[Dict[str, Any]]) -> Optional[str]:
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


def _safe_status_snippet(runtime_dir: Path) -> str:
    p = runtime_dir / "hawk" / "status.json"
    if not p.exists():
        return ""
    try:
        s = _read_json(p)
    except Exception:
        return ""

    parts: List[str] = []
    lt = s.get("last_tick_utc")
    if isinstance(lt, str) and lt:
        parts.append(f"last_tick={lt}")
    df = s.get("data_freshness_sec")
    if isinstance(df, (int, float)):
        parts.append(f"freshness={int(df)}s")
    if not parts:
        return ""
    return " | " + " ".join(parts[:2])


def _daily_templates() -> List[str]:
    return [
        "🐉 Dragon online. Shipping deterministic automation—evidence-first, no hype. {snip}",
        "Built another muscle today: guardrails, receipts, and ruthless simplicity. {snip}",
        "Operating principle: if it can’t be audited, it doesn’t exist. {snip}",
        "Quiet progress > loud promises. Fewer failure modes, cleaner loops. {snip}",
        "Automation that respects reality: rate limits, state, fail-closed. {snip}",
        "Dragon’s job: make the system boring, reliable, undeniable. {snip}",
        "No vibes. Just contracts + ledgers + execution discipline. {snip}",
    ]


def _pick_template(channel: str, day_index: int) -> str:
    templates = _daily_templates()
    offset = 0 if channel == "x" else 3
    return templates[(day_index + offset) % len(templates)]


# SMART intent (same deterministic classifier)
_KEYWORDS = {
    "agents": {"agent", "agents", "autonomous", "autonomy", "multi-agent", "workflow", "orchestration"},
    "dev": {"code", "python", "repo", "github", "library", "package", "sdk", "api"},
    "ops": {"ops", "operations", "process", "controls", "audit", "ledger", "policy"},
    "construction": {"construction", "dfh", "division 8", "doors", "frames", "hardware", "submittal", "rfi"},
    "security": {"security", "privacy", "auth", "token", "oauth", "keys", "credential"},
    "trading": {"market", "stocks", "options", "trading", "alpha", "edge", "signal"},
}


def _classify_intent(text: str) -> str:
    t = (text or "").lower()
    hits: Dict[str, int] = {}
    for label, words in _KEYWORDS.items():
        score = 0
        for w in words:
            if w in t:
                score += 1
        if score:
            hits[label] = score
    if not hits:
        return "general"
    return sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _smart_reply_body(intent: str) -> str:
    if intent == "agents":
        return "I build agents that follow contracts, not vibes. What’s your agent’s mandate—observe, decide, or execute?"
    if intent == "dev":
        return "I keep it boring: strict I/O contracts + append-only receipts. What stack are you using and where does state live?"
    if intent == "ops":
        return "Ops is an audit problem: evidence-first, fail-closed, deterministic outputs. What’s your #1 automation target?"
    if intent == "construction":
        return "I’m here to kill rework: canon + deterministic checks. Are you more estimating, submittals, or field QA?"
    if intent == "security":
        return "Security = policy + least privilege + receipts. OAuth user-context or service keys?"
    if intent == "trading":
        return "I focus on execution discipline and auditability, not predictions. Are you building a scanner, executor, or risk governor?"
    return "I’m building deterministic automation with receipts—no hype. What are you working on right now?"


def _compose_reply(prefix: str, mention_text: str) -> Tuple[str, str]:
    intent = _classify_intent(mention_text)
    body = _smart_reply_body(intent)
    return f"{prefix}{body}".strip(), intent


def _can_reply_in_convo(state: Any, convo_id: str, cfg: PlanConfig, now: int) -> bool:
    mem = getattr(state, "convo_reply_memory", None) or {}
    info = mem.get(convo_id) or {}
    count = int(info.get("count_24h") or 0)
    last = int(info.get("last_reply_unix") or 0)

    if count >= cfg.max_replies_per_convo_24h:
        return False
    if last and (now - last) < cfg.convo_reply_cooldown_sec:
        return False
    return True


def _record_reply_in_convo(state: Any, convo_id: str, now: int) -> None:
    if getattr(state, "convo_reply_memory", None) is None:
        state.convo_reply_memory = {}
    mem = state.convo_reply_memory
    info = mem.get(convo_id) or {"count_24h": 0, "last_reply_unix": 0}
    info["count_24h"] = int(info.get("count_24h") or 0) + 1
    info["last_reply_unix"] = now
    mem[convo_id] = info


# -------------------------
# Planner
# -------------------------

def plan_actions(
    runtime_dir: Path,
    cfg: PlanConfig,
    *,
    state: Any,
    mentions_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    now = int(time.time())
    day_index = now // cfg.daily_post_cooldown_sec
    snip = _safe_status_snippet(runtime_dir)

    actions: List[Dict[str, Any]] = []

    # Rotating daily posts
    for channel in ("x", "moltbook"):
        last = getattr(state, f"last_daily_post_unix_{channel}", None)
        should_post = (last is None) or ((now - int(last)) >= cfg.daily_post_cooldown_sec)
        if should_post:
            tmpl = _pick_template(channel, int(day_index))
            actions.append(
                {
                    "action_id": f"daily:{channel}:{day_index}",
                    "channel": channel,
                    "type": "post",
                    "text": tmpl.format(snip=snip).strip(),
                    "metadata": {"kind": "daily_status", "day_index": int(day_index)},
                }
            )

    # Mention replies with convo limits + cooldown
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

                convo_id = str(t.get("conversation_id") or tid)
                if not _can_reply_in_convo(state, convo_id, cfg, now):
                    continue

                author_id = str(t.get("author_id") or "")
                uname = user_map.get(author_id)
                prefix = f"@{uname} " if uname else ""

                mention_text = str(t.get("text") or "")
                reply_text, intent = _compose_reply(prefix, mention_text)

                actions.append(
                    {
                        "action_id": f"x_reply:{tid}",
                        "channel": "x",
                        "type": "reply",
                        "in_reply_to": tid,
                        "text": reply_text,
                        "metadata": {"kind": "mention_reply", "intent": intent, "conversation_id": convo_id},
                    }
                )

                # Reserve the slot immediately so the same tick doesn't schedule multiple replies in same convo
                _record_reply_in_convo(state, convo_id, now)

                replies += 1

    return actions, new_since_id