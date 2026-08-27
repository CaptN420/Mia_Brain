from __future__ import annotations

import re
import time
from typing import Dict, List

import requests

from action_monitor import SecurityStop, guard_network_request, log_effect


class OllamaClient:
    def __init__(self, api: str, request_timeout: int = 60, max_retries: int = 1, backoff_seconds: float = 0.7, num_gpu_layers: int = -1):
        self.api = api
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.num_gpu_layers = num_gpu_layers

    def _looks_truncated(self, text: str) -> bool:
        if not text:
            return True
        t = text.strip()
        if len(t) < 80:
            return False
        bad_end = (
            t.endswith(":")
            or t.endswith("-")
            or t.endswith("(")
            or t.endswith("[")
            or t.endswith("**")
            or t.endswith("##")
            or re.search(r"https?://\S*$", t) is not None
        )
        if bad_end:
            return True
        # unmatched markdown / obvious open block
        if t.count("**") % 2 == 1:
            return True
        if t.count("(") > t.count(")"):
            return True
        if t.count("[") > t.count("]"):
            return True
        return False

    def _clean(self, text: str) -> str:
        t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        # kill urls
        t = re.sub(r"https?://\S+", "[URL-INTERDITE]", t)
        # collapse blank lines
        t = re.sub(r"\n{3,}", "\n\n", t)
        # dedupe immediate repeated lines
        lines = []
        last_norm = ""
        for line in t.splitlines():
            norm = re.sub(r"\s+", " ", line.strip().lower())
            if norm and norm == last_norm:
                continue
            lines.append(line.rstrip())
            last_norm = norm
        t = "\n".join(lines).strip()
        return t

    def _finalize_complete(self, text: str) -> str:
        t = self._clean(text)
        if not t:
            return t

        # If the model kept going into a URL bullet, drop broken tail.
        t = re.sub(r"\[URL-INTERDITE\][^\n]*$", "", t).strip()

        # Prefer cutting at last complete punctuation or field line.
        if len(t) > 220:
            last_punct = max(t.rfind(". "), t.rfind("!\n"), t.rfind("?\n"), t.rfind(".\n"), t.rfind(": "))
            if last_punct > int(len(t) * 0.55):
                t = t[: last_punct + 1].strip()

        # Ensure the last line is not a dangling label
        lines = t.splitlines()
        while lines and re.match(r"^[A-Za-zÀ-ÿ _-]+\s*:\s*$", lines[-1].strip()):
            lines.pop()
        t = "\n".join(lines).strip()

        if not t:
            return t

        # Close the final sentence softly if needed
        if not re.search(r"[.!?]$", t) and not t.endswith(("]", ")", '"')):
            t += "."
        return t

    def ask(self, model: str, messages: List[Dict[str, str]], temperature: float = 0.2, num_predict: int = 120) -> str:
        guard_network_request(self.api, service='ollama_api')
        base_predict = max(80, int(num_predict))
        predicts = [base_predict, min(base_predict + 30, 180)]
        last_error = ""

        for predict_idx, predict in enumerate(predicts, start=1):
            options = {
                "temperature": temperature,
                "num_predict": predict,
            }
            if self.num_gpu_layers is not None:
                options["n_gpu_layers"] = self.num_gpu_layers

            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": options,
            }

            for attempt in range(1, self.max_retries + 2):
                started = time.time()
                try:
                    resp = requests.post(self.api, json=payload, timeout=self.request_timeout)
                    log_effect('service_call', target=self.api, risk='low', details={'model': model, 'timeout': self.request_timeout})
                    secs = time.time() - started
                    if resp.status_code != 200:
                        last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        raise requests.RequestException(last_error)

                    data = resp.json()
                    message = data.get("message", {}) or {}
                    raw = str(message.get("content", "") or message.get("thinking", "") or "")
                    text = self._clean(raw)
                    chars = len(text)

                    if self._looks_truncated(text) and predict_idx == 1 and chars < max(220, int(base_predict * 1.8)):
                        print(
                            f"[API INCOMPLETE] model={model} try={attempt}/{self.max_retries + 1} "
                            f"secs={secs:.1f} chars={chars} predict={predict} -> retry predict={predicts[1]}"
                        )
                        break

                    text = self._finalize_complete(text)
                    print(
                        f"[API OK] model={model} try={attempt}/{self.max_retries + 1} "
                        f"secs={secs:.1f} chars={len(text)} predict={predict}"
                    )
                    return text

                except SecurityStop:
                    raise
                except requests.RequestException as exc:
                    secs = time.time() - started
                    last_error = str(exc)
                    if attempt <= self.max_retries:
                        print(
                            f"[API RETRY] model={model} try={attempt}/{self.max_retries + 1} "
                            f"timeout={self.request_timeout}s reason={last_error}"
                        )
                        time.sleep(self.backoff_seconds * attempt)
                    else:
                        print(f"[API FAIL] model={model} reason={last_error}")

        return ""
