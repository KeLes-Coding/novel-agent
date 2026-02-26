import os
import re
import json
import glob
import datetime
from collections import Counter
from typing import Dict, Any, List, Tuple


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _tokenize_zh(text: str) -> List[str]:
    # 简单 token：按中文/英文/数字分块（启发式，足够做重复率）
    tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", text)
    return tokens


def _ngram_counts(tokens: List[str], n: int) -> Counter:
    c = Counter()
    if len(tokens) < n:
        return c
    for i in range(len(tokens) - n + 1):
        c[tuple(tokens[i : i + n])] += 1
    return c


def _repetition_metrics(text: str) -> Dict[str, Any]:
    tokens = _tokenize_zh(text)
    total = len(tokens)
    res = {"token_count": total, "ngram": {}}
    for n in (3, 4, 5):
        c = _ngram_counts(tokens, n)
        if not c:
            res["ngram"][str(n)] = {"unique": 0, "top": [], "repeat_ratio": 0.0}
            continue
        repeated = sum(v for v in c.values() if v >= 2)
        total_ngrams = sum(c.values())
        repeat_ratio = (repeated / total_ngrams) if total_ngrams else 0.0
        top = [{"ngram": "".join(k), "count": v} for k, v in c.most_common(10)]
        res["ngram"][str(n)] = {
            "unique": len(c),
            "total_ngrams": total_ngrams,
            "repeat_ratio": round(repeat_ratio, 4),
            "top": top,
        }
    return res


def _find_cliffhanger_signals(text: str) -> Dict[str, Any]:
    # 非严格：看结尾 200 字是否含“悬念词/动作词”
    tail = text[-200:]
    signals = [
        "忽然",
        "就在此时",
        "下一刻",
        "轰然",
        "骤然",
        "变故",
        "不对劲",
        "怎么可能",
        "竟然",
        "来不及",
        "他抬头",
        "她抬头",
    ]
    hit = [s for s in signals if s in tail]
    return {"tail_has_signal": len(hit) > 0, "hits": hit, "tail_preview": tail}


def _single_female_lead_risk(text: str) -> Dict[str, Any]:
    """
    启发式检测“多女主暧昧风险”：
    - 统计“她/姑娘/仙子/师姐/师妹/圣女/郡主”等称谓密度
    - 检测暧昧词与多对象共现
    这只是早期提醒，不做最终裁决。
    """
    risk_terms = [
        "师姐",
        "师妹",
        "圣女",
        "郡主",
        "公主",
        "仙子",
        "姑娘",
        "女修",
        "红衣女子",
        "白衣女子",
    ]
    flirt_terms = [
        "暧昧",
        "脸红",
        "心跳",
        "耳根",
        "亲近",
        "搂",
        "抱",
        "吻",
        "温柔",
        "依偎",
        "眼波",
        "娇嗔",
    ]
    hits_risk = sum(text.count(t) for t in risk_terms)
    hits_flirt = sum(text.count(t) for t in flirt_terms)
    score = hits_risk * 0.4 + hits_flirt * 1.0
    return {
        "risk_term_hits": hits_risk,
        "flirt_term_hits": hits_flirt,
        "risk_score": round(score, 2),
        "notes": "启发式：risk_score 越高越可能存在多女主/暧昧描写，需要人工复核",
    }


def _load_scene_plan(scene_plan_path: str) -> Dict[str, Any]:
    with open(scene_plan_path, "r", encoding="utf-8") as f:
        return json.load(f)


from pipeline.base_step import PipelineStep

class QCStep(PipelineStep):
    def run(self) -> Dict[str, Any]:
        scene_plan_path = self.context.get("scene_plan_path")
        draft_path = self.context.get("draft_path")
        bible_path = self.context.get("bible_path")

        scene_plan = _load_scene_plan(scene_plan_path)
        scenes_meta = scene_plan.get("scenes", [])

        draft_text = _read_text(draft_path) if os.path.exists(draft_path) else ""
        bible_text = _read_text(bible_path) if os.path.exists(bible_path) else ""

        scenes_glob = os.path.join(os.path.dirname(draft_path), "scenes", "scene_*.md")
        scene_files = sorted(glob.glob(scenes_glob))

        scene_reports: List[Dict[str, Any]] = []
        global_verdict = "PASS"
        global_warnings = []
        
        # State scenes mapping for reroll triggers
        state_scenes = {f"scene_{s.id:03d}": s for s in getattr(self, "state", None).scenes} if hasattr(self, "state") and self.state else {}

        for idx, sp in enumerate(scene_files, start=1):
            txt = _read_text(sp)
            rep_metrics = _repetition_metrics(txt)
            cliffhanger = _find_cliffhanger_signals(txt)
            single_fl = _single_female_lead_risk(txt)
            
            # 校验指标
            scene_verdict = "PASS"
            rep_4 = rep_metrics["ngram"]["4"].get("repeat_ratio", 0.0)
            if rep_4 >= 0.15:
                scene_verdict = "FAIL"
                global_warnings.append(f"文件 {os.path.basename(sp)} 重复率极高 ({rep_4})，已标记为 FAIL")
            elif rep_4 >= 0.08:
                scene_verdict = "WARN"

            # 拦截重写逻辑 (Reroll trigger)
            if scene_verdict == "FAIL" and self.state:
                base_name = os.path.splitext(os.path.basename(sp))[0]
                # base_name format like scene_001
                if base_name in state_scenes:
                    node = state_scenes[base_name]
                    if node.status == "done":
                        node.status = "pending"  # 回拨状态触发 drafting Reroll
                        if self.log:
                            self.log.warning(f"Interceptor triggered: Scene {node.id} quality check FAIL. Status rolled back to 'pending'.")

            rep = {
                "scene_file": sp,
                "verdict": scene_verdict,
                "basic": {
                    "char_len": len(txt),
                    "tokenized": rep_metrics["token_count"],
                },
                "repetition": rep_metrics,
                "cliffhanger": cliffhanger,
                "single_fl_risk": single_fl,
            }
            scene_reports.append(rep)
            if scene_verdict == "FAIL":
                global_verdict = "FAIL"
            elif scene_verdict == "WARN" and global_verdict == "PASS":
                global_verdict = "WARN"

        overall = {
            "draft": {
                "char_len": len(draft_text),
                "repetition": _repetition_metrics(draft_text),
                "single_fl_risk": _single_female_lead_risk(draft_text),
            },
            "bible": {
                "char_len": len(bible_text),
            },
            "scene_plan": {
                "planned_scene_count": len(scenes_meta),
                "generated_scene_files": len(scene_files),
            },
        }

        if overall["scene_plan"]["generated_scene_files"] == 0:
            global_verdict = "FAIL"
            global_warnings.append("未生成任何 scene 文件")

        report = {
            "meta": {
                "generated_at": datetime.datetime.now().isoformat(),
                "verdict": global_verdict,
                "warnings": global_warnings,
            },
            "overall": overall,
            "scenes": scene_reports,
        }

        out_path = self.store.save_json("05_qc/qc_report.json", report)
        
        # 保存被修改状态的场景树
        if self.state:
            self.state.save()

        return {"qc_report_path": out_path, "verdict": global_verdict, "warnings": global_warnings}

def run(step_ctx: Dict[str, Any]) -> Dict[str, Any]:
    return QCStep(step_ctx).run()
