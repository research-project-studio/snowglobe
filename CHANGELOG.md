# Changelog

All notable changes to the WebMap Archiver project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] - 2025-12-15

### Added
- **Capture Options UI**: New collapsible options panel in DevTools extension
  - "Reload on start" checkbox (toggleable, default ON) - ensures sprites/fonts are captured
  - "Expand coverage" checkbox (toggleable, default ON) - prepares for future tile fetching
  - "Archive mode" selector (standalone/original/full) - for future implementation
- **Options Pass-Through**: Capture options now included in bundle and passed to Modal/CLI
  - Extension sends options in bundle JSON
  - Modal API extracts and logs options
  - CLI API accepts `expand_coverage` and `mode` parameters

### Changed
- **Reload Behavior**: Page reload on capture start is now optional (default: enabled)
- **API Signatures**: Updated `create_archive_from_bundle()` to accept `expand_coverage` and `mode` parameters

### Technical Details
- Extension version: 0.3.2
- Options panel uses light grey theme with proper contrast
- Options included in bundle at `bundle.options.expandCoverage` and `bundle.options.archiveMode`
- Modal logs: `[API] Options - expandCoverage: true, archiveMode: standalone`
- CLI parameters accepted but not yet fully implemented (flagged for future work)

### Known Limitations
- **Expand coverage**: Parameter accepted but tile fetching not implemented in Modal/extension workflow. Use CLI with `--expand-coverage` flag for actual coverage expansion.
- **Archive mode**: Parameter accepted but different modes (original/full) not yet implemented. Only "standalone" mode currently functional.

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
