# src/providers/base.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass
class LLMResult:
    text: str
    raw: Optional[Dict[str, Any]] = None


class LLMProvider(Protocol):
    def generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> LLMResult: ...
