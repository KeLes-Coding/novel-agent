# src/agents/wiki_updater.py
from typing import Any, Dict
from providers.base import LLMProvider
import os


class WikiUpdater:
    def __init__(self, provider: LLMProvider, sys_prompt: str):
        self.provider = provider
        self.sys_prompt = sys_prompt

    def analyze_scene(self, text: str) -> Dict[str, Any]:
        """
        Analyze scene text to generate summary and extract new entities/facts.
        Returns a dict: {"summary": str, "new_facts": List[str]}
        """
        prompt = (
            "请分析以下小说章节，完成两个任务：\n"
            "1. **剧情摘要**：生成约100字的精炼摘要。\n"
            "2. **新设定提取**：提取文中**新出现**的或**发生重大变化**的关键实体（人物、地点、道具、势力）。"
            "如果没有新设定，该列表为空。\n\n"
            "请严格以 JSON 格式输出，格式如下：\n"
            "```json\n"
            "{\n"
            '  "summary": "...",\n'
            '  "new_facts": ["新人物：张三（铁匠）", "新地点：黑风寨", "状态变更：李四（重伤）"]\n'
            "}\n"
            "```\n\n"
            "【正文】\n" + text[:6000]  # 截断防止溢出
        )
        
        try:
            res = self.provider.generate(self.sys_prompt, prompt)
            # Simple heuristic to extract JSON if model wraps it in md code blocks
            content = res.text.strip()
            if "```json" in content:
                import re
                match = re.search(r"```json(.*?)```", content, re.DOTALL)
                if match:
                    content = match.group(1).strip()
            elif "```" in content:
                 match = re.search(r"```(.*?)```", content, re.DOTALL)
                 if match:
                    content = match.group(1).strip()
            
            import json
            data = json.loads(content)
            
            # Validation
            if "summary" not in data:
                data["summary"] = "Error: parsed JSON missing summary."
            if "new_facts" not in data:
                data["new_facts"] = []
                
            return data
            
        except Exception as e:
            # Fallback
            return {
                "summary": f"Analysis failed: {e}",
                "new_facts": []
            }

    def patch_bible(self, bible_path: str, new_facts: list[str], chapter_title: str, branch_id: str = None) -> str:
        """
        Append new facts to the bible file. Uses Copy-On-Write if branch_id is provided.
        Returns the path to the updated bible.
        """
        if not new_facts:
            return bible_path

        target_path = bible_path
        if branch_id:
            import shutil
            base, ext = os.path.splitext(bible_path)
            target_path = f"{base}_branch_{branch_id}{ext}"
            if not os.path.exists(target_path) and os.path.exists(bible_path):
                shutil.copy2(bible_path, target_path)

        if not os.path.exists(target_path):
             with open(target_path, "w", encoding="utf-8") as f:
                 f.write("# Project Bible\n\n")

        append_content = f"\n\n## [New] Dynamic Updates ({chapter_title})\n"
        for fact in new_facts:
            append_content += f"- {fact}\n"
            
        try:
            with open(target_path, "a", encoding="utf-8") as f:
                f.write(append_content)
            return target_path
        except Exception as e:
            print(f"Failed to patch bible: {e}") 
            return bible_path


    def consolidate_summaries(self, summaries: list[str]) -> str:
        """
        Merge multiple scene summaries into a cohesive chapter/arc summary.
        """
        if not summaries:
            return ""

        context_text = "\n".join([f"- Scene {i+1}: {s}" for i, s in enumerate(summaries)])
        
        prompt = (
            "请将以下【5个连续场景的剧情摘要】合并为一个【连贯的阶段性摘要】(约200字)。\n"
            "要求：\n"
            "1. 保留关键因果逻辑和伏笔。\n"
            "2. 忽略细枝末节，宏观概括剧情进展。\n"
            "3. 人名、地名准确无误。\n\n"
            "【原始场景摘要】\n"
            f"{context_text}\n"
        )
        
        try:
            res = self.provider.generate(self.sys_prompt, prompt)
            return res.text.strip()
        except Exception as e:
            # log error? We don't have logger here easily unless passed.
            # Return simple concatenation as fallback
            return f"Summary consolidation failed: {e}. Raw: " + " | ".join(summaries)
