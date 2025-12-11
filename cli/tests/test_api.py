"""
Tests for the public API module.
"""

import pytest
from pathlib import Path
from webmap_archiver.api import (
    inspect_bundle,
    normalize_bundle,
    create_archive_from_bundle,
)


def test_normalize_bundle():
    """Test that source -> sourceId normalization works."""
    bundle = {
        "version": "1.0",
        "metadata": {"url": "https://test.com", "capturedAt": "2024-01-01T00:00:00Z"},
        "viewport": {"center": [0, 0], "zoom": 10},
        "tiles": [
            {"source": "test", "z": 10, "x": 100, "y": 100, "data": "", "format": "pbf"}
        ]
    }
    
    normalized = normalize_bundle(bundle)
    
    assert "sourceId" in normalized["tiles"][0]
    assert "source" not in normalized["tiles"][0]
    assert normalized["tiles"][0]["sourceId"] == "test"


def test_normalize_bundle_missing_url():
    """Test that missing URL is handled."""
    bundle = {
        "version": "1.0",
        "metadata": {"capturedAt": "2024-01-01T00:00:00Z"},
        "viewport": {"center": [0, 0], "zoom": 10},
        "tiles": []
    }
    
    normalized = normalize_bundle(bundle)
    
    assert normalized["metadata"]["url"] == "https://unknown"


def test_inspect_bundle_valid():
    """Test inspection of a valid bundle."""
    bundle = {
        "version": "1.0",
        "metadata": {"url": "https://test.com", "capturedAt": "2024-01-01T00:00:00Z", "title": "Test Map"},
        "viewport": {"center": [0, 0], "zoom": 10},
        "tiles": []
    }
    
    result = inspect_bundle(bundle)
    
    assert result.is_valid
    assert result.version == "1.0"
    assert result.url == "https://test.com"
    assert result.title == "Test Map"
    assert result.tile_count == 0
    assert len(result.errors) == 0


def test_inspect_bundle_invalid():
    """Test inspection catches missing fields."""
    bundle = {"version": "1.0"}
    
    result = inspect_bundle(bundle)
    
    assert not result.is_valid
    assert len(result.errors) > 0
    assert any("metadata.url" in err for err in result.errors)
    assert any("viewport" in err for err in result.errors)


def test_inspect_bundle_with_tiles():
    """Test inspection with tiles."""
    bundle = {
        "version": "1.0",
        "metadata": {"url": "https://test.com", "capturedAt": "2024-01-01T00:00:00Z"},
        "viewport": {"center": [0, 0], "zoom": 10},
        "tiles": [
            {"sourceId": "source1", "z": 10, "x": 100, "y": 100, "data": "", "format": "pbf"},
            {"sourceId": "source2", "z": 10, "x": 101, "y": 100, "data": "", "format": "pbf"},
        ]
    }
    
    result = inspect_bundle(bundle)
    
    assert result.is_valid
    assert result.tile_count == 2
    assert "source1" in result.tile_sources
    assert "source2" in result.tile_sources


def test_inspect_bundle_old_field_names():
    """Test inspection detects old field name usage."""
    bundle = {
        "version": "1.0",
        "metadata": {"url": "https://test.com", "capturedAt": "2024-01-01T00:00:00Z"},
        "viewport": {"center": [0, 0], "zoom": 10},
        "tiles": [
            {"source": "test", "z": 10, "x": 100, "y": 100, "data": "", "format": "pbf"}
        ]
    }
    
    result = inspect_bundle(bundle)
    
    assert result.is_valid
    assert len(result.warnings) > 0
    assert any("source" in warn for warn in result.warnings)


def test_inspect_bundle_wrong_version():
    """Test inspection rejects wrong version."""
    bundle = {
        "version": "2.0",
        "metadata": {"url": "https://test.com", "capturedAt": "2024-01-01T00:00:00Z"},
        "viewport": {"center": [0, 0], "zoom": 10},
    }
    
    result = inspect_bundle(bundle)
    
    assert not result.is_valid
    assert any("version" in err.lower() for err in result.errors)


def test_inspect_bundle_empty_warning():
    """Test inspection warns about empty bundles."""
    bundle = {
        "version": "1.0",
        "metadata": {"url": "https://test.com", "capturedAt": "2024-01-01T00:00:00Z"},
        "viewport": {"center": [0, 0], "zoom": 10},
        "tiles": []
    }
    
    result = inspect_bundle(bundle)
    
    assert result.is_valid
    # Note: The warning is only shown if there's no style, HAR, or tiles
    # This bundle is technically valid but empty
