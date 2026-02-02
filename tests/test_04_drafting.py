import pytest

@pytest.mark.drafting
def test_scene_plan_validity(scene_plan_data):
    """Verify the scene plan JSON structure."""
    assert scene_plan_data is not None
    assert "scenes" in scene_plan_data
    assert isinstance(scene_plan_data["scenes"], list)
    assert len(scene_plan_data["scenes"]) > 0

# Use pytest_generate_tests mechanism locally or iterate
# To support parallel execution of scenes, we need to categorize them as separate tests.
# Since we loaded 'scenes_data' as a list in conftest, we can use it here.

def pytest_generate_tests(metafunc):
    if "scene_item" in metafunc.fixturenames:
        # Load data directly here since metafunc runs at collection time
        # But we can't easily access the session fixture 'scenes_data' inside generate_tests
        # So we have to re-load or assume conftest logic.
        # Better approach: access the data via a helper or just rely on the fixture being passed if we iterate.
        # But for true parallelism (xdist), we need parametrization.
        
        import json
        import os
        # Re-read fixtures for parametrization (a bit inefficient but safe)
        data_path = os.path.join(os.path.dirname(__file__), "data", "fixtures.json")
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                scenes = data.get("scenes", [])
                metafunc.parametrize("scene_item", scenes)
        else:
             metafunc.parametrize("scene_item", [])

@pytest.mark.drafting
def test_scene_drafting_quality(scene_item):
    """Test each drafted scene individually. 
    This allows parallel execution using pytest-xdist."""
    
    assert "content" in scene_item, f"Scene {scene_item.get('id')} missing content"
    content = scene_item["content"]
    
    # Basic quality checks
    assert len(content) > 500, f"Scene {scene_item.get('title')} content is too short"
    assert "林风" in content or "林七" in content, "Main character not found in scene" # hardcoded from known data
    
    # Check for completeness (e.g. no cut-off sentences at the very end?)
    # or just simple extraction check.
