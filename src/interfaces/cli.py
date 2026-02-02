import sys
from typing import List, Dict, Optional, Any
from .base import UserInterface

class CLIInterface(UserInterface):
    """
    用户界面的标准命令行 (Command Line Interface) 实现。
    """

    def notify(self, title: str, message: str, payload: Optional[Dict[str, Any]] = None):
        print(f"\n=== [{title}] ===")
        print(message)
        if payload:
            # 简单的格式化输出
            import json
            try:
                print(f"详细信息: {json.dumps(payload, ensure_ascii=False, indent=2)}")
            except:
                print(f"详细信息: {payload}")
        print("==================\n")

    def prompt_input(self, prompt_text: str, default: Optional[str] = None) -> str:
        p_str = f"{prompt_text} [{default}]: " if default else f"{prompt_text}: "
        user_in = input(p_str).strip()
        return user_in if user_in else (default or "")

    def prompt_multiline(self, prompt_text: str) -> str:
        print(f"\n{prompt_text} (输入 'END' 单独一行结束):")
        lines = []
        while True:
            try:
                line = input()
                if line.strip() == 'END':
                    break
                lines.append(line)
            except EOFError:
                break
        return "\n".join(lines)

    def ask_choice(self, prompt_text: str, options: List[str], descriptions: Optional[List[str]] = None) -> int:
        print(f"\n{prompt_text}")
        for i, opt in enumerate(options):
            desc = f" - {descriptions[i]}" if descriptions and i < len(descriptions) else ""
            print(f"  {i+1}. {opt}{desc}")
        
        while True:
            choice = input(f"请选择 (1-{len(options)}): ").strip()
            if choice.isdigit():
                val = int(choice)
                if 1 <= val <= len(options):
                    return val - 1
            print("无效的选择，请重试。")

    def confirm(self, prompt_text: str, default: bool = True) -> bool:
        choice_str = "[Y/n]" if default else "[y/N]"
        user_in = input(f"{prompt_text} {choice_str}: ").strip().lower()
        if not user_in:
            return default
        return user_in in ("y", "yes", "1", "true", "是", "确认")
