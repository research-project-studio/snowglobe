# Changelog

All notable changes to the WebMap Archiver project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-12-16

### Added - Phase 1: Raster Tile Support
- **Raster Tile Archiving**: Full support for PNG, JPG, and WebP raster tiles
  - Raster tiles automatically detected via existing classifier patterns
  - Stored in PMTiles format (raster mode) alongside vector tiles
  - No double-compression applied to already-compressed images
  - Format detection from URL extension (.png, .jpg, .webp)
  - Tile type passed through processor → PMTiles builder → viewer
- **Raster Viewer Support**: Viewer now renders both raster and vector sources
  - Automatically generates raster layers for raster PMTiles
  - Raster sources configured with type: "raster" and tileSize: 256
  - Works in both generated style (no captured style) and captured style modes
  - Raster layers render with full opacity by default

### Changed
- **Version**: Bumped to 1.0.0 to reflect production-ready status with multi-tile-type support
- **PMTiles Builder**: Already supported raster formats (PNG/JPEG/WebP) - verified working
- **Tile Processing**: Processor already infers tile type (raster vs vector) from URL and content
- **Viewer Generator**: Updated to handle both vector and raster sources in style generation

### Fixed
- **Viewer Layer Generation**: Fixed bug where vector layers were being created for raster sources
  - Added type check to skip raster sources in vector layer generation code
  - Raster sources now only handled by `generateDefaultStyle()` which creates proper raster layers
  - Fixes error: "layer requires a vector source" when viewing raster-only archives
- **Format Detection**: Fixed format inference for tiles without URL extensions
  - Added content magic bytes detection as fallback when URL has no extension
  - Fixes misdetection of JPEG as PBF for ArcGIS tile servers
  - Now correctly identifies PNG (89504e47), JPEG (ffd8ff), WebP (RIFF...WEBP)
- **Raster PMTiles URL**: Fixed PMTiles protocol URL for raster sources in viewer
  - Changed from relative path to absolute URL (required by PMTiles protocol)
  - Example: `pmtiles://tiles/source.pmtiles` → `pmtiles://http://localhost/.../tiles/source.pmtiles`
  - Added console logging to help debug PMTiles source loading
  - Fixes blank map when viewing raster-only archives
- **Tile Fetcher Diagnostics**: Added comprehensive error logging to coverage expansion
  - Now logs first 5 error messages when tile fetching fails
  - Logs authentication failure count and rate separately
  - Added source type/format logging for better diagnostics
  - Warns when >50% of fetches fail due to authentication

### Technical Details
- Extension version: 1.0.0
- CLI version: 1.0.0
- Raster detection patterns: `/\d+/\d+/\d+\.(png|jpg|webp)`
- PMTiles tile types: MVT (vector), PNG, JPEG, WEBP
- Raster tiles use Compression.NONE (no gzip)
- Vector tiles use Compression.GZIP
- Format inference: URL extension → content magic bytes → default to PNG

### v1.0.0 Roadmap (Future Phases)
- **Phase 2**: GeoJSON sources (inline + external) with tippecanoe conversion
- **Phase 3**: WMTS support (pre-tiled raster services)
- **Phase 4**: WMS support (dynamic raster generation)
- **Phase 5**: WFS support (vector feature services)
- **Phase 6**: Unified source processor (architectural refactor)

### Requirements
- Same as 0.3.2 - no new dependencies required for Phase 1
- Future phases will require `tippecanoe` for GeoJSON/WFS conversion

## [0.3.2] - 2025-12-15

### Added
- **Capture Options UI**: New collapsible options panel in DevTools extension
  - "Reload on start" checkbox (toggleable, default ON) - ensures sprites/fonts are captured
  - "Expand coverage" checkbox (toggleable, default ON) - fetches additional tiles to fill coverage gaps
  - "Archive mode" selector (standalone/original/full) - for future implementation
- **Coverage Expansion**: Fully functional tile fetching to expand zoom coverage
  - Analyzes captured tiles to identify coverage gaps
  - Fetches missing tiles to complete bounding box coverage
  - Adds one zoom level beyond captured range (e.g., z12-z14 → z12-z15)
  - Conservative rate limiting (10 req/s) to avoid overloading tile servers
  - Safety limit of 500 tiles per source to prevent timeouts
  - Works in both CLI and Modal (async) contexts
- **Options Pass-Through**: Capture options now included in bundle and passed to Modal/CLI
  - Extension sends options in bundle JSON
  - Modal API extracts and logs options
  - CLI API accepts `expand_coverage` and `mode` parameters

### Changed
- **Reload Behavior**: Page reload on capture start is now optional (default: enabled)
- **API Architecture**: Major refactor to support async contexts
  - Added `create_archive_from_bundle_async()` for async callers (Modal)
  - Made `_build_archive()` async to support tile fetching
  - Original `create_archive_from_bundle()` remains as sync wrapper for CLI
  - Uses `expand_coverage_async()` for tile fetching in async contexts

### Fixed
- **Coverage Expansion Bug**: Fixed critical bug causing 134+ million tile calculation
  - Root cause: `expand_zoom` parameter was being interpreted as target zoom instead of zoom levels to add
  - Changed from `expand_zoom = max_zoom + 1` (e.g., 15) to `expand_zoom = 1` (add 1 level)
  - Added safety check to skip expansion if >10,000 tiles calculated (indicates error)
  - Added max_tiles parameter (2000) to prevent runaway fetching
  - Added detailed logging of zoom ranges and tile counts
  - Capped expansion at z18 to prevent excessive tile generation

### Technical Details
- Extension version: 0.3.2
- Options panel uses light grey theme with proper contrast
- Options included in bundle at `bundle.options.expandCoverage` and `bundle.options.archiveMode`
- Coverage expansion now correctly adds 1 zoom level, not jumping to z22
- Logs show: "Captured zoom range: z12-z14" → "Target zoom range: z12-z15 (expand by 1)"
- Safety limits prevent Modal timeouts from excessive tile fetching

### Known Limitations
- **Archive mode**: Parameter accepted but different modes (original/full) not yet implemented. Only "standalone" mode currently functional.

### Requirements
- **Coverage expansion** requires `aiohttp`: `pip install aiohttp`
- Modal deployment includes aiohttp in dependencies

## [0.3.1] - 2025-12-14

### Fixed
- **Double Save Dialog**: Fixed duplicate download trigger that caused two save dialogs to appear
  - Removed duplicate event listener registration on download button
  - Download button now uses only `.onclick` handler (set dynamically in `showComplete()`)
- **Page Reload on Capture Start**: Extension now automatically reloads the page when capture starts
  - Ensures sprites, glyphs, and initial style are captured from the beginning
  - Reload bypasses browser cache to get fresh resources
  - Fixes issue where resources loaded before capture started were not captured

### Technical Details
- Extension version: 0.3.1
- Download button handler switched from `addEventListener` to `.onclick` to prevent double firing
- Added `chrome.devtools.inspectedWindow.reload({ ignoreCache: true })` at capture start
- Network listener starts BEFORE reload to capture all resources from page load
- 300ms delay after reload before updating UI (but capture already in progress)

## [0.3.0] - 2025-12-14

### Added
- **Sprite and Glyph Bundling**: Full support for capturing and archiving map sprites and glyphs
  - Extension now captures sprite resources (PNG/JSON) and glyph files (PBF) from network requests
  - Backend automatically creates fallback 1x sprites when only high-DPI @2x versions are captured
  - Glyph files organized by font name with proper directory structure
- **Async Style Loading**: Viewer now loads captured styles asynchronously to ensure proper URL resolution
  - Sprite and glyph URLs resolved to absolute paths before map initialization
  - Font stacks automatically simplified to single fonts to match captured glyph files
  - Fixes MapLibre validation issues with relative URLs
- **TileJSON Source Matching**: Enhanced style rewriting to support TileJSON URL sources
  - Domain-based matching for sources that use `url` property instead of `tiles` array
  - Supports basemap sources like MapTiler that use TileJSON metadata URLs

### Changed
- **Glyph Directory Structure**: Glyphs now organized by individual font name instead of comma-separated font stacks
  - Aligns with MapLibre's request pattern for cleaner archive structure
  - Example: `glyphs/Metropolis Semi Bold/0-255.pbf` instead of `glyphs/Metropolis_Semi_Bold,Noto_Sans/0-255.pbf`
- **Reduced Logging**: Removed verbose diagnostic logging throughout codebase
  - Style rewriting now shows concise summary instead of detailed step-by-step logging
  - Modal API diagnostics removed
  - Cleaner output for production use

### Fixed
- **Sprite/Glyph Loading in Viewer**: Sprites and glyphs now load correctly in archived maps
  - Fixed MapLibre URL validation errors by resolving URLs before map creation
  - Fixed multi-font glyph requests through font stack simplification
  - Resolved timing issues where transformRequest was called too late
- **Basemap Rendering**: Fixed issue where basemap layers would disappear due to sprite/glyph errors
  - Proper error handling prevents sprite/glyph failures from cascading to tile rendering
  - All layers now render correctly even if some resources are missing

### Technical Details
- Extension version: 0.3.0
- CLI package version: 0.3.0
- Viewer generates separate `style/captured_style.json` file instead of embedding in HTML
- Extension captures sprites at `sprite.png`, `sprite.json`, `sprite@2x.png`, `sprite@2x.json`
- Backend creates fallback files for missing 1x variants from @2x versions
- Viewer uses fetch API to load style before map initialization
- Font stack simplification reduces `["Font1", "Font2"]` arrays to `["Font1"]`

## [0.2.0] - 2024-12-XX

### Added
- Puppeteer-based style extraction via `/fetch-style` endpoint
- Server-side style capture for React applications
- Coverage expansion with `--expand-coverage` flag
- Layer discovery and styling from captured tiles

### Changed
- Improved tile source detection and matching
- Enhanced viewer with layer toggle controls
- Better error handling and validation

### Fixed
- HAR parsing for various tile URL formats
- PMTiles generation for multi-source captures
- Style generation for data layers without captured styling

## [0.1.0] - Initial Release

### Added
- Chrome extension for capturing web map tiles
- CLI tool for creating PMTiles archives
- Self-contained HTML viewer with MapLibre GL JS
- HAR file processing for tile extraction
- Basic style preservation from MapLibre maps

---

[0.3.2]: https://github.com/research-project-studio/snowglobe/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/research-project-studio/snowglobe/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/research-project-studio/snowglobe/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/research-project-studio/snowglobe/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/research-project-studio/snowglobe/releases/tag/v0.1.0
