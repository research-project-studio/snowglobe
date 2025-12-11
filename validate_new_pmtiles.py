#!/usr/bin/env python3
"""Validate the newly created PMTiles files."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "cli" / "src"))

from webmap_archiver import validate_pmtiles

pmtiles_files = [
    Path("nyc-parking-test-phase1/tiles/maptiler-385.pbf.pmtiles"),
    Path("nyc-parking-test-phase1/tiles/wxy-labs-parking_regs_v2.pmtiles"),
]

for pmtiles_path in pmtiles_files:
    print(f"\n{'='*70}")
    print(f"Validating: {pmtiles_path.name}")
    print(f"{'='*70}")

    result = validate_pmtiles(pmtiles_path)

    if not result.get("valid"):
        print(f"❌ Validation failed: {result.get('error')}")
        continue

    print(f"✓ Valid PMTiles file")
    print(f"  Tile Type: {result['tile_type']}")
    print(f"  Compression: {result['tile_compression']}")
    print(f"  Zoom Range: {result['min_zoom']} - {result['max_zoom']}")
    print(f"  Tile Count: {result['tile_count']}")
    print(f"  Bounds: {result['bounds']}")

print(f"\n{'='*70}")
print("Validation complete - Ready for pmtiles.io testing")
print(f"{'='*70}\n")
