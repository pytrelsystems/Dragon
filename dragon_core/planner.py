# dragon_core/planner.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import time


@dataclass(frozen=True)
class PlanConfig:
    daily_post_cooldown_sec: int = 24 * 3600
    max_mention_replies_per_run: int = 3

    # initiation limits
    max_initiate_replies_per_run: int = 2
    min_followers_to_reply: int = 25  # avoid brand-new spammy accounts
    max_replies_per_author_per_run: int = 1


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_user_map(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Map author_id -> user object from includes.users
    """
    m: Dict[str, Dict[str, Any]] = {}
    inc = payload.get("includes") or {}
    users = inc.get("users") or []
    if isinstance(users, list):
        for u in users:
            uid = str(u.get("id") or "")
            if uid:
                m[uid] = u
    return m


def _max_id(items: List[Dict[str, Any]]) -> Optional[str]:
    best: Optional[int] = None
    for t in items:
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
        "Quiet progress > loud promises. Cleaner loops, fewer failure modes. {snip}",
        "Automation that respects reality: rate limits, state, fail-closed. {snip}",
        "Dragon’s job: make the system boring, reliable, undeniable. {snip}",
        "No vibes. Just contracts + ledgers + execution discipline. {snip}",
    ]


def _pick_template(channel: str, day_index: int) -> str:
    templates = _daily_templates()
    offset = 0 if channel == "x" else 3
    return templates[(day_index + offset) % len(templates)]


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


def _reply_body(intent: str, *, initiate: bool) -> str:
    """
    Initiate replies should be extra polite + low-friction.
    """
    if initiate:
        if intent == "agents":
            return "Saw this—solid. I build deterministic agents (contracts + receipts). What’s your agent actually allowed to *do*?"
        if intent == "dev":
            return "This is the kind of build I respect. Where are you drawing the line between state, policy, and execution?"
        if intent == "ops":
            return "Love the ops angle. What’s the one control you wish you could enforce automatically without drama?"
        if intent == "security":
            return "Good take. Are you using OAuth user-context tokens, or service keys + delegation?"
        if intent == "construction":
            return "This hits. Are you closer to estimating, submittals, or field QA automation?"
        return "Respect. I’m building deterministic automation with receipts (no hype). What are you shipping right now?"

    # reactive (mentions)
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


def plan_actions(
    runtime_dir: Path,
    cfg: PlanConfig,
    *,
    state: Any,
    mentions_payload: Optional[Dict[str, Any]] = None,
    searches: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str], Dict[str, Optional[str]]]:
    """
    Returns:
      actions,
      new_mentions_since_id,
      new_search_since_ids (per label)
    """
    now = int(time.time())
    day_index = now // cfg.daily_post_cooldown_sec
    snip = _safe_status_snippet(runtime_dir)

    actions: List[Dict[str, Any]] = []

    # Daily rotating posts
    for channel in ("x", "moltbook"):
        last = getattr(state, f"last_daily_post_unix_{channel}", None)
        if last is None or (now - int(last)) >= cfg.daily_post_cooldown_sec:
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

    # Reply to mentions (reactive)
    new_mentions_since_id: Optional[str] = None
    if mentions_payload:
        tweets = mentions_payload.get("data") or []
        if isinstance(tweets, list) and tweets:
            new_mentions_since_id = _max_id(tweets)
            users = _extract_user_map(mentions_payload)

            replies = 0
            for t in tweets:
                if replies >= cfg.max_mention_replies_per_run:
                    break
                tid = str(t.get("id") or "")
                if not tid:
                    continue
                author_id = str(t.get("author_id") or "")
                uname = str((users.get(author_id) or {}).get("username") or "")
                prefix = f"@{uname} " if uname else ""

                text = str(t.get("text") or "")
                intent = _classify_intent(text)
                body = _reply_body(intent, initiate=False)

                actions.append(
                    {
                        "action_id": f"x_mention_reply:{tid}",
                        "channel": "x",
                        "type": "reply",
                        "in_reply_to": tid,
                        "text": f"{prefix}{body}",
                        "metadata": {"kind": "mention_reply", "intent": intent},
                    }
                )
                replies += 1

    # Initiate: recent search replies
    new_search_since_ids: Dict[str, Optional[str]] = {}
    if searches:
        replied = getattr(state, "replied_tweet_ids", {}) or {}
        per_author: Dict[str, int] = {}

        for label, payload in searches.items():
            tweets = payload.get("data") or []
            users = _extract_user_map(payload)
            new_search_since_ids[label] = _max_id(tweets) if isinstance(tweets, list) and tweets else None

            if not isinstance(tweets, list) or not tweets:
                continue

            initiated = 0
            for t in tweets:
                if initiated >= cfg.max_initiate_replies_per_run:
                    break

                tid = str(t.get("id") or "")
                if not tid:
                    continue
                if tid in replied:
                    continue

                author_id = str(t.get("author_id") or "")
                if not author_id:
                    continue
                per_author.setdefault(author_id, 0)
                if per_author[author_id] >= cfg.max_replies_per_author_per_run:
                    continue

                u = users.get(author_id) or {}
                metrics = u.get("public_metrics") or {}
                followers = metrics.get("followers_count")
                if isinstance(followers, int) and followers < cfg.min_followers_to_reply:
                    continue

                uname = str(u.get("username") or "")
                prefix = f"@{uname} " if uname else ""

                text = str(t.get("text") or "")
                intent = _classify_intent(text)
                body = _reply_body(intent, initiate=True)

                actions.append(
                    {
                        "action_id": f"x_initiate_reply:{label}:{tid}",
                        "channel": "x",
                        "type": "reply",
                        "in_reply_to": tid,
                        "text": f"{prefix}{body}",
                        "metadata": {"kind": "initiate_reply", "intent": intent, "search_label": label},
                    }
                )

                per_author[author_id] += 1
                replied[tid] = now  # reserve immediately to avoid double-scheduling
                initiated += 1

        # write back (agent will persist full state)
        state.replied_tweet_ids = replied

    return actions, new_mentions_since_id, new_search_since_ids