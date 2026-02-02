import pytest
import json
import os
import sys

# Add src to python path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

@pytest.fixture(scope="session")
def fixtures_data():
    """Load the fixed test data extracted from the run."""
    data_path = os.path.join(os.path.dirname(__file__), "data", "fixtures.json")
    if not os.path.exists(data_path):
        pytest.skip("fixtures.json not found. Run extract_fixtures.py first.")
    
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture
def ideation_data(fixtures_data):
    return fixtures_data.get("ideation")

@pytest.fixture
def outline_data(fixtures_data):
    return fixtures_data.get("outline")

@pytest.fixture
def bible_data(fixtures_data):
    return fixtures_data.get("bible")

@pytest.fixture
def scene_plan_data(fixtures_data):
    return fixtures_data.get("scene_plan")

@pytest.fixture
def scenes_data(fixtures_data):
    return fixtures_data.get("scenes", [])
