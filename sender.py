"""Evolution API client for sending WhatsApp text messages."""
from __future__ import annotations

import os
from typing import Any

import requests


class EvolutionSender:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        instance: str | None = None,
    ):
        self.base_url = (base_url or os.getenv("EVO_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("EVO_API_KEY")
        self.instance = instance or os.getenv("EVO_INSTANCE")
        missing = [k for k, v in {
            "EVO_URL": self.base_url,
            "EVO_API_KEY": self.api_key,
            "EVO_INSTANCE": self.instance,
        }.items() if not v]
        if missing:
            raise RuntimeError(f"Variaveis ausentes no .env: {', '.join(missing)}")

    def send_text(self, number: str, text: str, timeout: int = 30) -> dict[str, Any]:
        url = f"{self.base_url}/message/sendText/{self.instance}"
        headers = {"apikey": self.api_key, "Content-Type": "application/json"}
        payload = {"number": number, "text": text}
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def check_connection(self, timeout: int = 10) -> dict[str, Any]:
        url = f"{self.base_url}/instance/connectionState/{self.instance}"
        headers = {"apikey": self.api_key}
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()
