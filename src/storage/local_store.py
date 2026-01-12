# src/storage/local_store.py
import os
import json
from typing import Any, Dict, TextIO, Tuple


class LocalStore:
    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        self.art_dir = os.path.join(run_dir, "artifacts")
        os.makedirs(self.art_dir, exist_ok=True)

    def _abs(self, rel_path: str) -> str:
        # 统一处理用户传入的 "a/b/c.txt" 或 "a\b\c.txt"
        rel_path = rel_path.replace("/", os.sep).replace("\\", os.sep)
        return os.path.join(self.art_dir, rel_path)

    def save_text(self, rel_path: str, text: str) -> str:
        path = self._abs(rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return path

    def save_json(self, rel_path: str, obj: Dict[str, Any]) -> str:
        path = self._abs(rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return path

    def open_text(self, rel_path: str, mode: str = "w") -> Tuple[str, TextIO]:
        """
        用于流式写文件：返回 (abs_path, file_handle)
        mode 推荐用 "w" 或 "a"
        """
        path = self._abs(rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        f = open(path, mode, encoding="utf-8", newline="\n")
        return path, f
