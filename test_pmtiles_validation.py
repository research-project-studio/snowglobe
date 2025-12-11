#!/usr/bin/env python3
"""Test script to validate PMTiles files from NYC parking archive."""

from pathlib import Path
import json
import sys

# Add CLI package to path
sys.path.insert(0, str(Path(__file__).parent / "cli" / "src"))

from webmap_archiver import validate_pmtiles


def main():
    # PMTiles files from NYC parking archive
    pmtiles_files = [
        Path("nyc-parking-final/tiles/maptiler-385.pbf.pmtiles"),
        Path("nyc-parking-final/tiles/wxy-labs-parking_regs_v2.pmtiles"),
    ]

    for pmtiles_path in pmtiles_files:
        if not pmtiles_path.exists():
            print(f"❌ File not found: {pmtiles_path}")
            continue

        print(f"\n{'='*70}")
        print(f"Validating: {pmtiles_path.name}")
        print(f"{'='*70}")

        result = validate_pmtiles(pmtiles_path)

        if not result.get("valid"):
            print(f"❌ Validation failed: {result.get('error')}")
            continue

        print(f"✓ Valid PMTiles file")
        print(f"\nHeader Information:")
        print(f"  Tile Type: {result['tile_type']}")
        print(f"  Compression: {result['tile_compression']}")
        print(f"  Zoom Range: {result['min_zoom']} - {result['max_zoom']}")
        print(f"  Tile Count: {result['tile_count']}")

        print(f"\nBounds:")
        bounds = result['bounds']
        print(f"  West:  {bounds['west']:.6f}")
        print(f"  South: {bounds['south']:.6f}")
        print(f"  East:  {bounds['east']:.6f}")
        print(f"  North: {bounds['north']:.6f}")

        print(f"\nCenter:")
        center = result['center']
        print(f"  Lon:  {center['lon']:.6f}")
        print(f"  Lat:  {center['lat']:.6f}")
        print(f"  Zoom: {center['zoom']}")

        if result.get('metadata'):
            print(f"\nMetadata:")
            for key, value in result['metadata'].items():
                print(f"  {key}: {value}")

        if result.get('sample_tile'):
            print(f"\nSample Tile:")
            sample = result['sample_tile']
            if 'error' in sample:
                print(f"  Error reading tile: {sample['error']}")
            else:
                print(f"  Tile ID: {sample['tile_id']}")
                print(f"  Size: {sample['size']:,} bytes")
                print(f"  First 10 bytes: {sample['first_10_bytes']}")
                print(f"  Is Gzipped: {sample['is_gzipped']}")

                # Check for double compression issue
                if sample['is_gzipped']:
                    import gzip
                    # Try to decompress
                    try:
                        # Read the actual tile data to check
                        with open(pmtiles_path, 'rb') as f:
                            from pmtiles.reader import Reader, MmapSource
                            source = MmapSource(f.fileno())
                            reader = Reader(source)
                            tile_data = reader.get_tile(sample['tile_id'])

                            # Decompress once
                            decompressed = gzip.decompress(tile_data)

                            # Check if decompressed data is ALSO gzipped
                            if len(decompressed) >= 2 and decompressed[:2] == b'\x1f\x8b':
                                print(f"  ⚠️  WARNING: DOUBLE COMPRESSION DETECTED!")
                                print(f"     Tile is gzipped twice - this will prevent loading")
                            else:
                                print(f"  ✓ Compression OK (not double-compressed)")
                    except Exception as e:
                        print(f"  Could not check for double compression: {e}")

    print(f"\n{'='*70}")
    print("Validation complete")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
