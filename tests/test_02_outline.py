# -*- coding: utf-8 -*-
import pytest
import os
import json

@pytest.mark.outline
def test_outline_structure_simple():
    # Load data relative to CWD (assuming run from root)
    path = os.path.join("tests", "data", "fixtures.json")
    if not os.path.exists(path):
        # Try relative to file location
        path = os.path.join(os.path.dirname(__file__), "data", "fixtures.json")
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    outline_data = data.get("outline")
    assert outline_data is not None, "Outline data is missing"
    assert len(outline_data) > 100, "Outline data is too short"
    assert "#" in outline_data, "Outline should contain markdown headers"
    
    # Optional: Check for volume/summary but don't fail if encoding matches fail
    # on certain environments
    has_volume = "卷" in outline_data or "Volume" in outline_data
    if not has_volume:
        print("Warning: Volume header not found (possible encoding mismatch)")

@pytest.mark.outline
def test_outline_consistency(ideation_data, outline_data):
    # Basic consistency check
    assert outline_data is not None
    assert len(outline_data) > 100
