# src/providers/anthropic_provider.py
from __future__ import annotations
import os
from typing import Any, Dict, Optional

import httpx
from anthropic import Anthropic
from providers.base import LLMProvider, LLMResult
from providers.errors import retry_call


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        timeout_s: int = 60,
        max_retries: int = 2,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        max_tokens: int = 2048,
        proxy_url: Optional[str] = None,
    ):
        kwargs: Dict[str, Any] = {"timeout": timeout_s, "max_retries": max_retries}
        if base_url:
            kwargs["base_url"] = base_url
        if proxy_url:
            kwargs["http_client"] = httpx.Client(proxy=proxy_url)

        # api_key 优先级：显式传入 > 环境变量
        if api_key is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=api_key, **kwargs)
        self.model = model
        self.max_tokens = max_tokens

    def generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> LLMResult:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        # content 是一个 list，常见是第一段 text
        text = ""
        for block in getattr(msg, "content", []) or []:
            t = getattr(block, "text", None)
            if t:
                text += t
        return LLMResult(text=text.strip(), raw={"id": getattr(msg, "id", None)})
