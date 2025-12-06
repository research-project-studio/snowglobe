# WebMap Archiver

Transform HAR files into self-contained web map archives, preserving vector and raster tiles, styles, and all dependencies in a portable format.

## Installation

```bash
# Create conda environment
conda create -n webmap-archiver python=3.10
conda activate webmap-archiver

# Install package in development mode
pip install -e .

# Install development dependencies
pip install -e ".[dev]"
```

## Usage

### Create an archive from a HAR file

```bash
webmap-archive create input.har -o output.zip -n "My Map Archive"
```

### Inspect a HAR file without creating an archive

```bash
webmap-archive inspect input.har
```

## Phase 1 MVP Features

- Parse HAR files and extract all entries with content
- Detect and classify tile requests (vector/raster, basemap/overlay)
- Extract tile coordinates from URLs
- Build PMTiles archives from captured tiles
- Handle "orphan" data layers (tile sources in HAR but not in style.json)
- Extract styling from JavaScript files
- Generate MapLibre HTML viewer with extracted or default styling
- Output ZIP archive with manifest

## Archive Structure

```
archive.zip
├── manifest.json              # Archive metadata
├── viewer.html                # Self-contained HTML viewer
├── style/
│   └── extracted_layers.json  # Extracted styling (editable)
└── tiles/
    ├── source1.pmtiles
    └── source2.pmtiles
```

## Known Limitations (Phase 1)

1. **Style extraction is best-effort** - Complex MapLibre expressions may be simplified
2. **No sprite/glyph bundling** - Text labels and icons won't render
3. **No tile fetching** - Only tiles present in HAR are archived
4. **FULL mode only** - No DATA_ONLY, STYLE_ONLY, or HYBRID modes yet
5. **Vector tiles focus** - Raster tile support is present but less tested

## License

MIT
