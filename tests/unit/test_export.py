import os
import sys

# Add src to path
sys.path.insert(0, r"i:\WorkSpace\novel-agent\src")

from core.state import ProjectState
from storage.local_store import LocalStore
from core.fsm import StateMachine
from core.manager import ProjectManager


class MockInterface:
    def notify(self, title, msg, level="info"):
        print(f"[{level.upper()}] {title}: {msg}")
        
    def prompt(self, title, msg, **kwargs):
        print(f"[PROMPT] {title}: {msg}")
        return kwargs.get("default", True)

# we need a run_id that has finished scenes
run_id = "2026-02-13/22-15-57_6d49d759"
run_dir = os.path.abspath(f"runs/{run_id}")
store = LocalStore(os.path.join(run_dir, "artifacts"))
state = ProjectState.load(run_dir)

interface = MockInterface()
pm = ProjectManager(config_path="config/config.yaml", interface=interface, run_id=run_id)

print(f"Current Phase before export: {pm.fsm.current_phase}")
pm.run_export()
print(f"Current Phase after export: {pm.fsm.current_phase}")
