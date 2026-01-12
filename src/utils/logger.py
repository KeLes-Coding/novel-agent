import json
import logging
import os
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional
from typing import Callable


@dataclass(frozen=True)
class RunContext:
    run_id: str
    run_dir: str
    level: str = "DEBUG"
    jsonl_events: bool = True
    prompt_preview_chars: int = 240


class JsonlEventLogger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def emit(self, payload: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def setup_loggers(ctx: RunContext) -> Dict[str, Any]:
    os.makedirs(os.path.join(ctx.run_dir, "logs"), exist_ok=True)
    log_path = os.path.join(ctx.run_dir, "logs", "app.log")
    jsonl_path = os.path.join(ctx.run_dir, "logs", "app.jsonl")

    logger = logging.getLogger("novel_agent")
    logger.setLevel(getattr(logging, ctx.level.upper(), logging.DEBUG))
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | run=%(run_id)s step=%(step)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 清理重复 handler
    logger.handlers.clear()

    file_handler = RotatingFileHandler(
        log_path, maxBytes=50 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    jsonl = JsonlEventLogger(jsonl_path) if ctx.jsonl_events else None
    return {"logger": logger, "jsonl": jsonl}


class StepTimer:
    def __init__(self):
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


def log_event(jsonl: Optional[JsonlEventLogger], payload: Dict[str, Any]) -> None:
    if jsonl is not None:
        jsonl.emit(payload)


class LogAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("run_id", self.extra.get("run_id", "-"))
        extra.setdefault("step", self.extra.get("step", "-"))
        return msg, kwargs


def setup_loggers(ctx: RunContext) -> Dict[str, Any]:
    os.makedirs(os.path.join(ctx.run_dir, "logs"), exist_ok=True)
    log_dir = os.path.join(ctx.run_dir, "logs")
    log_path = os.path.join(log_dir, "app.log")
    jsonl_path = os.path.join(log_dir, "app.jsonl")

    base = logging.getLogger("novel_agent")
    base.setLevel(getattr(logging, ctx.level.upper(), logging.DEBUG))
    base.propagate = False
    base.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | run=%(run_id)s step=%(step)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # app.log
    file_handler = RotatingFileHandler(
        log_path, maxBytes=50 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    base.addHandler(file_handler)

    # console
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    base.addHandler(console)

    # JSONL events
    jsonl = JsonlEventLogger(jsonl_path) if ctx.jsonl_events else None

    # ——新增：按 step 的文件 handler（缓存，避免重复创建）——
    step_handlers: Dict[str, RotatingFileHandler] = {}

    def get_step_logger(step: str) -> LogAdapter:
        if step not in step_handlers:
            step_path = os.path.join(log_dir, f"step_{step}.log")
            h = RotatingFileHandler(
                step_path, maxBytes=50 * 1024 * 1024, backupCount=10, encoding="utf-8"
            )
            h.setFormatter(fmt)
            step_handlers[step] = h
            base.addHandler(h)
        return LogAdapter(base, {"run_id": ctx.run_id, "step": step})

    return {"logger": base, "jsonl": jsonl, "get_step_logger": get_step_logger}
