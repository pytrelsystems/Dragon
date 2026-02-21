# dragon_core/policy.py
"""
Dragon Engagement Policy (hard gates).

This is NOT vibes. It's deterministic enforcement:
- blocks disallowed content
- enforces tone + constraints
- applies rate limiting rules upstream (via executor too)

Keep this boring. This is what keeps Dragon reputable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
import re


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reasons: List[str]
    sanitized_text: str


_FINANCE_PROMISE_PATTERNS = [
    r"\bguarantee(d)?\b",
    r"\bfree money\b",
    r"\bcan't lose\b",
    r"\b100%\b",
    r"\bdouble your\b",
    r"\b(to the moon)\b",
]

_PERSONAL_DATA_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN-ish
    r"\b\d{10}\b",  # phone-ish
    r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b",  # phone-ish
]

_HARASSMENT_PATTERNS = [
    r"\bkill yourself\b",
    r"\bgo die\b",
    r"\bstupid (idiot|moron)\b",
]

def evaluate_text(text: str) -> PolicyDecision:
    reasons: List[str] = []
    t = (text or "").strip()

    if not t:
        return PolicyDecision(False, ["empty"], "")

    lowered = t.lower()

    for pat in _FINANCE_PROMISE_PATTERNS:
        if re.search(pat, lowered):
            reasons.append(f"finance_promise:{pat}")

    for pat in _PERSONAL_DATA_PATTERNS:
        if re.search(pat, t):
            reasons.append(f"personal_data:{pat}")

    for pat in _HARASSMENT_PATTERNS:
        if re.search(pat, lowered):
            reasons.append(f"harassment:{pat}")

    sanitized = _sanitize(t)

    allowed = len(reasons) == 0
    return PolicyDecision(allowed=allowed, reasons=reasons, sanitized_text=sanitized)


def _sanitize(text: str) -> str:
    # Minimal sanitization; do not rewrite meaning.
    s = text.replace("\r\n", "\n").strip()
    # cap extreme length to avoid API issues
    return s[:2000]


def validate_action(action: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Validates a structured action object and returns:
      (allowed, reasons, normalized_action)
    """
    reasons: List[str] = []

    action_type = str(action.get("type", "")).strip().lower()
    channel = str(action.get("channel", "")).strip().lower()
    text = str(action.get("text", "")).strip()

    if action_type not in {"post", "reply"}:
        reasons.append("invalid_type")

    if channel not in {"moltbook", "x"}:
        reasons.append("invalid_channel")

    decision = evaluate_text(text)
    if not decision.allowed:
        reasons.extend(decision.reasons)

    normalized = dict(action)
    normalized["type"] = action_type
    normalized["channel"] = channel
    normalized["text"] = decision.sanitized_text

    # Replies need a target
    if action_type == "reply":
        target = action.get("in_reply_to")
        if not target:
            reasons.append("missing_in_reply_to")

    return (len(reasons) == 0, reasons, normalized)