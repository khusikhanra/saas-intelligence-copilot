"""Thin OpenAI wrapper. The key comes from the environment only; every failure becomes an LLMError with a safe message."""
from __future__ import annotations

import json

from src import config
from src.security.logging_utils import log_event, redact


class LLMError(RuntimeError):
    """Any failure talking to the model (missing key, auth, rate limit, timeout, bad output)."""


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, client=None):
        self.model = model or config.OPENAI_MODEL
        self._client = client
        self._key = api_key or config.openai_api_key()
        if self._client is None and not self._key:
            raise LLMError("No OpenAI API key configured (set OPENAI_API_KEY).")

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._key, timeout=config.OPENAI_TIMEOUT_SECONDS, max_retries=config.OPENAI_MAX_RETRIES)
        return self._client

    def chat_json(self, system: str, user: str, max_tokens: int = 900) -> dict:
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0, max_tokens=max_tokens, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            text = resp.choices[0].message.content or ""
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate every SDK failure into one safe error type
            name = type(exc).__name__
            log_event("llm_error", kind=name)
            hints = {"AuthenticationError": "The API key was rejected.", "RateLimitError": "The AI service is rate-limiting requests; try again shortly.",
                     "APITimeoutError": "The AI service timed out.", "APIConnectionError": "Could not reach the AI service."}
            raise LLMError(hints.get(name, f"The AI service returned an error ({name}).")) from None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise LLMError("The AI service returned malformed output.") from None
        if not isinstance(data, dict):
            raise LLMError("The AI service returned an unexpected structure.")
        return data


def get_client_or_none() -> LLMClient | None:
    try:
        return LLMClient()
    except LLMError:
        return None
