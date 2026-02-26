import pytest

@pytest.mark.bible
def test_bible_structure(bible_data):
    """Verify the bible (characters/world) data structure."""
    assert bible_data is not None, "Bible data is missing"
    
    # Check for YAML-like structure or specific keys if it's raw text
    # The existing fixture shows it contains "characters:", "world:", etc.
    assert "characters:" in bible_data or "characters" in bible_data
    assert "world:" in bible_data or "setting" in bible_data

@pytest.mark.bible
def test_bible_content(bible_data):
    """Verify specific required fields in the bible."""
    # Check for Main Character (mc)
    assert "mc:" in bible_data or "protagonist" in bible_data.lower()
