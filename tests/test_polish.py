import os
import sys

# 添加 src 到路径
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from core.manager import ProjectManager
from interfaces.cli import CLIInterface

def test_polish():
    # Load an existing run
    interface = CLIInterface()
    manager = ProjectManager("config/config.yaml", interface, "2026-02-13/22-15-57_6d49d759")

    # Grab the first done scene, run polish cycle just for it.
    done_scenes = [s for s in manager.state.scenes if s.status == "done"]
    if done_scenes:
        target_scene = done_scenes[0]
        # Make sure the scene JSON exists
        print(f"Testing polish on scene ID {target_scene.id}")
        workflow = manager._get_workflow("polishing")
        workflow.run_polish_cycle(target_scene)
        
        print(f"Check 06_polishing/diffs for scene {target_scene.id}")
    else:
        print("No done scenes found.")

if __name__ == "__main__":
    test_polish()
