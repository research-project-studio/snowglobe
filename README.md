# WebMap Archiver (Snowglobe)

**Version 0.3.0** - Archive web maps as self-contained, offline-viewable packages

## Overview

WebMap Archiver captures interactive web maps and packages them into self-contained archives with full style preservation, including sprites, glyphs, and programmatically-added layers. Archives work offline and can be shared as single ZIP files.

## Features

- **Chrome Extension**: One-click capture of map tiles, styles, sprites, and glyphs from any web map
- **Full Style Preservation**: Captures complete MapLibre/Mapbox GL JS styles including programmatically-added layers
- **Sprite & Glyph Bundling**: Automatically captures and bundles all map icons and fonts
- **Self-Contained Archives**: Single ZIP file containing tiles (PMTiles), viewer HTML, and all assets
- **Offline Viewing**: Archives work without internet connection
- **CLI Tool**: Process HAR files or capture bundles into archives
- **Modal Cloud Processing**: Optional cloud-based processing for extension captures

## Project Structure

- **`extension/`** - Chrome extension for capturing web maps from DevTools
- **`cli/`** - Python CLI tool and API for processing captures into archives
- **`docs/`** - Documentation and development guides

## Quick Start

### Using the Chrome Extension

1. Install the extension (see `extension/README.md`)
2. Open DevTools → WebMap Archiver panel
3. Navigate to a web map and wait for tiles to load
4. Click "Start Capture" and interact with the map
5. Click "Stop & Build Archive"
6. Download the generated ZIP file

### Using the CLI

```bash
# Install
pip install git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli

# Process a capture bundle
webmap-archive build capture.json output.zip

# Process a HAR file
webmap-archive build capture.har output.zip --mode har
```

## What's New in 0.3.0

- **Sprite/Glyph Support**: Full capture and bundling of map sprites (icons) and glyphs (fonts)
- **TileJSON Source Matching**: Enhanced support for basemap sources using TileJSON metadata URLs
- **Async Style Loading**: Fixes MapLibre URL validation issues with improved viewer architecture
- **Cleaner Output**: Reduced verbose logging throughout codebase

See [CHANGELOG.md](CHANGELOG.md) for complete release notes.

## Technical Details

Archives contain:
- **PMTiles**: Vector/raster tiles in single-file archive format
- **Viewer HTML**: Self-contained MapLibre GL JS viewer
- **Sprites**: Icon images (PNG) and metadata (JSON) for map symbols
- **Glyphs**: Font files (PBF) for text rendering
- **Style**: Complete MapLibre style JSON with sources rewritten to local PMTiles

## Documentation

- [Extension README](extension/README.md) - Chrome extension usage
- [CLI README](cli/README.md) - CLI tool and API documentation
- [CHANGELOG](CHANGELOG.md) - Version history and release notes

## Development

Requires:
- Python 3.10+
- Node.js 18+ (for extension)
- Modal account (optional, for cloud processing)

See individual component READMEs for development setup.


