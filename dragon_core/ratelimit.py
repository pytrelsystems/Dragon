# dragon_core/ratelimit.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict
import time


@dataclass
class RateLimiter:
    """
    Token bucket-ish, but simple + deterministic:
    - max_actions_per_window per channel
    - window_sec size
    """
    max_actions_per_window: int = 5
    window_sec: int = 300  # 5 minutes

    def __post_init__(self) -> None:
        self._bucket: Dict[str, list[int]] = {}

    def allow(self, channel: str) -> bool:
        now = int(time.time())
        lst = self._bucket.get(channel, [])
        # prune old
        lst = [t for t in lst if (now - t) < self.window_sec]
        if len(lst) >= self.max_actions_per_window:
            self._bucket[channel] = lst
            return False
        lst.append(now)
        self._bucket[channel] = lst
        return True