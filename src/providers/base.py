# src/providers/base.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Iterator, Tuple


@dataclass
class LLMResult:
    text: str
    raw: Optional[Dict[str, Any]] = None
    # 新增：标准化的 usage 统计
    usage: Optional[Dict[str, int]] = field(default_factory=dict)
    # e.g. {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}


class LLMProvider(Protocol):
    def generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> LLMResult: ...

    # Optional: support streaming
    def stream_generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> Iterator[str]: ...

    # Optional: support json mode
    def generate_json(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Dict[str, Any]]: ...
