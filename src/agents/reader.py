from typing import Dict, Any, List
from providers.base import LLMProvider

class ReaderAgent:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        
    def critique(self, content: str, mood: str = "critical") -> Dict[str, Any]:
        """
        Analyze the content and provide a critique.
        mood: 'critical' (harsh), 'constructive' (gentle), 'style_focused' (focus on style)
        """
        
        system_prompt = """
        你是一位资深的网文主编，以眼光毒辣、要求严格著称。
        你的任务是审阅作者提交的草稿，并指出其中的问题。
        
        请重点关注以下维度：
        1. 【节奏感/Pacing】：是否拖沓？是否有无效的灌水？
        2. 【画面感/Imagery】：描写是否具体？是否使用了“Show, Don't Tell”？
        3. 【人设/Character】：人物说话是否符合身份？有无OOC？
        4. 【期待感/Hook】：结尾是否有钩子？情绪是否到位？
        
        请输出 JSON 格式的评审报告，包含以下字段：
        - score: (0-10分)
        - summary: (简短评价)
        - issues: [List of strings, specific problems]
        - suggestions: [List of strings, how to fix]
        """
        
        user_prompt = f"""
        【待审阅正文】
        {content}
        
        请开始审阅，直接输出 JSON：
        """
        
        try:
            # Assume provider.generate returns an object with .text or similar
            # If provider handles JSON extraction, good. If not, we might need a parser.
            # For now, let's assume we get text and need to parse it, 
            # or usage of a structured output capability if available.
            # Using basic generate for now.
            
            response = self.provider.generate(system=system_prompt, prompt=user_prompt)
            # Simple JSON cleanup if needed
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:-3]
            elif text.startswith("```"):
                text = text[3:-3]
                
            import json
            data = json.loads(text)
            return data
            
        except Exception as e:
            print(f"[ReaderAgent] Critique failed: {e}")
            return {
                "score": 5.0,
                "summary": "自动审阅失败",
                "issues": [str(e)],
                "suggestions": []
            }
