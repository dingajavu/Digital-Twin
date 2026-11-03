"""Provider abstraction.

The agent depends only on the `LLMClient` protocol. The default
implementation talks to any OpenAI-compatible endpoint; tests and evals can
swap in a fake without touching agent code.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from .config import LLMConfig


class LLMClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Any:
        """Return an object shaped like an OpenAI chat completion `message`
        (with `.content` and `.tool_calls`)."""
        ...


# Works with OpenAI or any service offering the same API, by setting base_url.
class OpenAICompatibleClient:
    def __init__(self, cfg: LLMConfig, model: str | None = None):
        # Imported here so tests using a fake client don't need the OpenAI library loaded.
        from openai import OpenAI

        kwargs: dict[str, Any] = {}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        # If unset, the OpenAI library falls back to its own default lookup.
        api_key = os.getenv(cfg.api_key_env)
        if api_key:
            kwargs["api_key"] = api_key
        self._client = OpenAI(**kwargs)
        self.model = model or cfg.model
        self.extra_params = dict(cfg.extra_params or {})

    def complete(self, messages, tools=None):
        # extra_params carries settings from the config, like reasoning_effort.
        params: dict[str, Any] = {"model": self.model, "messages": messages, **self.extra_params}
        # Tools are only sent when there are some; the judge and the forced final answer use none.
        if tools:
            params["tools"] = tools
        response = self._client.chat.completions.create(**params)
        return response.choices[0].message


# Pick the client for the configured provider. `model` lets the eval judge use a different model.
def build_client(cfg: LLMConfig, model: str | None = None) -> LLMClient:
    if cfg.provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleClient(cfg, model=model)
    raise ValueError(f"Unsupported LLM provider: {cfg.provider}")
