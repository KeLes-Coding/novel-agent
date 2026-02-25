import os
from typing import Dict, Any
from providers.base import LLMProvider

class AIBypassAgent:
    def __init__(self, provider: LLMProvider, prompts: Dict[str, Any]):
        self.provider = provider
        self.prompts = prompts.get("ai_bypass", {})
        
    def bypass(self, original_text: str, output_path: str = None) -> str:
        """
        Apply AI-bypass logic to the polished text to make it more human-like.
        """
        system_prompt = self.prompts.get("system", "你是一个人类作家，请重写去AI味。")
        user_template = self.prompts.get("user_template", "{content}")
        user_prompt = user_template.format(content=original_text)
        
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
            print(f"[AIBypassAgent] Bypass failed: {e}")
            return original_text
