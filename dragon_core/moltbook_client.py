# dragon_core/moltbook_client.py
"""
Moltbook client (minimal).

This is intentionally tiny and dependency-free (requests is optional).
If you prefer httpx later, swap it in.

Env:
  MOLTBOOK_APP_KEY=...
  MOLTBOOK_BASE_URL=https://moltbook.com  (override if needed)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import os
import json
import urllib.request


@dataclass(frozen=True)
class MoltbookConfig:
    app_key: str
    base_url: str = "https://moltbook.com"


class MoltbookClient:
    def __init__(self, cfg: MoltbookConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def from_env() -> "MoltbookClient":
        key = os.environ.get("MOLTBOOK_APP_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing MOLTBOOK_APP_KEY")
        base = os.environ.get("MOLTBOOK_BASE_URL", "https://moltbook.com").strip()
        return MoltbookClient(MoltbookConfig(app_key=key, base_url=base))

    def create_post(self, text: str) -> Dict[str, Any]:
        # Endpoint is a placeholder. Wire to Moltbook’s actual API route once confirmed.
        return self._post_json("/api/agent/posts", {"text": text})

    def reply(self, in_reply_to: str, text: str) -> Dict[str, Any]:
        return self._post_json("/api/agent/replies", {"in_reply_to": in_reply_to, "text": text})

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.cfg.base_url.rstrip("/") + path
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.cfg.app_key}")
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}