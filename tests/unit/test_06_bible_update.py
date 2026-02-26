
import pytest
import os
import sys
import logging
from unittest.mock import MagicMock, ANY

# Setup paths
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.core.manager import ProjectManager
from src.core.state import ProjectState, SceneNode
from src.agents.wiki_updater import WikiUpdater

# Setup test logger
log_dir = os.path.join(os.path.dirname(__file__), "logger")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "test_bible_update.log")

logger = logging.getLogger("TestBibleUpdate")
logger.handlers = []
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True
)

# Mock Provider returning JSON
class MockProviderJSON:
    def __init__(self, json_str):
        self.json_str = json_str
        
    def generate(self, system, prompt, meta=None):
        logger.info(f"MockProviderJSON generating. Prompt len: {len(prompt)}")
        mock_res = MagicMock()
        mock_res.text = self.json_str
        return mock_res

@pytest.fixture
def manager_with_json_provider(tmp_path):
    run_dir = tmp_path / "test_run_bible"
    run_dir.mkdir()
    
    # Create a mock bible file
    bible_path = run_dir / "bible.md"
    bible_path.write_text("# Original Bible\n\n- Character A\n", encoding="utf-8")
    
    state = ProjectState(run_id="test_bible", run_dir=str(run_dir))
    state.bible_path = str(bible_path)
    state.outline_path = str(run_dir / "outline.md") # dummy
    
    # Mock return value for analyze_scene
    mock_json = """
    ```json
    {
        "summary": "This is a summary of the scene.",
        "new_facts": [
            "New Character: Bob (Merchant)",
            "Location: Hidden Cave"
        ]
    }
    ```
    """
    
    manager = ProjectManager.__new__(ProjectManager)
    manager.run_id = "test_bible"
    manager.state = state
    manager.logger_env = {"logger": logger}
    manager.wiki_updater = WikiUpdater(MockProviderJSON(mock_json), "sys_prompt")
    manager.store = MagicMock() # mock store
    manager.store._abs.side_effect = lambda p: p
    manager.ctx_builder = MagicMock()
    manager.ctx_builder.build.return_value = {"payload": {}}
    manager.workflow = MagicMock() # mock workflow engine
    
    return manager

def test_bible_update_trigger(manager_with_json_provider):
    """Test that Scene execution triggers evaluate_scene and updates bible."""
    logger.info("=== TEST START: test_bible_update_trigger ===")
    
    manager = manager_with_json_provider
    
    # Create a mock scene node with content path
    scene = SceneNode(id=1, title="Encounter with Bob")
    scene_path = os.path.join(manager.state.run_dir, "scene_001.md")
    scene.content_path = scene_path
    
    # Create content file
    with open(scene_path, "w", encoding="utf-8") as f:
        f.write("Bob appeared in the hidden cave.")
        
    # We need to mock _consolidate_memory to avoid errors as we didn't mock everything for it
    manager._consolidate_memory = MagicMock()
    manager._handle_branches = MagicMock()
    
    logger.info("Step: Calling _process_scene_recursive")
    manager._process_scene_recursive(scene, auto_mode=True)
    
    # Verification
    # 1. Summary updated?
    assert scene.summary == "This is a summary of the scene."
    logger.info("✅ Verified: Scene summary updated.")
    
    # 2. Bible patched?
    with open(manager.state.bible_path, "r", encoding="utf-8") as f:
        bible_content = f.read()
        
    logger.info(f"Bible content after update:\n{bible_content}")
    
    assert "## [New] Dynamic Updates (Encounter with Bob)" in bible_content
    assert "- New Character: Bob (Merchant)" in bible_content
    assert "- Location: Hidden Cave" in bible_content
    logger.info("✅ Verified: Bible patched with new facts.")
    
    logger.info("=== TEST END: test_bible_update_trigger ===")
