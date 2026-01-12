# src/providers/openai_provider.py
from __future__ import annotations
import os
from typing import Any, Dict, Optional

import httpx
from openai import OpenAI
from providers.base import LLMProvider, LLMResult
from providers.errors import retry_call


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        timeout_s: int = 60,
        max_retries: int = 2,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        organization: Optional[str] = None,
        project: Optional[str] = None,
        proxy_url: Optional[str] = None,
    ):
        kwargs: Dict[str, Any] = {"timeout": timeout_s, "max_retries": max_retries}
        if base_url:
            kwargs["base_url"] = base_url
        if organization:
            kwargs["organization"] = organization
        if project:
            kwargs["project"] = project
        if proxy_url:
            kwargs["http_client"] = httpx.Client(proxy=proxy_url)

        # api_key 优先级：显式传入 > 环境变量
        if api_key is None:
            api_key = os.environ.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=api_key, **kwargs)
        self.model = model

    def generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> LLMResult:
        resp = self.client.chat.completions.create(
            model=self.model,  # DeepSeek: "deepseek-chat" 或 "deepseek-reasoner"
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return LLMResult(text=text, raw={"id": getattr(resp, "id", None)})
