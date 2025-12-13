# Fix: Source URL Pattern Matching for Style Rewriting

## For Claude Code

This document explains the root cause of the style source rewriting failure and provides the fix.

---

## Problem Summary

The captured style from `map.getStyle()` has sources like:
```json
"parking_regs": {
  "type": "vector",
  "tiles": ["https://tiles.wxy-labs.org/parking_regs_v2/{z}/{x}/{y}.mvt"]
}
```

The PMTiles files are named based on URL-derived source IDs:
- `wxy-labs.pmtiles` (from domain `tiles.wxy-labs.org`)
- `maptiler.pmtiles` (from domain `api.maptiler.com`)

**The problem**: We need to match `parking_regs` → `wxy-labs.pmtiles` by comparing URL patterns, but the original tile URL is NOT included in the capture bundle.

---

## Root Cause

### In the Extension (panel.ts or wherever tiles are captured)

Tiles are sent as:
```typescript
{
  z: 12,
  x: 1205,
  y: 1539,
  sourceId: "wxy-labs",  // Derived from URL at capture time
  data: "base64...",
  format: "pbf"
  // NO URL FIELD!
}
```

### In the Processor (processor.py)

```python
url_template = _infer_url_template(tile.url)  # tile.url is None/missing!
url_patterns[source_id] = url_template  # Stores None or garbage
```

### In the Style Rewriter (api.py)

```python
for info in tile_source_infos:
    if not info.url_pattern:  # url_pattern is None - skips!
        continue
```

**Result**: No URL patterns to match, so no sources get rewritten.

---

## The Fix

### Part 1: Extension - Include URL in Tile Data

Update the extension to include the original URL when capturing tiles.

**File**: `extension/src/devtools/panel.ts` (or wherever tiles are processed)

Find where tiles are added to the capture data and include the URL:

```typescript
// BEFORE (missing URL)
tiles.push({
  z: tileCoords.z,
  x: tileCoords.x,
  y: tileCoords.y,
  sourceId: derivedSourceId,
  data: base64Data,
  format: tileFormat,
});

// AFTER (include URL)
tiles.push({
  z: tileCoords.z,
  x: tileCoords.x,
  y: tileCoords.y,
  sourceId: derivedSourceId,
  url: request.url,  // ADD THIS - the original request URL
  data: base64Data,
  format: tileFormat,
});
```

### Part 2: Parser - Parse URL Field

Update the parser to handle the URL field.

**File**: `cli/src/webmap_archiver/capture/parser.py`

Find the `CaptureTile` dataclass and add `url` field:

```python
@dataclass
class CaptureTile:
    """A captured map tile."""
    coord: TileCoord
    source_id: str
    data: bytes
    format: str
    url: str | None = None  # ADD THIS FIELD
```

Update the parsing logic to extract the URL:

```python
def _parse_tile(self, tile_data: dict) -> CaptureTile:
    """Parse a tile from bundle data."""
    return CaptureTile(
        coord=TileCoord(
            z=tile_data['z'],
            x=tile_data['x'],
            y=tile_data['y'],
        ),
        source_id=tile_data.get('sourceId') or tile_data.get('source') or 'unknown',
        data=base64.b64decode(tile_data['data']),
        format=tile_data.get('format', 'pbf'),
        url=tile_data.get('url'),  # ADD THIS
    )
```

### Part 3: Processor - Use URL for Pattern

**File**: `cli/src/webmap_archiver/capture/processor.py`

The processor code already tries to use `tile.url`, but we need to handle the case where some tiles have URLs and others don't:

```python
def process_capture_bundle(bundle: CaptureBundle) -> ProcessedCapture:
    tiles_by_source: dict[str, list[tuple[TileCoord, bytes]]] = {}
    tile_sources: dict[str, TileSource] = {}
    url_patterns: dict[str, str] = {}

    if bundle.tiles:
        for tile in bundle.tiles:
            source_id = tile.source_id

            if source_id not in tiles_by_source:
                tiles_by_source[source_id] = []
                
                # Get URL template from tile URL if available
                url_template = None
                if tile.url:  # CHECK FOR URL
                    url_template = _infer_url_template(tile.url)
                
                tile_sources[source_id] = TileSource(
                    name=source_id,
                    url_template=url_template or f"tiles/{source_id}",
                    tile_type=_infer_tile_type(tile.url or "", tile.data),
                    format=tile.format or _infer_format(tile.url or "")
                )
                
                # Store URL pattern for source matching
                if url_template:
                    url_patterns[source_id] = url_template
                    print(f"[Processor] Stored URL pattern for '{source_id}': {url_template}", flush=True)
                else:
                    print(f"[Processor] WARNING: No URL for source '{source_id}', pattern matching will fail", flush=True)

            tiles_by_source[source_id].append((tile.coord, tile.data))
```

### Part 4: Add Debug Logging with flush=True

**File**: `cli/src/webmap_archiver/api.py`

Update all print statements in `_rewrite_style_sources()` to use `flush=True`:

```python
def _rewrite_style_sources(style: dict, tile_source_infos: list) -> dict:
    import copy

    rewritten_style = copy.deepcopy(style)

    if 'sources' not in rewritten_style:
        print("[StyleRewrite] No sources in style", flush=True)
        return rewritten_style

    print(f"[StyleRewrite] Processing {len(rewritten_style['sources'])} sources", flush=True)
    
    # Debug: show what URL patterns we have
    print(f"[StyleRewrite] Available PMTiles URL patterns:", flush=True)
    for info in tile_source_infos:
        print(f"[StyleRewrite]   {info.name}: {info.url_pattern}", flush=True)

    rewrite_count = 0
    
    for source_name, source_def in rewritten_style['sources'].items():
        if source_def.get('type') not in ['vector', 'raster']:
            continue

        tile_urls = source_def.get('tiles', [])
        if not tile_urls:
            print(f"[StyleRewrite] Source '{source_name}' has no tile URLs, skipping", flush=True)
            continue

        print(f"[StyleRewrite] Matching source '{source_name}'", flush=True)
        print(f"[StyleRewrite]   Style tile URLs: {tile_urls[:2]}...", flush=True)

        matched_pmtiles = None

        for tile_url in tile_urls:
            style_pattern = _normalize_tile_url(tile_url)
            print(f"[StyleRewrite]   Normalized style pattern: {style_pattern}", flush=True)

            for info in tile_source_infos:
                if not info.url_pattern:
                    print(f"[StyleRewrite]   Skipping '{info.name}' - no URL pattern", flush=True)
                    continue

                pmtiles_pattern = _normalize_tile_url(info.url_pattern)
                print(f"[StyleRewrite]   Comparing to '{info.name}': {pmtiles_pattern}", flush=True)

                if _patterns_match(style_pattern, pmtiles_pattern):
                    print(f"[StyleRewrite]   MATCH FOUND!", flush=True)
                    matched_pmtiles = info.path
                    break

            if matched_pmtiles:
                break

        if matched_pmtiles:
            print(f"[StyleRewrite] Rewriting '{source_name}' -> {matched_pmtiles}", flush=True)
            source_def['url'] = f"pmtiles://{matched_pmtiles}"
            source_def.pop('tiles', None)
            rewrite_count += 1
        else:
            print(f"[StyleRewrite] WARNING: No match found for '{source_name}'", flush=True)

    print(f"[StyleRewrite] Successfully rewrote {rewrite_count} of {len(rewritten_style['sources'])} sources", flush=True)
    return rewritten_style
```

### Part 5: Fix Misleading Success Message

**File**: `cli/src/webmap_archiver/api.py`

In `_build_archive()`, the message "Rewrote X source URLs" is misleading. Fix it:

```python
# BEFORE (misleading)
if verbose:
    print(f"    Rewrote {len(captured_style.get('sources', {}))} source URLs to local PMTiles")

# AFTER (accurate)
# The _rewrite_style_sources function now prints its own accurate count
# Just indicate that rewriting was attempted
if verbose:
    print("    Style source rewriting complete (see [StyleRewrite] logs for details)")
```

---

## Testing

After making these changes:

1. **Rebuild and reload the extension**

2. **Capture parkingregulations.nyc again** - ensure tiles include URLs

3. **Check Modal logs** for:
   ```
   [Processor] Stored URL pattern for 'wxy-labs': https://tiles.wxy-labs.org/parking_regs_v2/{z}/{x}/{y}.mvt
   [StyleRewrite] Available PMTiles URL patterns:
   [StyleRewrite]   wxy-labs: https://tiles.wxy-labs.org/parking_regs_v2/{z}/{x}/{y}.mvt
   [StyleRewrite] Matching source 'parking_regs'
   [StyleRewrite]   Style tile URLs: ['https://tiles.wxy-labs.org/parking_regs_v2/{z}/{x}/{y}.mvt']
   [StyleRewrite]   MATCH FOUND!
   [StyleRewrite] Rewriting 'parking_regs' -> tiles/wxy-labs.pmtiles
   ```

4. **Verify the archive** - check `style/captured_style.json`:
   ```json
   "parking_regs": {
     "type": "vector",
     "url": "pmtiles://tiles/wxy-labs.pmtiles"
   }
   ```

---

## Summary of Changes

| File | Change |
|------|--------|
| `extension/src/devtools/panel.ts` | Add `url` field to tile objects |
| `cli/src/webmap_archiver/capture/parser.py` | Add `url` field to CaptureTile dataclass |
| `cli/src/webmap_archiver/capture/processor.py` | Handle missing URLs gracefully, add logging |
| `cli/src/webmap_archiver/api.py` | Add `flush=True` to all prints, fix misleading message |

The key insight is: **without the original tile URL, we cannot match style sources to PMTiles files**. The extension must preserve this information.