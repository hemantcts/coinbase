"""Optional alerts: POST a small JSON payload to a webhook (Slack, Discord, n8n, ...)."""
from __future__ import annotations

import httpx


class Notifier:
    def __init__(self, webhook: str = "", timeout: float = 5.0):
        self.webhook = webhook
        self.timeout = timeout
        self.failures = 0

    def send(self, title: str, message: str, level: str = "info") -> bool:
        if not self.webhook:
            return False
        payload = {"text": f"[{level.upper()}] {title}: {message}", "title": title, "message": message, "level": level}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                client.post(self.webhook, json=payload).raise_for_status()
            return True
        except Exception:
            self.failures += 1
            return False
