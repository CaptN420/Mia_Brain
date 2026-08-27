from __future__ import annotations

import json
import time
import re
from typing import Dict, List, Any, Optional

import requests


class OpenAIClient:
    """Client for interacting with OpenAI-compatible APIs (e.g., LM Studio, Ollama)."""

    def __init__(
        self,
        api: str = "http://localhost:1234/v1/chat/completions",
        request_timeout: int = 60,
        max_retries: int = 1,
        retry_backoff_seconds: float = 0.7,
        api_key: Optional[str] = None,
    ):
        self.api = api.rstrip("/")
        self.request_timeout = request_timeout
        self.max_api_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.api_key = api_key

    def ask(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 120,
    ) -> str:
        """Send a chat completion request and return the response text."""
        base_predict = max(1, max_tokens)
        predicts = [base_predict, min(base_predict + 30, 180)]

        last_error = ""

        for predict in predicts:
            options = {
                "temperature": temperature,
                "num_predict": predict,
            }

            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": options,
            }

            for attempt in range(1, self.max_api_retries + 2):
                started = time.time()
                try:
                    resp = requests.post(
                        self.api,
                        json=payload,
                        timeout=self.request_timeout,
                    )
                    if resp.status_code != 200:
                        last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        raise requests.RequestException(last_error)

                    data = resp.json()
                    message = data.get("message", {}) or {}
                    raw = str(message.get("content", "") or message.get("reasoning", "") or "")
                    text = raw.strip()

                    if not text:
                        last_error = "Empty response from API"
                        raise requests.RequestException(last_error)

                    return text

                except requests.RequestException as exc:
                    secs = time.time() - started
                    last_error = str(exc)
                    if attempt <= self.max_api_retries:
                        print(f"[API RETRY] model={model} try={attempt}/{self.max_api_retries + 1} "
                              f"timeout={self.request_timeout}s reason={last_error}")
                        time.sleep(self.retry_backoff_seconds * attempt)
                    else:
                        print(f"[API FAIL] model={model} reason={last_error}")

        return ""

    def _is_effectively_empty(self, text: str) -> bool:
        """Check if the response is effectively empty or a weak value."""
        if not text:
            return True
        lowered = text.lower().strip()
        weak_values = {
            "none", "null", "vide", "aucun", "aucune", "n/a", "-", ".",
        }
        return lowered in weak_values

    def _normalize_agent_output(self, text: str) -> str:
        """Normalize agent output by removing markdown formatting."""
        if not text:
            return ""
        s = str(text).strip()
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        s = re.sub(r"^```(?:text|md|markdown|json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()
