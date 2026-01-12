# src/providers/factory.py
from __future__ import annotations
from typing import Any, Dict

from providers.base import LLMProvider
from providers.mock import MockProvider
from providers.openai_provider import OpenAIProvider
from providers.anthropic_provider import AnthropicProvider
from providers.gemini_provider import GeminiProvider


def build_provider(cfg: Dict[str, Any]) -> LLMProvider:
    p = cfg["provider"]
    p_type = (p.get("type") or "mock").lower()
    model = p.get("model") or "unknown-model"

    timeout_s = int(p.get("timeout_s", 60))
    max_retries = int(p.get("max_retries", 2))

    if p_type == "mock":
        return MockProvider()

    if p_type == "openai":
        o = cfg.get("openai", {}) or {}
        return OpenAIProvider(
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            base_url=o.get("base_url"),
            api_key=o.get("api_key"),
            organization=o.get("organization"),
            project=o.get("project"),
            proxy_url=o.get("proxy_url"),
        )

    if p_type == "anthropic":
        a = cfg.get("anthropic", {}) or {}
        return AnthropicProvider(
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            base_url=a.get("base_url"),
            api_key=a.get("api_key"),
            max_tokens=int(a.get("max_tokens", 2048)),
            proxy_url=a.get("proxy_url"),
        )

    if p_type == "gemini":
        g = cfg.get("gemini", {}) or {}
        gen = g.get("generation", {}) or {}
        return GeminiProvider(
            model=model,
            temperature=float(gen.get("temperature", 0.8)),
            max_output_tokens=int(gen.get("max_output_tokens", 2048)),
            api_key=g.get("api_key"),
            proxy_url=g.get("proxy_url"),
        )

    raise ValueError(f"Unknown provider.type: {p_type}")
