# Fix: TileJSON Source Matching & Sprite/Glyph Bundling

## For Claude Code

This document addresses two remaining issues:
1. Basemap sources using TileJSON URLs aren't being rewritten to local PMTiles
2. Sprites and glyphs aren't being captured/bundled, causing 403 errors

---

## Issue 1: TileJSON Source Matching

### The Problem

The `maptiler_planet` source uses a TileJSON URL instead of a `tiles` array:

```json
"maptiler_planet": {
  "type": "vector",
  "url": "https://api.maptiler.com/tiles/v3/tiles.json?key=..."
}
```

But we have captured tiles in `maptiler.pmtiles` from URLs like:
```
https://api.maptiler.com/tiles/v3/11/603/770.pbf?key=...
```

The current `_rewrite_style_sources()` only checks for `source_def.get('tiles', [])`, which is empty for TileJSON sources.

### The Fix

**File**: `cli/src/webmap_archiver/api.py`

Update `_rewrite_style_sources()` to also match TileJSON URLs by domain:

```python
def _rewrite_style_sources(style: dict, tile_source_infos: list) -> dict:
    """
    Rewrite source URLs in captured style to point to local PMTiles files.

    Handles two source formats:
    1. Direct tiles array: {"tiles": ["https://example.com/{z}/{x}/{y}.pbf"]}
    2. TileJSON URL: {"url": "https://example.com/tiles.json"}

    For TileJSON sources, matches by domain since we can't compare tile URL patterns directly.
    """
    import copy
    from urllib.parse import urlparse

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

        # STRATEGY 1: Match by tiles array (direct tile URLs)
        tile_urls = source_def.get('tiles', [])
        if tile_urls:
            print(f"[StyleRewrite] Matching source '{source_name}' (tiles array)", flush=True)
            print(f"[StyleRewrite]   Style tile URLs: {tile_urls[:2]}...", flush=True)

            matched_pmtiles = None
            for tile_url in tile_urls:
                style_pattern = _normalize_tile_url(tile_url)
                print(f"[StyleRewrite]   Normalized style pattern: {style_pattern}", flush=True)

                for info in tile_source_infos:
                    if not info.url_pattern:
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
            continue

        # STRATEGY 2: Match by TileJSON URL (domain-based matching)
        tilejson_url = source_def.get('url')
        if tilejson_url:
            # Skip if already rewritten to pmtiles://
            if tilejson_url.startswith('pmtiles://'):
                continue
                
            print(f"[StyleRewrite] Matching source '{source_name}' (TileJSON URL)", flush=True)
            print(f"[StyleRewrite]   TileJSON URL: {tilejson_url}", flush=True)

            tilejson_parsed = urlparse(tilejson_url)
            tilejson_domain = tilejson_parsed.netloc

            matched_pmtiles = None
            for info in tile_source_infos:
                if not info.url_pattern:
                    continue

                pmtiles_parsed = urlparse(info.url_pattern)
                pmtiles_domain = pmtiles_parsed.netloc

                print(f"[StyleRewrite]   Comparing domain '{tilejson_domain}' to '{pmtiles_domain}' ({info.name})", flush=True)

                if tilejson_domain == pmtiles_domain:
                    print(f"[StyleRewrite]   DOMAIN MATCH FOUND!", flush=True)
                    matched_pmtiles = info.path
                    break

            if matched_pmtiles:
                print(f"[StyleRewrite] Rewriting '{source_name}' -> {matched_pmtiles}", flush=True)
                source_def['url'] = f"pmtiles://{matched_pmtiles}"
                rewrite_count += 1
            else:
                print(f"[StyleRewrite] WARNING: No domain match found for '{source_name}'", flush=True)
            continue

        # No tiles array and no URL - nothing to match
        print(f"[StyleRewrite] Source '{source_name}' has no tile URLs or TileJSON URL, skipping", flush=True)

    print(f"[StyleRewrite] Successfully rewrote {rewrite_count} of {len(rewritten_style['sources'])} sources", flush=True)
    return rewritten_style
```

### Expected Result After Fix

```
[StyleRewrite] Matching source 'maptiler_planet' (TileJSON URL)
[StyleRewrite]   TileJSON URL: https://api.maptiler.com/tiles/v3/tiles.json?key=...
[StyleRewrite]   Comparing domain 'api.maptiler.com' to 'api.maptiler.com' (maptiler)
[StyleRewrite]   DOMAIN MATCH FOUND!
[StyleRewrite] Rewriting 'maptiler_planet' -> tiles/maptiler.pmtiles
```

And in the archive:
```json
"maptiler_planet": {
  "type": "vector",
  "url": "pmtiles://tiles/maptiler.pmtiles"
}
```

---

## Issue 2: Sprite & Glyph Bundling

### The Problem

The style references external resources that aren't being captured:

```json
"sprite": "https://api.maptiler.com/maps/dataviz-dark/sprite",
"glyphs": "https://api.maptiler.com/fonts/{fontstack}/{range}.pbf?key=..."
```

These cause 403 errors when the API key expires or is rate-limited, and prevent offline use.

### The Solution (Two Parts)

#### Part A: Extension - Capture Sprites and Glyphs from Network

The extension needs to identify and capture these resources from network traffic.

**File**: `extension/src/devtools/panel.ts` (or wherever network requests are processed)

Add detection for sprite and glyph requests:

```typescript
// Sprite detection patterns
const SPRITE_PATTERNS = [
  /\/sprite(@\dx)?\.png$/i,
  /\/sprite(@\dx)?\.json$/i,
  /\/sprites?\//i,
];

// Glyph detection patterns  
const GLYPH_PATTERNS = [
  /\/fonts\/[^/]+\/\d+-\d+\.pbf/i,
  /\/glyphs?\//i,
];

function isSprite(url: string): boolean {
  return SPRITE_PATTERNS.some(p => p.test(url));
}

function isGlyph(url: string): boolean {
  return GLYPH_PATTERNS.some(p => p.test(url));
}

// In the network request handler:
function processRequest(request: CapturedRequest) {
  const url = request.url;
  
  if (isTileRequest(url)) {
    tiles.push({...});
  } else if (isSprite(url)) {
    // Capture sprite
    const filename = extractSpriteFilename(url);  // e.g., "sprite.png", "sprite@2x.json"
    sprites.push({
      url: url,
      filename: filename,
      data: request.responseBody,  // base64
    });
  } else if (isGlyph(url)) {
    // Capture glyph
    const { fontStack, range } = parseGlyphUrl(url);  // e.g., "Roboto Bold", "0-255"
    glyphs.push({
      url: url,
      fontStack: fontStack,
      range: range,
      data: request.responseBody,  // base64
    });
  }
}

function extractSpriteFilename(url: string): string {
  const match = url.match(/\/(sprite(@\dx)?\.(png|json))(\?|$)/i);
  return match ? match[1] : 'sprite.png';
}

function parseGlyphUrl(url: string): { fontStack: string, range: string } {
  // Pattern: /fonts/{fontstack}/{range}.pbf
  const match = url.match(/\/fonts\/([^/]+)\/(\d+-\d+)\.pbf/i);
  if (match) {
    return {
      fontStack: decodeURIComponent(match[1]),
      range: match[2],
    };
  }
  return { fontStack: 'unknown', range: '0-255' };
}
```

Update the bundle structure to include resources:

```typescript
const bundle = {
  version: "1.0",
  metadata: {...},
  viewport: {...},
  style: null,
  tiles: [...],
  resources: {
    sprites: sprites,  // [{url, filename, data}, ...]
    glyphs: glyphs,    // [{url, fontStack, range, data}, ...]
  },
};
```

#### Part B: Backend - Bundle Sprites and Glyphs in Archive

**File**: `cli/src/webmap_archiver/api.py`

Update `_build_archive()` to handle sprites and glyphs:

```python
def _build_archive(
    processed,
    capture,
    output_path: Path,
    name: str | None,
    mode: str,
    verbose: bool,
) -> ArchiveResult:
    # ... existing code ...

    # Handle captured style
    captured_style = None
    if capture.style:
        if verbose:
            print("  Found captured style from map.getStyle()")
        
        # Rewrite tile sources to point to local PMTiles
        captured_style = _rewrite_style_sources(capture.style, tile_source_infos)
        
        # Rewrite sprite URL if we have captured sprites
        if processed.sprites:
            captured_style = _rewrite_sprite_url(captured_style)
            if verbose:
                print(f"    Rewrote sprite URL to local path")
        
        # Rewrite glyphs URL if we have captured glyphs
        if processed.glyphs:
            captured_style = _rewrite_glyphs_url(captured_style)
            if verbose:
                print(f"    Rewrote glyphs URL to local path")
        
        if verbose:
            print("    Style source rewriting complete (see [StyleRewrite] logs for details)")

    # ... rest of existing code ...

    # Package archive
    packager = ArchivePackager(output_path)

    for info in tile_source_infos:
        pmtiles_path = temp_path / f"{info.name}.pmtiles"
        packager.add_pmtiles(info.name, pmtiles_path)

    packager.add_viewer(viewer_html)

    # Add sprites to archive
    if processed.sprites:
        for sprite in processed.sprites:
            sprite_path = f"sprites/{sprite.filename}"
            packager.temp_files.append((sprite_path, sprite.data))
        if verbose:
            print(f"  Added {len(processed.sprites)} sprite files to archive")

    # Add glyphs to archive
    if processed.glyphs:
        for glyph in processed.glyphs:
            # Organize by font stack: glyphs/{fontstack}/{range}.pbf
            safe_fontstack = "".join(c if c.isalnum() or c in " -_" else "_" for c in glyph.font_stack)
            glyph_path = f"glyphs/{safe_fontstack}/{glyph.range}.pbf"
            packager.temp_files.append((glyph_path, glyph.data))
        if verbose:
            print(f"  Added {len(processed.glyphs)} glyph files to archive")

    # ... rest of packaging ...


def _rewrite_sprite_url(style: dict) -> dict:
    """Rewrite sprite URL to point to local files."""
    if 'sprite' in style:
        # Local sprite path (without extension - MapLibre adds .png/.json)
        style['sprite'] = './sprites/sprite'
        print(f"[StyleRewrite] Rewrote sprite URL to local path", flush=True)
    return style


def _rewrite_glyphs_url(style: dict) -> dict:
    """Rewrite glyphs URL to point to local files."""
    if 'glyphs' in style:
        # Local glyphs path template
        style['glyphs'] = './glyphs/{fontstack}/{range}.pbf'
        print(f"[StyleRewrite] Rewrote glyphs URL to local path", flush=True)
    return style
```

#### Part C: Update Parser and Processor for Resources

**File**: `cli/src/webmap_archiver/capture/parser.py`

Ensure `CaptureResource` dataclass exists and handles sprites/glyphs:

```python
@dataclass
class CaptureResource:
    """A captured resource (sprite or glyph)."""
    resource_type: str  # 'sprite' or 'glyph'
    url: str
    data: bytes
    filename: str | None = None  # For sprites: "sprite.png", "sprite@2x.json"
    font_stack: str | None = None  # For glyphs: "Roboto Bold"
    range: str | None = None  # For glyphs: "0-255"
```

Update parsing to extract resources from bundle:

```python
def _parse_resources(self, resources_data: dict) -> list[CaptureResource]:
    """Parse resources from bundle."""
    import base64
    
    result = []
    
    # Parse sprites
    for sprite in resources_data.get('sprites', []):
        result.append(CaptureResource(
            resource_type='sprite',
            url=sprite.get('url', ''),
            data=base64.b64decode(sprite['data']) if isinstance(sprite['data'], str) else sprite['data'],
            filename=sprite.get('filename', 'sprite.png'),
        ))
    
    # Parse glyphs
    for glyph in resources_data.get('glyphs', []):
        result.append(CaptureResource(
            resource_type='glyph',
            url=glyph.get('url', ''),
            data=base64.b64decode(glyph['data']) if isinstance(glyph['data'], str) else glyph['data'],
            font_stack=glyph.get('fontStack', 'unknown'),
            range=glyph.get('range', '0-255'),
        ))
    
    return result
```

### Archive Structure After Fix

```
archive.zip
├── viewer.html
├── manifest.json
├── style/
│   └── captured_style.json
├── tiles/
│   ├── maptiler.pmtiles
│   └── wxy-labs.pmtiles
├── sprites/
│   ├── sprite.png
│   ├── sprite.json
│   ├── sprite@2x.png
│   └── sprite@2x.json
└── glyphs/
    ├── Metropolis Semi Bold/
    │   ├── 0-255.pbf
    │   └── 256-511.pbf
    ├── Noto Sans Bold/
    │   ├── 0-255.pbf
    │   └── ...
    └── .../
```

### Style After Rewriting

```json
{
  "version": 8,
  "sources": {
    "maptiler_planet": {
      "type": "vector",
      "url": "pmtiles://tiles/maptiler.pmtiles"
    },
    "parking_regs": {
      "type": "vector", 
      "url": "pmtiles://tiles/wxy-labs.pmtiles"
    }
  },
  "sprite": "./sprites/sprite",
  "glyphs": "./glyphs/{fontstack}/{range}.pbf",
  "layers": [...]
}
```

---

## Implementation Order

1. **First**: Fix TileJSON source matching (quick change to `api.py`)
   - This gets basemap tiles working immediately

2. **Second**: Add sprite/glyph capture to extension
   - Update `panel.ts` to detect and capture these resources

3. **Third**: Update backend to bundle and rewrite sprite/glyph URLs
   - Update parser, processor, and archive builder

---

## Testing

### After TileJSON Fix

Run a capture and verify logs show:
```
[StyleRewrite] Rewriting 'maptiler_planet' -> tiles/maptiler.pmtiles
[StyleRewrite] Successfully rewrote 2 of 4 sources
```

Check archive - `maptiler_planet` should now use `pmtiles://tiles/maptiler.pmtiles`.

### After Sprite/Glyph Fix

Run a capture and verify:
1. No 403 errors for fonts
2. Text labels render correctly
3. Icons render correctly
4. Archive contains `sprites/` and `glyphs/` directories