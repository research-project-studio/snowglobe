# WebMap Archiver

Transform web maps into self-contained archives. Supports both HAR files (CLI workflow) and capture bundles (browser extension workflow), preserving vector and raster tiles, styles, and all dependencies in a portable format.

## Installation

```bash
# Create conda environment
conda create -n webmap-archiver python=3.12
conda activate webmap-archiver

# Install package in development mode
pip install -e .

# Install development dependencies
pip install -e ".[dev]"
```

## CLI Commands

### `create` - Create archive from HAR file

Create an archive from a HAR (HTTP Archive) file captured from browser DevTools.

```bash
webmap-archive create input.har -o output.zip -n "My Map Archive"
```

**Options:**
- `-o, --output PATH` - Output ZIP path (default: `<input>.zip`)
- `-n, --name TEXT` - Archive name (default: derived from filename)
- `-v, --verbose` - Verbose output
- `--style-override PATH` - JSON file with complete MapLibre style from `map.getStyle()`
- `--mode [standalone|original|full]` - Archive mode (default: `full`)
  - `standalone` - Viewer HTML + PMTiles only (minimal, self-contained)
  - `original` - Original site files + serve.py (for exact reproduction)
  - `full` - Both standalone and original (maximum flexibility)
- `--expand-coverage` - Fill gaps in captured tile coverage
- `--expand-zoom N` - Expand coverage by N additional zoom levels
- `--rate-limit FLOAT` - Rate limit for tile fetching (requests/sec, default: 10)

**Example with advanced options:**
```bash
webmap-archive create map.har \
  -o archive.zip \
  --style-override style.json \
  --expand-coverage \
  --mode standalone \
  --verbose
```

### `process` - Process capture bundle

Process a capture bundle (JSON) created by the browser extension into an archive.

```bash
webmap-archive process bundle.json -o output.zip
```

**Options:**
- `-o, --output PATH` - Output ZIP path (default: `<input>.zip`)
- `-v, --verbose` - Verbose output

**Example:**
```bash
webmap-archive process nyc-parking.json -o nyc-parking.zip --verbose
```

The bundle should follow the v1.0 capture bundle format with:
- `version`: "1.0"
- `metadata`: URL, title, timestamp
- `viewport`: Center, zoom, bounds
- `tiles`: Array of captured tiles with coordinates and data
- `style`: Optional MapLibre style object
- `har`: Optional HAR log for additional resources

### `inspect` - Analyze HAR file

Analyze a HAR file without creating an archive. Shows detected tiles, sources, coverage, and styling information.

```bash
webmap-archive inspect input.har
```

**Options:**
- `--show-urls` - Display full tile URLs
- `--show-style` - Display detected style information
- `--show-layers` - Show layer names from vector tiles

**Example:**
```bash
webmap-archive inspect map.har --show-layers --show-style
```

### `capture-style-help` - Style capture instructions

Show step-by-step instructions for capturing a map's style from browser DevTools console.

```bash
webmap-archive capture-style-help
```

This displays JavaScript code to copy-paste into the browser console to extract the complete MapLibre/Mapbox style object.

## Programmatic API

The package provides a clean public API for use in Python code, Modal functions, or other tools:

```python
from webmap_archiver import (
    create_archive_from_bundle,
    create_archive_from_har,
    inspect_bundle,
    normalize_bundle,
    ArchiveResult,
)

# Process a capture bundle
result = create_archive_from_bundle(
    bundle=bundle_dict,
    output_path=Path("output.zip"),
    name="My Archive",
    verbose=True
)

print(f"Created archive with {result.tile_count} tiles")
print(f"Size: {result.size:,} bytes")
print(f"Sources: {len(result.tile_sources)}")

# Inspect a bundle before processing
inspection = inspect_bundle(bundle_dict)
if inspection.is_valid:
    print(f"Valid bundle with {inspection.tile_count} tiles")
else:
    print(f"Errors: {inspection.errors}")

# Process HAR file
result = create_archive_from_har(
    har_path=Path("map.har"),
    output_path=Path("archive.zip"),
    verbose=True
)
```

**Available API Functions:**
- `create_archive_from_bundle(bundle, output_path, *, name, mode, verbose)` → `ArchiveResult`
- `create_archive_from_har(har_path, output_path, *, name, mode, style_override, verbose)` → `ArchiveResult`
- `inspect_bundle(bundle)` → `InspectionResult`
- `normalize_bundle(bundle)` → `dict`

**Data Classes:**
- `ArchiveResult` - Information about created archive (path, size, tiles, sources, bounds)
- `TileSourceResult` - Details about a tile source (name, count, zoom range, type, layers)
- `InspectionResult` - Validation results (valid, errors, warnings, metadata)

## Archive Structure

```
archive.zip
├── manifest.json              # Archive metadata and source info
├── viewer.html                # Self-contained HTML viewer (MapLibre GL JS)
└── tiles/
    ├── source1.pmtiles       # PMTiles archive for each source
    └── source2.pmtiles
```

**Manifest includes:**
- Archive name, description, creation date
- Bounding box and zoom range
- Tile source information (type, format, count, zoom range)
- Discovered source layers for vector tiles

**Viewer features:**
- Embedded MapLibre GL JS (no CDN dependencies)
- Automatic PMTiles protocol registration
- Orphan layer detection and styling
- Responsive layout with layer controls

## Features

### Core Capabilities
- ✅ Parse HAR files and extract all entries
- ✅ Detect and classify tile requests (vector/raster)
- ✅ Extract tile coordinates from various URL patterns
- ✅ Build PMTiles archives from captured tiles
- ✅ Automatic source layer discovery via MVT protobuf parsing
- ✅ Handle orphan data layers (tiles not in style.json)
- ✅ Generate self-contained MapLibre HTML viewer
- ✅ Process browser extension capture bundles
- ✅ Comprehensive manifest with metadata

### Advanced Features
- ✅ Coverage expansion (fill gaps + additional zoom levels)
- ✅ Rate-limited tile fetching with async/await
- ✅ Multi-source support (basemaps + overlays)
- ✅ Style override from `map.getStyle()`
- ✅ Three archive modes (standalone/original/full)
- ✅ Sprite and glyph bundling for text/icons
- ✅ Original site preservation with serve.py

### API Layer (v0.2.0)
- ✅ Clean public API for programmatic use
- ✅ Single source of truth for archive building
- ✅ Automatic layer discovery and metadata
- ✅ Validation and normalization utilities
- ✅ Modal/serverless-ready

## Workflow Examples

### Browser Extension → Archive

1. Use the WebMap Archiver browser extension to capture a map
2. Download the capture bundle JSON
3. Process with CLI:
   ```bash
   webmap-archive process capture.json -o archive.zip
   ```

### DevTools HAR → Archive

1. Open browser DevTools → Network tab
2. Navigate to map page and interact (pan/zoom)
3. Right-click → Save all as HAR
4. Process with CLI:
   ```bash
   webmap-archive create map.har -o archive.zip --expand-coverage
   ```

### Python/Modal Workflow

```python
from webmap_archiver import create_archive_from_bundle

# In Modal function or Python script
result = create_archive_from_bundle(
    bundle=request_json,
    output_path=temp_path / "archive.zip",
    verbose=True
)

# Upload result.output_path to storage
```

## Testing

```bash
# Run all tests
pytest

# Run API tests specifically
pytest tests/test_api.py -v

# Run with coverage
pytest --cov=webmap_archiver --cov-report=html
```

## Version History

### v0.2.0 (Current)
- Added public API layer (`api.py`)
- Added `process` command for capture bundles
- Automatic source layer discovery from MVT tiles
- Improved bundle validation and normalization
- Ready for Modal/serverless deployment

### v0.1.0
- Initial HAR-based workflow
- PMTiles archive generation
- Coverage expansion
- Multi-mode archives (standalone/original/full)

## License

MIT
