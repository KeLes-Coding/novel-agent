from __future__ import annotations
import time
import random
from dataclasses import dataclass
from typing import Callable, TypeVar, Tuple

T = TypeVar("T")


@dataclass
class ProviderError(Exception):
    provider: str
    message: str
    retryable: bool = True

    def __str__(self) -> str:
        return f"[{self.provider}] {self.message} (retryable={self.retryable})"


def retry_call(
    fn: Callable[[], T],
    provider: str,
    max_retries: int = 2,
    base_sleep_s: float = 0.8,
    retryable_exceptions: Tuple[type, ...] = (Exception,),
) -> T:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            last_exc = e
            if attempt >= max_retries:
                raise ProviderError(
                    provider=provider, message=str(e), retryable=True
                ) from e
            # 指数退避 + 抖动
            sleep_s = base_sleep_s * (2**attempt) * (0.7 + random.random() * 0.6)
            time.sleep(sleep_s)
    raise ProviderError(
        provider=provider, message=str(last_exc), retryable=True
    ) from last_exc
