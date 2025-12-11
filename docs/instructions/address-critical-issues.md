# WebMap Archiver: Critical Issues Analysis & Path Forward

## For Claude Code

This document analyzes two critical issues and provides implementation guidance to address them.

---

## Issue 1: PMTiles Not Loading in pmtiles.io

### Symptoms
- Both basemap and data layer PMTiles files don't load in pmtiles.io
- Files have reasonable sizes (8MB+)
- PMTiles header shows tiles present, but nothing renders

### Root Cause Analysis

**Likely Causes (in order of probability):**

1. **Double Compression**
   - The extension captures network responses that are already gzip-compressed
   - The `PMTilesBuilder._ensure_gzipped()` method gzips again
   - Result: Double-gzipped data that can't be decompressed

2. **Incorrect Tile Type in Header**
   - PMTiles header must specify `TileType.MVT` for vector tiles
   - If metadata says "vector" but header says wrong type, viewers fail

3. **Tile ID Calculation Issues**
   - PMTiles uses Hilbert curve tile IDs, not simple ZXY
   - If `zxy_to_tileid` isn't called correctly, tiles are stored at wrong IDs

4. **Missing or Invalid Metadata**
   - pmtiles.io needs valid bounds, zoom ranges, and center
   - If GeoBounds conversion fails, header has invalid bounds

### Diagnostic Steps

Add this diagnostic code to the CLI to validate PMTiles:

```python
# In api.py or a new diagnostics module

def validate_pmtiles(path: Path) -> dict:
    """Validate a PMTiles file and return diagnostic info."""
    from pmtiles.reader import Reader
    
    with open(path, 'rb') as f:
        reader = Reader(f)
        header = reader.header()
        metadata = reader.metadata()
        
        # Get a sample tile
        sample_tile = None
        for tile_id, tile_data in reader.tiles():
            sample_tile = (tile_id, tile_data[:100])  # First 100 bytes
            break
        
        return {
            "valid": True,
            "tile_type": header.tile_type,
            "tile_compression": header.tile_compression,
            "min_zoom": header.min_zoom,
            "max_zoom": header.max_zoom,
            "bounds": {
                "west": header.min_lon_e7 / 1e7,
                "south": header.min_lat_e7 / 1e7,
                "east": header.max_lon_e7 / 1e7,
                "north": header.max_lat_e7 / 1e7,
            },
            "tile_count": header.num_tile_entries,
            "sample_tile_starts_with": sample_tile[1][:10].hex() if sample_tile else None,
            "sample_is_gzipped": sample_tile[1][:2] == b'\x1f\x8b' if sample_tile else None,
        }
```

### Fix: Smart Compression Handling

Update `PMTilesBuilder._ensure_gzipped()`:

```python
def _ensure_gzipped(self, data: bytes) -> bytes:
    """
    Ensure data is gzipped exactly once.
    
    CRITICAL: Network-captured tiles may already be gzipped.
    Double-gzipping corrupts the data.
    """
    # Check if already gzipped (magic bytes: 0x1f 0x8b)
    if len(data) >= 2 and data[:2] == b'\x1f\x8b':
        # Validate it's actually valid gzip
        try:
            import gzip
            gzip.decompress(data)
            return data  # Already valid gzip, return as-is
        except Exception:
            pass  # Not valid gzip despite magic bytes, compress it
    
    # Not gzipped - compress it
    import gzip
    import io
    
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6) as gz:
        gz.write(data)
    return buf.getvalue()
```

### Fix: Validate Tile Content Before Building

```python
def build(self) -> None:
    """Build and write the PMTiles archive."""
    if not self.tiles:
        raise ValueError("No tiles to write")
    
    if not self.metadata:
        raise ValueError("Metadata not set")
    
    # VALIDATION: Check tile content
    sample_coord, sample_data = self.tiles[0]
    print(f"  Sample tile z{sample_coord.z}/{sample_coord.x}/{sample_coord.y}")
    print(f"    Size: {len(sample_data)} bytes")
    print(f"    Starts with: {sample_data[:20].hex()}")
    print(f"    Is gzipped: {sample_data[:2] == b'\\x1f\\x8b'}")
    
    # ... rest of build logic
```

---

## Issue 2: Cartography Not Preserved

### The Core Problem

The extension currently produces a "data dump" - raw tiles with generated styling - rather than preserving the **cartographic artifact** which includes:

- The map's visual appearance (colors, line weights, labels)
- The original style.json with all programmatic layers
- Sprites (icons, symbols)
- Glyphs (fonts for labels)
- The original website context (legends, UI, explanatory text)

### What the Specs Intended

From the spec, archive modes are:

| Mode | What's Preserved | File Size | Use Case |
|------|-----------------|-----------|----------|
| `standalone` | Tiles + generated viewer | Small | Quick data extraction |
| `original` | Full site + serve.py | Medium | Academic/archival |
| `full` | Both | Larger | Maximum flexibility |

**The default should be `full`, not `standalone`.**

### Current Extension Behavior vs. Intended

**Current (Broken):**
```
Extension captures:
├── Network tile requests (✓)
├── Viewport info (✓)
├── Page metadata (✓)
├── style: null (✗ - NOT captured!)
├── sprites: [] (✗ - NOT captured!)
├── glyphs: [] (✗ - NOT captured!)
└── siteAssets: [] (✗ - NOT captured!)
```

**Intended:**
```
Extension captures:
├── Network tile requests (✓)
├── Viewport info (✓)  
├── Page metadata (✓)
├── style: { ...map.getStyle()... } (✓)
├── sprites: { png: base64, json: {...} } (✓)
├── glyphs: { "Font/0-255.pbf": base64, ... } (✓)
└── siteAssets: { "index.html": base64, ... } (for full mode)
```

### Required Extension Changes

#### 1. Add Archive Mode Selection to Popup/Panel

```typescript
// In panel.html, add mode selector
<div class="mode-selector">
  <label>Archive Mode:</label>
  <select id="archive-mode">
    <option value="full" selected>Full (recommended)</option>
    <option value="standalone">Standalone (data only)</option>
    <option value="original">Original Site</option>
  </select>
  <p class="mode-description" id="mode-desc">
    Preserves all tiles, styling, and original website.
  </p>
</div>
```

#### 2. Capture Runtime Style via map.getStyle()

```typescript
// In panel.ts, when stopping recording

async function captureMapStyle(): Promise<object | null> {
  return new Promise((resolve) => {
    chrome.devtools.inspectedWindow.eval(
      `(function() {
        // Try to find the map instance
        // This works for MapLibre/Mapbox GL JS
        
        // Common patterns for map instances
        const candidates = [
          window.map,
          window.maplibreMap,
          window.mapboxMap,
          document.querySelector('.maplibregl-map')?.__maplibregl_map,
          document.querySelector('.mapboxgl-map')?.__mapboxgl_map,
        ];
        
        for (const map of candidates) {
          if (map && typeof map.getStyle === 'function') {
            try {
              return JSON.stringify(map.getStyle());
            } catch (e) {
              console.error('Failed to get style:', e);
            }
          }
        }
        
        // Fallback: look for map in all window properties
        for (const key of Object.keys(window)) {
          const obj = window[key];
          if (obj && typeof obj.getStyle === 'function') {
            try {
              return JSON.stringify(obj.getStyle());
            } catch (e) {}
          }
        }
        
        return null;
      })()`,
      (result, error) => {
        if (error) {
          console.error('[WebMap Archiver] Style capture error:', error);
          resolve(null);
        } else if (result) {
          try {
            resolve(JSON.parse(result));
          } catch {
            resolve(null);
          }
        } else {
          resolve(null);
        }
      }
    );
  });
}
```

#### 3. Capture Sprites from Network

```typescript
// Track sprite requests during recording
interface SpriteData {
  baseUrl: string;
  png1x?: string;  // base64
  png2x?: string;  // base64
  json1x?: object;
  json2x?: object;
}

function isSpriteRequest(url: string): boolean {
  return url.includes('sprite') && (
    url.endsWith('.png') || 
    url.endsWith('.json') ||
    url.includes('sprite@2x')
  );
}

// In the request handler
if (isSpriteRequest(request.url)) {
  const content = await getResponseContent(request);
  if (request.url.endsWith('.png')) {
    // Store as base64
    sprites.png = content;
  } else if (request.url.endsWith('.json')) {
    sprites.json = JSON.parse(atob(content));
  }
}
```

#### 4. Capture Glyphs from Network

```typescript
// Track glyph requests during recording
interface GlyphData {
  fontStack: string;
  range: string;
  data: string;  // base64 pbf
}

function isGlyphRequest(url: string): boolean {
  // Pattern: /fonts/{font-stack}/{range}.pbf
  return url.includes('/fonts/') && url.endsWith('.pbf');
}

function parseGlyphUrl(url: string): { fontStack: string; range: string } | null {
  const match = url.match(/\/fonts\/([^/]+)\/(\d+-\d+)\.pbf/);
  if (match) {
    return { fontStack: decodeURIComponent(match[1]), range: match[2] };
  }
  return null;
}
```

#### 5. Update Bundle Building

```typescript
// In buildCaptureBundle()

const bundle = {
  version: "1.0",
  metadata: {
    url: pageInfo.url,
    title: pageInfo.title,
    capturedAt: new Date().toISOString(),
    userAgent: navigator.userAgent,
    archiveMode: selectedMode,  // NEW: Include selected mode
  },
  viewport: { ... },
  
  // CRITICAL: Include captured style
  style: capturedStyle,  // From map.getStyle()
  
  // CRITICAL: Include sprites
  resources: {
    sprites: capturedSprites,
    glyphs: capturedGlyphs,
  },
  
  har: { ... },
  tiles: [ ... ],
};
```

---

## Backend Changes Required

### 1. API Should Accept and Use Mode

```python
def create_archive_from_bundle(
    bundle: dict,
    output_path: Path,
    *,
    mode: str | None = None,  # Override bundle's mode
    verbose: bool = False,
) -> ArchiveResult:
    """
    Create archive respecting the requested mode.
    
    Modes:
    - standalone: viewer.html + tiles only
    - original: original site + serve.py + tiles
    - full: both (default)
    """
    # Get mode from bundle or parameter
    archive_mode = mode or bundle.get("metadata", {}).get("archiveMode", "full")
    
    # ... build archive according to mode
```

### 2. Use Captured Style, Not Generated

```python
def _build_archive(...):
    # CRITICAL: Use captured style if available
    captured_style = bundle.get("style")
    
    if captured_style:
        # Rewrite source URLs to point to local PMTiles
        style = rewrite_style_sources(captured_style, tile_sources)
        print(f"  Using captured style with {len(style.get('layers', []))} layers")
    else:
        # Fall back to generated style (for standalone mode or when capture failed)
        style = generate_fallback_style(tile_sources, discovered_layers)
        print(f"  Generated fallback style (no captured style available)")
```

### 3. Bundle Sprites and Glyphs

```python
def _build_archive(...):
    resources = bundle.get("resources", {})
    
    # Add sprites to archive
    if resources.get("sprites"):
        for variant, data in resources["sprites"].items():
            if variant.endswith(".png"):
                packager.add_file(f"resources/sprites/{variant}", base64.b64decode(data))
            else:
                packager.add_file(f"resources/sprites/{variant}", json.dumps(data).encode())
    
    # Add glyphs to archive
    if resources.get("glyphs"):
        for glyph in resources["glyphs"]:
            path = f"resources/glyphs/{glyph['fontStack']}/{glyph['range']}.pbf"
            packager.add_file(path, base64.b64decode(glyph['data']))
```

---

## Avoiding Overfitting to Test Case

### Current Risk

The implementation has been developed primarily against `parkingregulations.nyc`. There's risk of:
- Hard-coded MapTiler-specific patterns
- Assumptions about source naming
- WXY-specific layer detection

### Mitigation Strategies

#### 1. Generic Source Detection

```python
# DON'T do this:
if "maptiler" in url:
    source_name = "maptiler"
elif "wxy" in url:
    source_name = "wxy-labs"

# DO this:
def derive_source_name(url: str) -> str:
    """Derive source name from URL without provider-specific logic."""
    parsed = urlparse(url)
    
    # Use domain as base
    domain = parsed.netloc.split('.')[0]
    
    # Try to extract tileset name from path
    # Common patterns: /tiles/v3/..., /v4/mapbox.terrain/...
    path_parts = [p for p in parsed.path.split('/') if p and not p.isdigit()]
    
    # Skip common prefixes
    skip = {'tiles', 'v1', 'v2', 'v3', 'v4', 'api'}
    meaningful = [p for p in path_parts if p.lower() not in skip]
    
    if meaningful:
        return f"{domain}-{meaningful[0]}"
    return domain
```

#### 2. Layer Discovery from Tiles, Not Hardcoding

```python
# DON'T do this:
KNOWN_LAYERS = {
    "maptiler": ["transportation", "water", "building"],
    "wxy-labs": ["parking_regs"],
}

# DO this:
def discover_layers(tiles: list[tuple]) -> list[str]:
    """Discover layers by actually parsing tile content."""
    # Parse MVT protobuf to extract layer names
    # This works for ANY vector tile source
```

#### 3. Test with Multiple Sources

Create a test suite with different map providers:
- MapTiler (current test case)
- Mapbox
- ESRI
- OpenMapTiles
- Custom/self-hosted

#### 4. No Provider-Specific Viewer Code

```javascript
// DON'T do this:
if (source.name.includes('maptiler')) {
    // Special maptiler handling
}

// DO this:
// Use source metadata uniformly
sources[source.name] = {
    type: source.type,
    url: `pmtiles://tiles/${source.filename}`,
};
```

---

## Implementation Priority

### Phase 1: Fix PMTiles Portability (Highest Priority)
1. Add compression detection to avoid double-gzip
2. Validate PMTiles output with diagnostic tool
3. Test generated PMTiles in pmtiles.io before considering done

### Phase 2: Capture Runtime Style
1. Implement `map.getStyle()` capture in extension
2. Pass style through to backend
3. Use captured style in viewer generation

### Phase 3: Add Archive Mode Selection
1. Add mode selector to extension UI
2. Implement `full` mode with original site preservation
3. Make `full` the default

### Phase 4: Resource Bundling
1. Capture sprites during recording
2. Capture glyphs during recording
3. Bundle in archive and rewrite style URLs

---

## Success Criteria

After these fixes:

1. **PMTiles Portability**
   - Any generated PMTiles file loads in pmtiles.io
   - Can be used with any PMTiles-compatible viewer
   - Works independently of the bundled viewer.html

2. **Cartography Preservation**
   - Archived map looks identical to original
   - Labels render correctly (fonts bundled)
   - Icons render correctly (sprites bundled)
   - Layer styling matches original (runtime style captured)

3. **No Overfitting**
   - Works with MapTiler, Mapbox, ESRI, and other providers
   - No provider-specific hardcoding
   - Layer discovery is fully automatic

4. **User Control**
   - Users can select archive mode
   - Default is `full` for maximum preservation
   - Clear explanation of what each mode includes