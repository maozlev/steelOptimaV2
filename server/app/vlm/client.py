import base64
import hashlib
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.vlm.prompts import CLASSIFY_CROP_PROMPT, VERDICT_SCHEMA, VlmVerdict


def _strip_fences(raw: str) -> str:
    # with think:False the model sometimes wraps the JSON in ```json fences
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


@dataclass
class VlmResult:
    verdict: VlmVerdict | None
    raw_response: str | None
    latency_ms: int
    prompt_hash: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict is not None


class OllamaVlmClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.base_url = base_url or settings.ollama_url
        self.model = model or settings.vlm_model
        self.timeout_s = timeout_s or settings.vlm_timeout_s

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            models = r.json().get("models", [])
        except Exception:
            return False
        return any(
            m["name"] == self.model and "vision" in m.get("capabilities", [])
            for m in models
        )

    def classify_crop(self, crop_png: bytes, prompt: str | None = None) -> VlmResult:
        prompt = prompt or CLASSIFY_CROP_PROMPT
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        payload = {
            "model": self.model,
            "think": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(crop_png).decode()],
                }
            ],
            "format": VERDICT_SCHEMA,
            "stream": False,
            "options": {"temperature": 0},
        }

        t0 = time.time()
        raw, error = None, None
        for _ in range(2):  # one retry on invalid/unparseable output
            try:
                r = httpx.post(
                    f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_s
                )
                r.raise_for_status()
                raw = r.json().get("message", {}).get("content")
                verdict = VlmVerdict.model_validate_json(_strip_fences(raw))
                return VlmResult(
                    verdict=verdict,
                    raw_response=raw,
                    latency_ms=int((time.time() - t0) * 1000),
                    prompt_hash=prompt_hash,
                )
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
        return VlmResult(
            verdict=None,
            raw_response=raw,
            latency_ms=int((time.time() - t0) * 1000),
            prompt_hash=prompt_hash,
            error=error,
        )
