# src/providers/gemini_provider.py
from __future__ import annotations
import os
from typing import Any, Dict, Optional

from google import genai
from google.genai import types
from providers.base import LLMProvider, LLMResult
from providers.errors import retry_call


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        temperature: float = 0.8,
        max_output_tokens: int = 2048,
        api_key: Optional[str] = None,
        proxy_url: Optional[str] = None,
    ):
        # 会自动读取 GEMINI_API_KEY 或 GOOGLE_API_KEY
        # 优先级：显式传入 > 环境变量
        if api_key is None:
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get(
                "GOOGLE_API_KEY"
            )

        client_kwargs = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if proxy_url:
            # Gemini 使用 HTTPX 代理
            client_kwargs["http_options"] = {
                "proxies": {"https": proxy_url, "http": proxy_url}
            }

        self.client = genai.Client(**client_kwargs)
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> LLMResult:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )
        resp = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=cfg,
        )
        text = getattr(resp, "text", "") or ""

        # --- 新增 usage 解析 ---
        usage_dict = {}
        # Gemini usage_metadata: prompt_token_count, candidates_token_count, total_token_count
        um = getattr(resp, "usage_metadata", None)
        if um:
            usage_dict = {
                "prompt_tokens": um.prompt_token_count,
                "completion_tokens": um.candidates_token_count,
                "total_tokens": um.total_token_count,
            }

        return LLMResult(text=text.strip(), raw={}, usage=usage_dict)
