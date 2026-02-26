# src/pipeline/base_step.py
from abc import ABC, abstractmethod
from typing import Dict, Any

class PipelineStep(ABC):
    """
    Standard interface for all pipeline steps.
    """
    def __init__(self, context: Dict[str, Any]):
        self.context = context
        self.state = context.get("state")
        self.store = context.get("store")
        self.provider = context.get("provider")
        self.prompts = context.get("prompts", {})
        self.workflow = context.get("workflow")
        self.log = context.get("log")
        self.cfg = context.get("cfg", {})
    
    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """
        Execute the pipeline step and return a dictionary of results.
        """
        pass
