# src/utils/trace_logger.py
import json
import os
import time
import uuid
import datetime
from typing import Any, Dict, Optional, Iterator, Tuple

# 引入基类，方便类型注解
from providers.base import LLMProvider, LLMResult


class TraceLogger:
    def __init__(self, trace_path: str):
        self.trace_path = trace_path
        os.makedirs(os.path.dirname(trace_path), exist_ok=True)

    def log_call(
        self,
        run_id: str,
        step: str,
        provider: str,
        model: str,
        system: str,
        prompt: str,
        output: str,
        usage: Dict[str, int],
        latency_ms: int,
        error: Optional[str] = None,
        meta: Optional[Dict] = None,
    ):
        # 简单粗暴的字数统计（针对中文环境，直接len即可）
        word_count = len(output) if output else 0

        entry = {
            "trace_id": uuid.uuid4().hex,
            "timestamp": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": step,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "status": "error" if error else "success",
            "request": {"system": system, "prompt": prompt, "meta": meta or {}},
            "response": {
                "text": output,
                "word_count": word_count,  # <--- 新增：字数统计
                "usage": usage,
                "error": error,
            },
        }

        # 使用 append 模式写入 JSONL
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TracingProvider(LLMProvider):
    """
    包装器：自动拦截 Provider 的调用并记录到 TraceLogger
    """

    def __init__(
        self, inner: LLMProvider, logger: TraceLogger, run_id: str, step_getter
    ):
        self.inner = inner
        self.logger = logger
        self.run_id = run_id
        self.step_getter = step_getter  # 动态获取当前步骤名函数
        # 复制 inner 的属性以便外部访问
        self.model = getattr(inner, "model", "unknown")
        # 获取 provider 类型名称 (e.g. "openai", "anthropic")
        self.provider_type = inner.__class__.__name__.replace("Provider", "").lower()

    def generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> LLMResult:
        t0 = time.perf_counter()
        error = None
        result = None

        try:
            result = self.inner.generate(system, prompt, meta)
            return result
        except Exception as e:
            error = str(e)
            raise e
        finally:
            latency = int((time.perf_counter() - t0) * 1000)
            step_name = self.step_getter()

            text_out = result.text if result else ""
            # 获取 usage，如果没有则为空字典
            usage = getattr(result, "usage", {})

            self.logger.log_call(
                run_id=self.run_id,
                step=step_name,
                provider=self.provider_type,
                model=self.model,
                system=system,
                prompt=prompt,
                output=text_out,
                usage=usage,
                latency_ms=latency,
                error=error,
                meta=meta,
            )

    def stream_generate(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> Iterator[str]:
        # 流式生成的 Trace 比较特殊，需要累积内容
        t0 = time.perf_counter()
        collected_text = []
        error = None

        try:
            if not hasattr(self.inner, "stream_generate"):
                # 如果底层不支持流式，回退到普通生成
                res = self.generate(system, prompt, meta)
                yield res.text
                return

            stream = self.inner.stream_generate(system, prompt, meta)
            for chunk in stream:
                collected_text.append(chunk)
                yield chunk
        except Exception as e:
            error = str(e)
            raise e
        finally:
            latency = int((time.perf_counter() - t0) * 1000)
            step_name = self.step_getter()
            full_text = "".join(collected_text)

            # 流式通常较难获取精确 usage，标记一下
            usage = {"note": "streamed_estimation"}

            self.logger.log_call(
                run_id=self.run_id,
                step=step_name,
                provider=self.provider_type,
                model=self.model,
                system=system,
                prompt=prompt,
                output=full_text,
                usage=usage,
                latency_ms=latency,
                error=error,
                meta=meta,
            )

    def generate_json(
        self, system: str, prompt: str, meta: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        t0 = time.perf_counter()
        error = None
        raw_text = ""

        try:
            if hasattr(self.inner, "generate_json"):
                raw_text, obj = self.inner.generate_json(system, prompt, meta)
            else:
                # Fallback logic if needed, usually handled by caller or inner
                res = self.inner.generate(system, prompt, meta)
                raw_text = res.text
                obj = json.loads(raw_text)
            return raw_text, obj
        except Exception as e:
            error = str(e)
            raise e
        finally:
            latency = int((time.perf_counter() - t0) * 1000)
            step_name = self.step_getter()
            self.logger.log_call(
                run_id=self.run_id,
                step=step_name,
                provider=self.provider_type,
                model=self.model,
                system=system,
                prompt=prompt,
                output=raw_text,
                usage={"type": "json_mode"},
                latency_ms=latency,
                error=error,
                meta=meta,
            )
