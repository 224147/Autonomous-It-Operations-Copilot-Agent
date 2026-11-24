"""LLM / embedding client. Supports Azure OpenAI and OpenAI; degrades gracefully when neither is configured."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from backend.config import Settings

logger = logging.getLogger(__name__)


class LLM(Protocol):
    name: str

    def chat(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 1200) -> str | None: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAILLM:
    """Works for both OpenAI and Azure OpenAI via the official openai SDK."""

    def __init__(self, settings: Settings, azure: bool) -> None:
        import openai

        self._azure = azure
        self._timeout = settings.llm_timeout_seconds
        if azure:
            self._client = openai.AzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key.get_secret_value(),
                api_version=settings.azure_openai_api_version,
                timeout=self._timeout,
                max_retries=2,
            )
            self._chat_model = settings.azure_openai_chat_deployment
            self._embed_model = settings.azure_openai_embedding_deployment
            self.name = f"azure:{self._embed_model or 'no-embedding'}"
        else:
            kwargs: dict[str, Any] = {}
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = openai.OpenAI(
                api_key=settings.openai_api_key.get_secret_value(), timeout=self._timeout, max_retries=2, **kwargs
            )
            self._chat_model = settings.openai_chat_model
            self._embed_model = settings.openai_embedding_model
            self.name = f"openai:{self._embed_model}"

    @property
    def can_embed(self) -> bool:
        return bool(self._embed_model)

    @property
    def can_chat(self) -> bool:
        return bool(self._chat_model)

    def chat(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 1200) -> str | None:
        if not self.can_chat:
            return None
        try:
            kwargs: dict[str, Any] = {}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = self._client.chat.completions.create(
                model=self._chat_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                max_tokens=max_tokens,
                **kwargs,
            )
            return resp.choices[0].message.content
        except Exception:
            logger.exception("LLM chat call failed; falling back to deterministic logic")
            return None

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._embed_model, input=texts)
        return [item.embedding for item in resp.data]


def build_llm(settings: Settings) -> OpenAILLM | None:
    provider = settings.llm_provider
    azure_ready = bool(settings.azure_openai_endpoint and settings.azure_openai_api_key.get_secret_value())
    openai_ready = bool(settings.openai_api_key.get_secret_value())
    try:
        if provider == "azure" or (provider == "auto" and azure_ready):
            if not azure_ready:
                logger.warning("LLM_PROVIDER=azure but endpoint/key missing; LLM disabled")
                return None
            return OpenAILLM(settings, azure=True)
        if provider == "openai" or (provider == "auto" and openai_ready):
            if not openai_ready:
                logger.warning("LLM_PROVIDER=openai but OPENAI_API_KEY missing; LLM disabled")
                return None
            return OpenAILLM(settings, azure=False)
    except Exception:
        logger.exception("Failed to initialise LLM client; continuing without LLM")
    return None


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_json_object(text: str | None) -> dict[str, Any] | None:
    """Extract a JSON object from an LLM reply (tolerates code fences)."""
    if not text:
        return None
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
