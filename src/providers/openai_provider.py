# src/providers/openai_provider.py
from __future__ import annotations
import os
from typing import Any, Dict, Optional, Iterator, Tuple
import json

import httpx
from openai import OpenAI
from providers.base import LLMProvider, LLMResult
from providers.errors import retry_call

# 可选：如果你的 openai SDK 暴露了这些异常类型，可引入更精确的重试
try:
    from openai import (
        APITimeoutError,
        APIConnectionError,
        RateLimitError,
        InternalServerError,
    )

    RETRYABLE = (
        APITimeoutError,
        APIConnectionError,
        RateLimitError,
        InternalServerError,
        httpx.TimeoutException,
    )
except Exception:
    RETRYABLE = (Exception,)


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
        self.model = model
        self.max_retries = max_retries

        kwargs: Dict[str, Any] = {
            "timeout": timeout_s,
            "max_retries": 0,
        }  # 让 retry_call 接管重试
        if base_url:
            kwargs["base_url"] = base_url
        if organization:
            kwargs["organization"] = organization
        if project:
            kwargs["project"] = project
        if proxy_url:
            kwargs["http_client"] = httpx.Client(proxy=proxy_url)

        if api_key is None:
            api_key = os.environ.get("OPENAI_API_KEY")

        self.client = OpenAI(api_key=api_key, **kwargs)

    def generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> LLMResult:
        def _call():
            return self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )

        resp = retry_call(
            _call,
            provider="openai",
            max_retries=self.max_retries,
            retryable_exceptions=RETRYABLE,
        )
        text = (resp.choices[0].message.content or "").strip()
        return LLMResult(text=text, raw={"id": getattr(resp, "id", None)})

    def stream_generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> Iterator[str]:
        """
        逐段 yield 文本（delta），用于边生成边落盘
        """

        def _start_stream():
            return self.client.chat.completions.create(
                model=self.model,
                stream=True,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )

        stream = retry_call(
            _start_stream,
            provider="openai",
            max_retries=self.max_retries,
            retryable_exceptions=RETRYABLE,
        )

        for event in stream:
            # openai python: event.choices[0].delta.content
            delta = getattr(event.choices[0], "delta", None)
            if not delta:
                continue
            chunk = getattr(delta, "content", None)
            if chunk:
                yield chunk

    def generate_json(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        返回 (raw_text, parsed_obj). 优先 response_format 强制 JSON，不支持则降级。
        """

        def _call_json():
            return self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )

        # 1) 尝试强约束 JSON
        try:
            resp = retry_call(
                _call_json,
                provider="openai",
                max_retries=self.max_retries,
                retryable_exceptions=RETRYABLE,
            )
            raw = (resp.choices[0].message.content or "").strip()
            return raw, json.loads(raw)
        except Exception:
            # 2) 降级：普通 generate，再用你现有的“截取 {}”方式解析
            raw = self.generate(system, prompt, meta).text
            raw2 = raw.strip()
            try:
                return raw2, json.loads(raw2)
            except Exception:
                l = raw2.find("{")
                r = raw2.rfind("}")
                if l != -1 and r != -1 and r > l:
                    s = raw2[l : r + 1]
                    return raw2, json.loads(s)
                raise
