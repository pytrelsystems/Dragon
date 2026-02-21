# dragon_core/planner.py
"""
Engagement planner (NO dragon_actions_next file).

Outputs:
  (actions, new_since_id)

Features:
- Rotating daily post templates (7-day cycle, per channel)
- Evidence-backed snippet from runtime/hawk/status.json if present
- SMART mention replies:
    - deterministic keyword/intent classification
    - one tailored question + one crisp statement
    - @username prefix when available
- Safe: no P/L flex, no promises, no predictions
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


# -------------------------
# Helpers
# -------------------------

def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_user_map(mentions_payload: Dict[str, Any]) -> Dict[str, str]:
    """Map author_id -> username from includes.users"""
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
    """
    Pulls a tiny evidence-backed snippet from hawk/status.json if present.
    Never assumes keys beyond basic existence checks.
    """
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

    mode = s.get("mode")
    if isinstance(mode, str) and mode:
        parts.append(f"mode={mode}")

    if not parts:
        return ""
    return " | " + " ".join(parts[:3])


def _daily_templates() -> List[str]:
    return [
        "🐉 Dragon online. Shipping deterministic automation—evidence-first, no hype. {snip}",
        "Built another muscle today: guardrails, receipts, and ruthless simplicity. {snip}",
        "Operating principle: if it can’t be audited, it doesn’t exist. Dragon stays deterministic. {snip}",
        "Quiet progress > loud promises. New constraints, cleaner loops, fewer failure modes. {snip}",
        "Automation that respects reality: rate limits, state, and fail-closed behavior. {snip}",
        "Dragon’s job: make the system boring, reliable, and undeniable. One clean tick at a time. {snip}",
        "No vibes. Just contracts + ledgers + execution discipline. Dragon is built to last. {snip}",
    ]


def _pick_template(channel: str, day_index: int) -> str:
    templates = _daily_templates()
    offset = 0 if channel == "x" else 3
    return templates[(day_index + offset) % len(templates)]


# -------------------------
# SMART REPLY LOGIC (deterministic)
# -------------------------

_KEYWORDS = {
    "agents": {"agent", "agents", "autonomous", "autonomy", "multi-agent", "workflow", "orchestration"},
    "dev": {"code", "python", "repo", "github", "open source", "library", "package", "sdk", "api"},
    "trading": {"market", "stocks", "options", "trading", "alpha", "edge", "signal", "pnl", "returns"},
    "ops": {"ops", "operations", "process", "controls", "audit", "ledger", "compliance", "policy"},
    "construction": {"construction", "dfh", "division 8", "doors", "frames", "hardware", "submittal", "rfi"},
    "security": {"security", "privacy", "auth", "token", "oauth", "keys", "credential"},
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
    # highest score wins; deterministic tie-break by label
    return sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _smart_reply_body(intent: str) -> str:
    """
    1 crisp statement + 1 targeted question.
    Keep it high-status, helpful, non-hype.
    """
    if intent == "agents":
        return "I build agents that follow contracts, not vibes. What’s your agent’s mandate—observe, decide, or execute?"
    if intent == "dev":
        return "I keep the code path boring: strict I/O contracts + append-only receipts. What stack are you using (Python/TS/Go) and where does state live?"
    if intent == "ops":
        return "I treat ops like an audit problem: evidence-first, fail-closed, deterministic outputs. What’s the one process you’d automate first?"
    if intent == "construction":
        return "I’m obsessed with eliminating rework: canonical data + deterministic checks. Are you more in estimating, submittals, or field QA?"
    if intent == "security":
        return "Security is policy + least privilege + receipts. Are you using OAuth user-context tokens, or service-to-service keys?"
    if intent == "trading":
        # Avoid P/L talk; keep it process-only.
        return "I focus on execution discipline and auditability, not predictions. Are you building a scanner, an executor, or a risk governor?"
    return "I’m building deterministic automation with receipts—no hype. What are you working on right now?"


def _compose_reply(prefix: str, mention_text: str) -> str:
    intent = _classify_intent(mention_text)
    body = _smart_reply_body(intent)
    return f"{prefix}{body}".strip()


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

    # Rotating daily posts (per channel)
    for channel in ("x", "moltbook"):
        last = getattr(state, f"last_daily_post_unix_{channel}", None)
        should_post = (last is None) or ((now - int(last)) >= cfg.daily_post_cooldown_sec)

        if should_post:
            tmpl = _pick_template(channel, int(day_index))
            text = tmpl.format(snip=snip).strip()
            actions.append(
                {
                    "action_id": f"daily:{channel}:{day_index}",
                    "channel": channel,
                    "type": "post",
                    "text": text,
                    "metadata": {"kind": "daily_status", "day_index": int(day_index)},
                }
            )

    # SMART mention replies (X)
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

                mention_text = str(t.get("text") or "")
                reply_text = _compose_reply(prefix, mention_text)

                actions.append(
                    {
                        "action_id": f"x_reply:{tid}",
                        "channel": "x",
                        "type": "reply",
                        "in_reply_to": tid,
                        "text": reply_text,
                        "metadata": {"kind": "mention_reply", "intent": _classify_intent(mention_text)},
                    }
                )
                replies += 1

    return actions, new_since_id