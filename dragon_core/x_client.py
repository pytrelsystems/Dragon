# dragon_core/x_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import json
import os
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class XConfig:
    access_token: str
    base_url: str = "https://api.x.com"


class XClient:
    def __init__(self, cfg: XConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def from_env() -> "XClient":
        tok = os.environ.get("X_USER_ACCESS_TOKEN", "").strip()
        if not tok:
            raise RuntimeError("Missing X_USER_ACCESS_TOKEN")
        base = os.environ.get("X_BASE_URL", "https://api.x.com").strip()
        return XClient(XConfig(access_token=tok, base_url=base))

    def me(self) -> Dict[str, Any]:
        return self._get_json("/2/users/me")

    def mentions(
        self,
        user_id: str,
        since_id: Optional[str] = None,
        max_results: int = 10,
    ) -> Dict[str, Any]:
        # Add fields + expansions so we can see author username
        qs = {
            "max_results": str(max(5, min(100, int(max_results)))),
            "tweet.fields": "author_id,created_at,conversation_id,lang",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        if since_id:
            qs["since_id"] = since_id
        return self._get_json(f"/2/users/{user_id}/mentions?{urllib.parse.urlencode(qs)}")

    def post(self, text: str) -> Dict[str, Any]:
        return self._post_json("/2/tweets", {"text": text})

    def reply(self, in_reply_to_tweet_id: str, text: str) -> Dict[str, Any]:
        payload = {"text": text, "reply": {"in_reply_to_tweet_id": in_reply_to_tweet_id}}
        return self._post_json("/2/tweets", payload)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.access_token}", "Content-Type": "application/json"}

    def _get_json(self, path: str) -> Dict[str, Any]:
        url = self.cfg.base_url.rstrip("/") + path
        req = urllib.request.Request(url, method="GET")
        for k, v in self._headers().items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.cfg.base_url.rstrip("/") + path
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        for k, v in self._headers().items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}