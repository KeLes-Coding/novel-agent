# src/utils/graph_parser.py
import json
from typing import List, Dict, Any, Tuple


class GraphParser:
    """
    负责解析大纲/分场表的结构，并进行逻辑一致性检查。
    Phase 3.1: 初步实现线性逻辑检查。
    """

    @staticmethod
    def parse_scene_plan(file_path: str) -> List[Dict[str, Any]]:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("scenes", [])

    @staticmethod
    def validate_logic(scenes: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """
        检查场景列表的逻辑一致性。
        返回: (是否通过, 错误/警告列表)
        """
        warnings = []
        passed = True

        # 1. ID 连续性检查
        ids = [s.get("id") for s in scenes]
        if not ids:
            return False, ["Scene list is empty"]

        # 检查是否缺失
        expected_ids = set(range(1, len(ids) + 1))
        actual_ids = set(ids)
        missing = expected_ids - actual_ids
        if missing:
            warnings.append(f"Missing scene IDs: {missing}")
            # 不阻断，但标记

        # 2. 核心字段检查 (Precondition Check)
        required_fields = ["goal", "conflict", "characters"]
        for s in scenes:
            sid = s.get("id")
            for field in required_fields:
                val = s.get(field)
                if not val or (isinstance(val, list) and len(val) == 0):
                    warnings.append(
                        f"Scene {sid} missing required logic field: '{field}'"
                    )
                    # 强逻辑错误，视情况可设为 False

            # 3. 简单的时间/因果流检查 (Heuristic)
            # 如果上一章有 cliffhanger，这一章最好能接上（这里很难通过规则硬性判断，只能做简单的文本存在性检查）
            pass

        return passed, warnings
