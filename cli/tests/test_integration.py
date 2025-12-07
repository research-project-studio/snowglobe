"""
Integration tests for webmap-archiver using real HAR data.
"""

import pytest
from pathlib import Path

from webmap_archiver.har.parser import HARParser
from webmap_archiver.har.classifier import RequestClassifier, RequestType
from webmap_archiver.tiles.detector import TileDetector
from webmap_archiver.tiles.coverage import CoverageCalculator

FIXTURES = Path(__file__).parent / "fixtures"
HAR_FILE = FIXTURES / "parkingregulations.nyc.har"


@pytest.fixture
def har_entries():
    """Parse HAR file and return entries."""
    parser = HARParser(HAR_FILE)
    return parser.parse()


def test_har_file_exists():
    """Test that the HAR file exists."""
    assert HAR_FILE.exists(), f"HAR file not found at {HAR_FILE}"


def test_har_parsing(har_entries):
    """Test that HAR file parses correctly."""
    assert len(har_entries) > 0, "HAR file should contain entries"

    # Check that entries have content
    with_content = [e for e in har_entries if e.has_content]
    assert len(with_content) > 50, "HAR file should have many entries with content"

    # Check that entries are successful
    successful = [e for e in har_entries if e.is_successful]
    assert len(successful) > 50, "HAR file should have many successful responses"


def test_tile_classification(har_entries):
    """Test that tiles are classified correctly."""
    classifier = RequestClassifier()
    grouped = classifier.classify_all(har_entries)

    # Should find vector tiles
    vector_tiles = grouped[RequestType.VECTOR_TILE]
    assert len(vector_tiles) > 0, "Should find vector tiles"

    # May find other map resources
    style_json = grouped[RequestType.STYLE_JSON]
    sprites = grouped[RequestType.SPRITE_IMAGE]
    glyphs = grouped[RequestType.GLYPH]

    # Log what we found
    print(f"Found {len(vector_tiles)} vector tiles")
    print(f"Found {len(style_json)} style.json files")
    print(f"Found {len(sprites)} sprite images")
    print(f"Found {len(glyphs)} glyph files")


def test_tile_detection(har_entries):
    """Test that tile coordinates are extracted correctly."""
    classifier = RequestClassifier()
    grouped = classifier.classify_all(har_entries)

    detector = TileDetector()
    detected = []

    for entry in grouped[RequestType.VECTOR_TILE]:
        tile = detector.detect(entry.url, entry.content)
        if tile:
            detected.append(tile)

    assert len(detected) > 0, "Should detect tiles from URLs"

    # Check coordinate ranges
    zooms = set(t.coord.z for t in detected)
    assert len(zooms) > 0, "Should have tiles at various zoom levels"

    # Should group into sources
    sources = detector.group_by_source(detected)
    assert len(sources) > 0, "Should detect at least one tile source"

    # Log source information
    for template, (source, tiles) in sources.items():
        print(f"Source: {source.name}, {len(tiles)} tiles, type: {source.tile_type}")


def test_coverage_calculation(har_entries):
    """Test that geographic coverage is calculated correctly."""
    classifier = RequestClassifier()
    grouped = classifier.classify_all(har_entries)

    detector = TileDetector()
    detected = []

    for entry in grouped[RequestType.VECTOR_TILE]:
        tile = detector.detect(entry.url, entry.content)
        if tile:
            detected.append(tile)

    if not detected:
        pytest.skip("No tiles detected")

    coverage_calc = CoverageCalculator()
    coords = [t.coord for t in detected]

    # Calculate bounds
    bounds = coverage_calc.calculate_bounds(coords)
    assert -180 <= bounds.west <= 180
    assert -180 <= bounds.east <= 180
    assert -90 <= bounds.south <= 90
    assert -90 <= bounds.north <= 90
    assert bounds.west < bounds.east
    assert bounds.south < bounds.north

    # Calculate zoom range
    zoom_range = coverage_calc.get_zoom_range(coords)
    assert zoom_range[0] <= zoom_range[1]
    assert 0 <= zoom_range[0] <= 20
    assert 0 <= zoom_range[1] <= 20

    # Log coverage information
    print(f"Bounds: ({bounds.west:.4f}, {bounds.south:.4f}) to ({bounds.east:.4f}, {bounds.north:.4f})")
    print(f"Zoom range: {zoom_range[0]}-{zoom_range[1]}")
    print(f"Center: {bounds.center}")


def test_source_grouping(har_entries):
    """Test that tiles are grouped by source correctly."""
    classifier = RequestClassifier()
    grouped = classifier.classify_all(har_entries)

    detector = TileDetector()
    detected = []

    for entry in grouped[RequestType.VECTOR_TILE]:
        tile = detector.detect(entry.url, entry.content)
        if tile:
            detected.append(tile)

    if not detected:
        pytest.skip("No tiles detected")

    sources = detector.group_by_source(detected)
    assert len(sources) >= 1, "Should have at least one tile source"

    # Check that each source has tiles
    for template, (source, tiles) in sources.items():
        assert len(tiles) > 0, f"Source {source.name} should have tiles"
        assert source.tile_type in ["vector", "raster"]
        assert source.format in ["pbf", "mvt", "png", "jpg", "webp"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
