#!/usr/bin/env python3
"""Test captured style handling in API."""

from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent / "cli" / "src"))

from webmap_archiver import create_archive_from_bundle

# Create a minimal test bundle with a captured style
test_bundle = {
    "version": "1.0",
    "metadata": {
        "url": "https://test.example.com/map",
        "title": "Test Map with Captured Style",
        "capturedAt": "2025-12-11T20:00:00Z",
        "userAgent": "Test"
    },
    "viewport": {
        "center": [-74, 40.7],
        "zoom": 12,
        "bounds": [[-74.1, 40.6], [-73.9, 40.8]]
    },
    # Captured style from map.getStyle()
    "style": {
        "version": 8,
        "name": "Test Style",
        "sources": {
            "test-source": {
                "type": "vector",
                "url": "https://example.com/tiles/{z}/{x}/{y}.mvt"
            }
        },
        "layers": [
            {
                "id": "background",
                "type": "background",
                "paint": {"background-color": "#333"}
            },
            {
                "id": "test-layer",
                "type": "fill",
                "source": "test-source",
                "source-layer": "test",
                "paint": {"fill-color": "#ff0000"}
            }
        ]
    },
    "tiles": [
        {
            "z": 12,
            "x": 1205,
            "y": 1539,
            "sourceId": "test-source",
            "data": "GgAAAAA=",  # minimal MVT tile
        }
    ]
}

print("Testing captured style handling...")
print(f"Bundle has style: {bool(test_bundle.get('style'))}")
print(f"Style has {len(test_bundle['style']['sources'])} sources")
print(f"Style has {len(test_bundle['style']['layers'])} layers")

output_path = Path("test-captured-style-output.zip")

try:
    result = create_archive_from_bundle(
        bundle=test_bundle,
        output_path=output_path,
        verbose=True,
    )

    print(f"\n✓ Archive created: {result.output_path}")
    print(f"  Size: {result.size:,} bytes")
    print(f"  Tiles: {result.tile_count}")

    # Check if style was saved
    import zipfile
    with zipfile.ZipFile(output_path, 'r') as zf:
        files = zf.namelist()
        has_captured_style = any('captured_style' in f for f in files)
        print(f"  Has captured_style.json: {has_captured_style}")

        if has_captured_style:
            print("\n✓ Phase 3 implementation successful!")
        else:
            print("\n✗ captured_style.json not found in archive")

except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
