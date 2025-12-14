# Changelog

All notable changes to the WebMap Archiver project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.3.0]: https://github.com/research-project-studio/snowglobe/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/research-project-studio/snowglobe/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/research-project-studio/snowglobe/releases/tag/v0.1.0
