from abc import ABC, abstractmethod
from typing import List, Any, Dict, Optional

class UserInterface(ABC):
    """
    用户交互的抽象基类 (Abstract Base Class)。
    将核心逻辑与 CLI/Web 实现解耦。
    """

    @abstractmethod
    def notify(self, title: str, message: str, payload: Optional[Dict[str, Any]] = None):
        """发送通知或状态更新给用户。"""
        pass

    @abstractmethod
    def prompt_input(self, prompt_text: str, default: Optional[str] = None) -> str:
        """请求用户输入自由文本。"""
        pass

    @abstractmethod
    def prompt_multiline(self, prompt_text: str) -> str:
        """请求用户输入多行文本 (以特定结束符终止)。"""
        pass

    @abstractmethod
    def ask_choice(self, prompt_text: str, options: List[str], descriptions: Optional[List[str]] = None) -> int:
        """
        请求用户从列表中选择一个选项。
        返回选中选项的索引 (0-based)。
        """
        pass

    @abstractmethod
    def confirm(self, prompt_text: str, default: bool = True) -> bool:
        """请求用户进行是/否确认。"""
        pass
