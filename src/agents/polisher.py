import os
from typing import Dict, Any, List
from providers.base import LLMProvider

class PolisherAgent:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        
    def polish(self, original_text: str, critique: Dict[str, Any], style_guide: str = "", style_examples: List[str] = None, output_path: str = None) -> str:
        """
        Refine the text based on critique and style guide.
        """
        
        system_prompt = """
        你是一位金牌网文精修师（Polisher）。
        你的任务是根据【主编的修改意见】和【目标风格】，对【原始内容】进行润色和重写。
        
        **工作原则**：
        1. **保留剧情**：不要改变原有的剧情走向和核心冲突。
        2. **提升质感**：优化描写，消除“由于...所以...”等说明性文字，改用动作和神态表现。
        3. **解决问题**：针对主编提出的每一个 issue 进行定点爆破。
        4. **风格统一**：确保文字风格符合目标要求（如：热血简练、或古风唯美）。
        
        请直接输出修改后的正文，不要包含任何“好的”、“如下所示”等废话。
        """
        
        issues_text = "\n".join([f"- {i}" for i in critique.get("issues", [])])
        suggestions_text = "\n".join([f"- {s}" for s in critique.get("suggestions", [])])
        
        examples_text = ""
        if style_examples:
            examples_text = "\n【目标风格参考段落】\n（请仔细体会以下段落的语感、断句模式和描写重点，确保修改后的文本无限贴近这种风格）：\n"
            for i, ex in enumerate(style_examples, 1):
                examples_text += f"参考段落 {i}：\n{ex}\n\n"
        
        user_prompt = f"""
        【主编意见】
        评分：{critique.get('score')}
        问题点：
        {issues_text}
        修改建议：
        {suggestions_text}
        
        【目标风格补充】
        {style_guide}
        {examples_text}
        
        【原始内容】
        {original_text}
        
        请开始精修：
        """
        
        try:
            if hasattr(self.provider, "stream_generate"):
                full_text = ""
                with open(output_path, "w", encoding="utf-8") if output_path else open(os.devnull, "w") as f:
                    for chunk in self.provider.stream_generate(system=system_prompt, prompt=user_prompt):
                        f.write(chunk)
                        f.flush()
                        full_text += chunk
                return full_text.strip()
            else:
                response = self.provider.generate(system=system_prompt, prompt=user_prompt)
                full_text = response.text.strip()
                if output_path:
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(full_text)
                return full_text
            
        except Exception as e:
            print(f"[PolisherAgent] Polish failed: {e}")
            return original_text
