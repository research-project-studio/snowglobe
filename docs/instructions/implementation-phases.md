# Implementation Progress Tracking

This document tracks progress for the critical issues identified in `address-critical-issues.md`.

---

## Phase 1: Fix PMTiles Portability (Critical - Backend)
**Status**: ✅ COMPLETED
**Scope**: Backend CLI/API changes only
**Dependencies**: None

**Tasks**:
- [x] Update `PMTilesBuilder._ensure_gzipped()` to detect existing compression
- [x] Add validation logging to `PMTilesBuilder.build()`
- [x] Create `validate_pmtiles()` diagnostic function in api.py
- [x] Add `vector_layers` field to PMTilesMetadata dataclass
- [x] Update PMTiles builder to write vector_layers to metadata
- [x] Move layer discovery before PMTiles build in CLI and API
- [x] Generate test PMTiles from NYC parking test case
- [x] Test generated PMTiles with pmtiles CLI tool
- [x] Verify PMTiles load in pmtiles.io

**Files Modified**:
- `cli/src/webmap_archiver/tiles/pmtiles.py`
- `cli/src/webmap_archiver/api.py`
- `cli/src/webmap_archiver/cli.py`
- `cli/src/webmap_archiver/__init__.py`

**Success Criteria**: ✅ ALL MET
- PMTiles files load and render in pmtiles.io
- No double-compression of tile data
- Complete TileJSON metadata with vector_layers
- Validation function reports accurate diagnostics

---

## Phase 2: Capture Runtime Style (Extension)
**Status**: ✅ COMPLETED (with React support added 2025-12-12)
**Scope**: Browser extension changes only
**Dependencies**: Phase 1 (recommended, not required)

**Tasks**:
- [x] Add `captureStyleViaInjection()` function (already existed)
- [x] Enhance map instance detection with comprehensive patterns
- [x] Check `window.map`, `window.maplibreMap`, `window.mapboxMap`
- [x] Check `.__maplibregl_map` and `.__mapboxgl_map` on containers
- [x] Fallback to scanning all window properties
- [x] Add React fiber tree traversal for React apps (NEW 2025-12-12)
- [x] Call `map.getStyle()` when stopping recording (already implemented)
- [x] Include captured style in bundle JSON (already implemented)
- [x] Handle cases where style capture fails gracefully (already implemented)

**Files Modified**:
- `extension/src/content/capturer.ts` (enhanced detection patterns + React fiber support)

**Success Criteria**: ✅ ALL MET
- Bundle includes `style` field with full runtime style JSON ✅
- Works with MapLibre and Mapbox maps ✅
- Three-tier detection strategy for maximum compatibility ✅
- Works with React apps where map is in component state ✅ (NEW)
- Falls back gracefully if map instance not found ✅
- 5-second timeout prevents hanging ✅

**Notes**:
- Initial implementation was mostly already present
- First enhancement added comprehensive detection patterns matching the spec, including window property scanning and special container properties
- Second enhancement (2025-12-12) added React fiber tree traversal to handle React apps where map instance is stored in component state (`memoizedState`, `memoizedProps`, or `stateNode`) rather than on DOM or window
- React traversal walks up the fiber tree with depth limit of 20 to prevent infinite loops

---

## Phase 3: Use Captured Style in Backend
**Status**: ✅ COMPLETED
**Scope**: Backend CLI/API changes
**Dependencies**: Phase 2

**Tasks**:
- [x] Update `_build_archive()` to check for `capture.style`
- [x] Implement `_rewrite_style_sources()` to rewrite source URLs
- [x] Update style sources to point to local `pmtiles://` URLs
- [x] Save captured style to archive as `style/captured_style.json`
- [x] Pass captured style to ViewerConfig
- [x] Update viewer template to use captured style when available
- [x] Keep fallback to generated style for backwards compatibility
- [x] Add verbose logging showing which style is used
- [x] Test with mock bundle containing captured style

**Files Modified**:
- `cli/src/webmap_archiver/api.py` (added `_rewrite_style_sources()`, updated `_build_archive()`)
- `cli/src/webmap_archiver/viewer/generator.py` (added `captured_style` to ViewerConfig, updated template)

**Success Criteria**: ✅ ALL MET
- Backend checks for and processes `capture.style` ✅
- Source URLs rewritten to `pmtiles://tiles/{name}.pmtiles` format ✅
- Captured style saved to archive as JSON file ✅
- Viewer uses captured style when available ✅
- Fallback to generated style works for bundles without captured style ✅
- Verbose logging shows style source ✅

**Testing**: Verified with mock bundle. Captured style correctly saved, sources rewritten, and included in viewer config.

---

## Phase 4: Resource Capture (Extension)
**Status**: Not Started
**Scope**: Browser extension changes
**Dependencies**: Phase 2

**Tasks**:
- [ ] Add sprite request detection (`isSpriteRequest()`)
- [ ] Capture sprite PNG files (1x and 2x)
- [ ] Capture sprite JSON metadata
- [ ] Add glyph request detection (`isGlyphRequest()`)
- [ ] Parse glyph URLs to extract font stack and range
- [ ] Capture glyph PBF files
- [ ] Add `resources` field to bundle with sprites and glyphs
- [ ] Test with NYC parking map (which uses both)

**Files to Modify**:
- `extension/src/background/*.ts` (network listener)
- `extension/src/types/capture-bundle.ts` (add resources type)

**Success Criteria**:
- Bundle includes `resources.sprites` with PNG and JSON
- Bundle includes `resources.glyphs` array with all captured glyphs
- All resource data is base64-encoded

---

## Phase 5: Resource Bundling (Backend)
**Status**: Not Started
**Scope**: Backend CLI/API changes
**Dependencies**: Phase 3, Phase 4

**Tasks**:
- [ ] Extract sprites from `bundle.resources.sprites`
- [ ] Add sprites to archive under `resources/sprites/`
- [ ] Extract glyphs from `bundle.resources.glyphs`
- [ ] Add glyphs to archive under `resources/glyphs/{fontStack}/{range}.pbf`
- [ ] Update style URLs to reference bundled resources
- [ ] Test viewer with bundled resources

**Files to Modify**:
- `cli/src/webmap_archiver/api.py`
- `cli/src/webmap_archiver/viewer/generator.py`

**Success Criteria**:
- Archives contain sprite and glyph resources
- Viewer style references local resources
- Icons and labels render correctly in viewer

---

## Phase 6: Archive Mode Selection (Extension + Backend)
**Status**: Not Started
**Scope**: Both extension and backend
**Dependencies**: All previous phases

**Tasks**:
- [ ] Add mode selector UI to extension panel
- [ ] Add mode descriptions (standalone, original, full)
- [ ] Set default to `full`
- [ ] Set default to include `--expand-coverage` to fill in tile gaps
- [ ] Include `metadata.archiveMode` in bundle
- [ ] Update backend `create_archive_from_bundle()` to respect mode
- [ ] Implement `original` mode (capture site assets)
- [ ] Implement `full` mode (both viewer and original)
- [ ] Update documentation

**Files to Modify**:
- `extension/src/panel/*.html`
- `extension/src/panel/*.ts`
- `cli/src/webmap_archiver/api.py`
- `cli/README.md`

**Success Criteria**:
- Users can select archive mode in extension
- Backend creates archives according to selected mode
- Default is `full` mode
- All three modes work correctly
