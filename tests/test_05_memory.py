
import pytest
import os
import sys
import logging
from unittest.mock import MagicMock, ANY

# Add src to python path for imports like 'from utils.logger import ...' to work
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
# And ensure root is also there if needed for 'src.core'
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.core.manager import ProjectManager
from src.core.state import ProjectState, SceneNode
from src.core.context import ContextBuilder
from src.agents.wiki_updater import WikiUpdater

# Setup test logger
log_dir = os.path.join(os.path.dirname(__file__), "logger")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "test_memory.log")

# Reset logger handlers to avoid duplication if run multiple times
logger = logging.getLogger("TestMemory")
logger.handlers = []
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True
)

# Mock Provider for WikiUpdater
class MockProvider:
    def generate(self, system, prompt, meta=None):
        logger.info(f"MockProvider generated request. Prompt len: {len(prompt)}")
        mock_res = MagicMock()
        mock_res.text = "【Chapter Summary】Mocked consolidation calculation."
        return mock_res

@pytest.fixture
def mock_manager(tmp_path):
    logger.info("Box: Setting up Mock Manager")
    
    # Use tmp_path fixture which provides a valid path object
    run_dir = tmp_path / "test_run"
    run_dir.mkdir()
    
    state = ProjectState(run_id="test_run", run_dir=str(run_dir))
    # Populate scenes 1 to 20
    state.scenes = []
    for i in range(1, 21): 
        node = SceneNode(id=i, title=f"Scene {i}")
        node.summary = f"Summary for scene {i}"
        node.status = "done"
        state.scenes.append(node)
        
    manager = ProjectManager.__new__(ProjectManager)
    manager.run_id = "test_run"
    manager.state = state
    # Assign the test logger to logger_env so manager.log property works
    manager.logger_env = {"logger": logger}
    manager.wiki_updater = WikiUpdater(MockProvider(), "sys_prompt")
    
    return manager

def test_memory_consolidation_trigger(mock_manager):
    """Test that memory consolidation is triggered correctly."""
    logger.info("=== TEST START: test_memory_consolidation_trigger ===")
    manager = mock_manager
    state = manager.state
    
    # 1. Initial Check
    assert len(state.archived_summaries) == 0
    assert state.last_archived_scene_id == 0
    
    # 2. Scene 5 finished -> Diff is 5. Threshold 10. No trigger.
    logger.info("Step: Call _consolidate_memory(5)")
    manager._consolidate_memory(5)
    assert len(state.archived_summaries) == 0
    
    # 3. Scene 10 finished -> Diff is 10. Trigger archive for 1-5.
    logger.info("Step: Call _consolidate_memory(10)")
    manager._consolidate_memory(10)
    
    # Check results
    assert len(state.archived_summaries) == 1
    assert state.last_archived_scene_id == 5
    assert "Mocked consolidation calculation" in state.archived_summaries[0]
    logger.info("✅ Verified: Archive triggered at scene 10")
    
    # 4. Scene 14 finished -> Diff (14-5)=9. No trigger.
    logger.info("Step: Call _consolidate_memory(14)")
    manager._consolidate_memory(14)
    assert len(state.archived_summaries) == 1
    
    # 5. Scene 15 finished -> Diff (15-5)=10. Trigger archive for 6-10.
    logger.info("Step: Call _consolidate_memory(15)")
    manager._consolidate_memory(15)
    
    assert len(state.archived_summaries) == 2
    assert state.last_archived_scene_id == 10
    logger.info("✅ Verified: Archive triggered at scene 15")
    
    logger.info("=== TEST END: test_memory_consolidation_trigger ===")

def test_context_assembly_with_memory():
    """Test ContextBuilder puts archived and recent summaries into prompt."""
    logger.info("=== TEST START: test_context_assembly_with_memory ===")
    
    # Setup State manually
    state = ProjectState(run_id="test", run_dir="/tmp")
    state.archived_summaries = ["Summary Vol 1", "Summary Vol 2"]
    state.last_archived_scene_id = 10
    
    state.scenes = []
    for i in range(1, 16):
        node = SceneNode(id=i, title=f"Scene {i}")
        node.summary = f"Scene Summary {i}"
        state.scenes.append(node)
        
    store = MagicMock()
    # mock abs path just returning the string
    store._abs.side_effect = lambda p: p 
    
    # Mock ContextBuilder's load_best_content to avoid disk I/O
    builder = ContextBuilder(state, store)
    builder._load_best_content = MagicMock(return_value="Mock Content")
    
    logger.info("Step: Build context for Scene 13")
    res = builder.build(13)
    payload = res["payload"]
    prev_context = payload["prev_context"]
    
    logger.info(f"Generated prev_context preview:\n{prev_context[:200]}...")
    
    # Verification
    # 1. Archives should be present
    assert "Summary Vol 1" in prev_context
    assert "Summary Vol 2" in prev_context
    logger.info("✅ Verified: Archives present")
    
    # 2. Recent scenes (11, 12) should be present
    # Range: last_archived(10) + 1 = 11 use to scene_id(13) - 1 = 12
    assert "Scene Summary 11" in prev_context
    assert "Scene Summary 12" in prev_context
    logger.info("✅ Verified: Recent scenes present")
    
    # 3. Exclusions
    # Scene 10 is archived, so its raw summary should NOT be in 'Recent' section
    # (Though logic might not explicitly forbid it if regex matched, but here we check existence)
    # Actually, our code puts "Summary Vol 1" etc.
    # The raw "Scene Summary 10" should not be in the recent part.
    assert "Scene Summary 10" not in prev_context
    logger.info("✅ Verified: Archived scene 10 not in recent list")
    
    # Scene 13 is current, should not be in prev_context
    assert "Scene Summary 13" not in prev_context
    logger.info("✅ Verified: Current scene 13 not in prev_context")
    
    logger.info("=== TEST END: test_context_assembly_with_memory ===")
