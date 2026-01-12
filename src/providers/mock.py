from .base import LLMProvider
from typing import Dict, Any

class MockProvider(LLMProvider):
    def generate(self, system: str, prompt: str, meta: Dict[str, Any]) -> str:
        # 先用 mock 输出占位，后续接本地模型或 API
        return f"[MOCK OUTPUT]\nSYSTEM:\n{system}\n\nPROMPT:\n{prompt[:800]}\n"
