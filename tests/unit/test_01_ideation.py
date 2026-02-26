import pytest
import re

@pytest.mark.ideation
def test_ideation_structure(ideation_data):
    """Verify that the ideation content has the expected structure."""
    assert ideation_data is not None, "Ideation data is missing"
    
    # Check for presence of multiple options/candidates
    # Usually "方案 X" or "Option X"
    assert "方案" in ideation_data or "Option" in ideation_data
    
    # Check for Analysis section
    assert "深度评估" in ideation_data or "Analysis" in ideation_data or "建议" in ideation_data

@pytest.mark.ideation
def test_ideation_content_constraints(ideation_data):
    """Verify basic constraints of the generated ideas."""
    # Example: Check if it's not too short
    assert len(ideation_data) > 100, "Ideation content is suspiciously short"
    
    # Check for markdown formatting
    assert "#" in ideation_data, "Content should contain Markdown headers"
