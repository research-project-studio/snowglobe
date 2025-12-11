# Implementation Progress Tracking

This document tracks progress for the critical issues identified in `address-critical-issues.md`.

---

## Phase 1: Fix PMTiles Portability (Critical - Backend)
**Status**: Not Started
**Scope**: Backend CLI/API changes only
**Dependencies**: None

**Tasks**:
- [ ] Update `PMTilesBuilder._ensure_gzipped()` to detect existing compression
- [ ] Add validation logging to `PMTilesBuilder.build()`
- [ ] Create `validate_pmtiles()` diagnostic function in api.py
- [ ] Generate test PMTiles from NYC parking test case
- [ ] Test generated PMTiles in pmtiles.io
- [ ] Verify both raster and vector tiles load correctly

**Files to Modify**:
- `cli/src/webmap_archiver/tiles/pmtiles_builder.py`
- `cli/src/webmap_archiver/api.py` (add validation function)

**Success Criteria**:
- PMTiles files load in pmtiles.io without errors
- No double-compression of tile data
- Validation function reports accurate diagnostics

---

## Phase 2: Capture Runtime Style (Extension)
**Status**: Not Started
**Scope**: Browser extension changes only
**Dependencies**: Phase 1 (recommended, not required)

**Tasks**:
- [ ] Add `captureMapStyle()` function to panel/devtools code
- [ ] Implement map instance detection (MapLibre, Mapbox, generic)
- [ ] Call `map.getStyle()` when stopping recording
- [ ] Include captured style in bundle JSON
- [ ] Handle cases where style capture fails gracefully
- [ ] Test with NYC parking map

**Files to Modify**:
- `extension/src/panel/*.ts` or similar devtools code

**Success Criteria**:
- Bundle includes `style` field with full runtime style JSON
- Works with MapLibre-based maps
- Falls back gracefully if map instance not found

---

## Phase 3: Use Captured Style in Backend
**Status**: Not Started
**Scope**: Backend CLI/API changes
**Dependencies**: Phase 2

**Tasks**:
- [ ] Update `_build_archive()` to check for `bundle.style`
- [ ] Implement `rewrite_style_sources()` to rewrite source URLs
- [ ] Update style sources to point to local `pmtiles://` URLs
- [ ] Keep fallback to generated style for backwards compatibility
- [ ] Add verbose logging showing which style is used
- [ ] Test with captured NYC parking style

**Files to Modify**:
- `cli/src/webmap_archiver/api.py`
- `cli/src/webmap_archiver/viewer/generator.py` (possibly)

**Success Criteria**:
- Viewer uses captured style when available
- Generated archives match original map appearance
- Fallback style still works for bundles without captured style

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
