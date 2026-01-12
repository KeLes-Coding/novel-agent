import os
import yaml
import datetime
import uuid
from typing import Dict, Any

from utils.logger import RunContext, setup_loggers, LogAdapter, StepTimer, log_event
from utils.hashing import sha256_text, sha256_file
from storage.local_store import LocalStore
from providers.mock import MockProvider
from providers.factory import build_provider
from pipeline.step_02_outline import run as outline_run
from pipeline.step_03_bible import run as bible_run
from pipeline.step_04_drafting import run as drafting_run
from pipeline.step_05_qc import run as qc_run


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def snapshot_configs(run_dir: str, config_paths: Dict[str, str]) -> None:
    snap_dir = os.path.join(run_dir, "snapshot")
    os.makedirs(snap_dir, exist_ok=True)
    for name, p in config_paths.items():
        with open(p, "r", encoding="utf-8") as fsrc:
            with open(
                os.path.join(snap_dir, f"{name}.yaml"), "w", encoding="utf-8"
            ) as fdst:
                fdst.write(fsrc.read())


def new_run_dir(runs_dir: str) -> tuple[str, str]:
    today = datetime.date.today().isoformat()
    base = os.path.join(runs_dir, today)
    os.makedirs(base, exist_ok=True)
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run_dir = os.path.join(base, run_id)
    os.makedirs(run_dir, exist_ok=True)
    return run_id, run_dir


def build_system_prompt(prompts: Dict[str, Any]) -> str:
    return prompts.get("global_system", "").strip()


def run_once(config_path="config/config.yaml", prompts_path="config/prompts.yaml"):
    cfg = load_yaml(config_path)
    prompts = load_yaml(prompts_path)

    run_id, run_dir = new_run_dir(cfg["output"]["runs_dir"])
    snapshot_configs(run_dir, {"config": config_path, "prompts": prompts_path})

    ctx = RunContext(
        run_id=run_id,
        run_dir=run_dir,
        level=cfg["logging"]["level"],
        jsonl_events=cfg["logging"]["jsonl_events"],
        prompt_preview_chars=cfg["logging"]["prompt_preview_chars"],
    )

    env = setup_loggers(ctx)
    base_logger = env["logger"]
    jsonl = env["jsonl"]
    get_step_logger = env["get_step_logger"]

    orch_logger = LogAdapter(base_logger, {"run_id": run_id, "step": "orchestrator"})
    store = LocalStore(run_dir)

    # Provider（先 mock）
    provider = build_provider(cfg)

    # ========== Step: ideation ==========
    step = "ideation"
    log = get_step_logger(step)

    step_total = StepTimer()
    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": step,
            "event": "STEP_START",
        },
    )

    try:
        tags = cfg["content"]["tags"]
        genre = cfg["content"]["genre"]
        target_words = cfg["content"]["length"]["target_words"]

        ideation_prompt = (
            prompts["ideation"].strip()
            + f"\n\n约束：题材={genre}，tags={tags}，目标字数≈{target_words}"
        )
        system = build_system_prompt(prompts)

        # 记录 prompt_hash（system + prompt）
        full_prompt = system + "\n\n" + ideation_prompt
        prompt_hash = sha256_text(full_prompt)

        log.info(
            f"Calling model provider={cfg['provider']['type']} model={cfg['provider']['model']} prompt_hash={prompt_hash[:12]}"
        )

        # 模型调用耗时
        t_call = StepTimer()
        ideas_text = provider.generate(
            system=system, prompt=ideation_prompt, meta={"cfg": cfg}
        )
        call_ms = t_call.ms()

        output_hash = sha256_text(ideas_text)

        # 落盘
        path = store.save_text("01_ideation/ideas.txt", ideas_text)
        artifact_hash = sha256_file(path)

        log.info(
            f"Saved artifact: {path} call_ms={call_ms} output_hash={output_hash[:12]} artifact_hash={artifact_hash[:12]}"
        )

        # JSONL：MODEL_CALL
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "MODEL_CALL",
                "provider": cfg["provider"]["type"],
                "model": cfg["provider"]["model"],
                "prompt_hash": prompt_hash,
                "output_hash": output_hash,
                "duration_ms": call_ms,
                "prompt_preview": ideation_prompt[: ctx.prompt_preview_chars],
            },
        )

        # JSONL：ARTIFACT_SAVED
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ARTIFACT_SAVED",
                "artifact_path": path,
                "artifact_sha256": artifact_hash,
            },
        )

    except Exception as e:
        # 关键：log.exception 会把 traceback 全打出来
        log.exception("Step failed with exception")
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ERROR",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise

    finally:
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "STEP_END",
                "duration_ms": step_total.ms(),
            },
        )

        # ========== Step: outline ==========
    step = "outline"
    log = get_step_logger(step)
    step_total = StepTimer()
    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": step,
            "event": "STEP_START",
        },
    )

    try:
        # ideation 产物路径（就是你前面 save_text 返回的 path）
        idea_path = path

        log.info(f"Building outline from idea_path={idea_path}")

        result = outline_run(
            {
                "cfg": cfg,
                "prompts": prompts,
                "provider": provider,
                "store": store,
                "log": log,
                "jsonl": jsonl,
                "run_id": run_id,
                "idea_path": idea_path,
            }
        )

        outline_path = result["outline_path"]
        outline_hash = sha256_file(outline_path)

        log.info(
            f"Saved outline artifact: {outline_path} artifact_hash={outline_hash[:12]}"
        )

        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ARTIFACT_SAVED",
                "artifact_path": outline_path,
                "artifact_sha256": outline_hash,
            },
        )

    except Exception as e:
        log.exception("Step failed with exception")
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ERROR",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise

    finally:
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "STEP_END",
                "duration_ms": step_total.ms(),
            },
        )

        # ========== Step: bible ==========
    step = "bible"
    log = get_step_logger(step)
    step_total = StepTimer()
    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": step,
            "event": "STEP_START",
        },
    )

    try:
        # 这里的 outline_path 需要是你 outline step 的输出路径变量
        # 如果你当前没有保存成变量：请在 outline step 成功后加一句 outline_path = result["outline_path"]
        log.info(f"Building character bible from outline_path={outline_path}")

        # ——生成 prompt_hash / output_hash / artifact_hash / call_ms——
        system = build_system_prompt(prompts)

        # 调用 step_03_bible
        t_call = StepTimer()
        result = bible_run(
            {
                "cfg": cfg,
                "prompts": prompts,
                "provider": provider,
                "store": store,
                "outline_path": outline_path,
            }
        )
        call_ms = t_call.ms()

        bible_path = result["bible_path"]
        bible_prompt = result["bible_prompt"]
        bible_text = result["bible_text"]

        full_prompt = system + "\n\n" + bible_prompt
        prompt_hash = sha256_text(full_prompt)
        output_hash = sha256_text(bible_text)
        artifact_hash = sha256_file(bible_path)

        log.info(
            f"Saved bible artifact: {bible_path} call_ms={call_ms} prompt_hash={prompt_hash[:12]} "
            f"output_hash={output_hash[:12]} artifact_hash={artifact_hash[:12]}"
        )

        # JSONL：MODEL_CALL（这里我们把它当作 bible step 的“模型调用事件”）
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "MODEL_CALL",
                "provider": cfg["provider"]["type"],
                "model": cfg["provider"]["model"],
                "prompt_hash": prompt_hash,
                "output_hash": output_hash,
                "duration_ms": call_ms,
                "prompt_preview": bible_prompt[: ctx.prompt_preview_chars],
            },
        )

        # JSONL：ARTIFACT_SAVED
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ARTIFACT_SAVED",
                "artifact_path": bible_path,
                "artifact_sha256": artifact_hash,
            },
        )

    except Exception as e:
        log.exception("Step failed with exception")
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ERROR",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise
    finally:
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "STEP_END",
                "duration_ms": step_total.ms(),
            },
        )

        # ========== Step: drafting ==========
    step = "drafting"
    log = get_step_logger(step)
    step_total = StepTimer()
    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": step,
            "event": "STEP_START",
        },
    )

    try:
        log.info(
            f"Drafting scenes from outline_path={outline_path} bible_path={bible_path}"
        )

        t_call = StepTimer()
        result = drafting_run(
            {
                "cfg": cfg,
                "prompts": prompts,
                "provider": provider,
                "store": store,
                "run_id": run_id,
                "jsonl": jsonl,
                "ctx": ctx,
                "log": log,
                "outline_path": outline_path,
                "bible_path": bible_path,
            }
        )
        call_ms = t_call.ms()

        scene_plan_path = result["scene_plan_path"]
        draft_path = result["draft_path"]
        scene_paths = result["scene_paths"]

        log.info(f"Saved scene_plan: {scene_plan_path}")
        log.info(
            f"Saved draft: {draft_path} scenes={len(scene_paths)} total_ms={call_ms}"
        )

        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ARTIFACT_SAVED",
                "artifact_path": draft_path,
            },
        )

    except Exception as e:
        log.exception("Step failed with exception")
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ERROR",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise
    finally:
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "STEP_END",
                "duration_ms": step_total.ms(),
            },
        )

        # ========== Step: qc ==========
    step = "qc"
    log = get_step_logger(step)
    step_total = StepTimer()
    log_event(
        jsonl,
        {
            "ts": datetime.datetime.now().isoformat(),
            "run_id": run_id,
            "step": step,
            "event": "STEP_START",
        },
    )

    try:
        log.info("Running QC checks on draft and scenes")

        result = qc_run(
            {
                "store": store,
                "scene_plan_path": scene_plan_path,
                "draft_path": draft_path,
                "bible_path": bible_path,
            }
        )

        qc_report_path = result["qc_report_path"]
        verdict = result["verdict"]
        warnings = result["warnings"]

        log.info(f"QC verdict={verdict} report={qc_report_path}")
        if warnings:
            for w in warnings:
                log.warning(f"QC warning: {w}")

        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ARTIFACT_SAVED",
                "artifact_path": qc_report_path,
            },
        )

    except Exception as e:
        log.exception("Step failed with exception")
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "ERROR",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise
    finally:
        log_event(
            jsonl,
            {
                "ts": datetime.datetime.now().isoformat(),
                "run_id": run_id,
                "step": step,
                "event": "STEP_END",
                "duration_ms": step_total.ms(),
            },
        )

    orch_logger.info(f"Run completed at {run_dir}")
    return run_dir


if __name__ == "__main__":
    run_once()
