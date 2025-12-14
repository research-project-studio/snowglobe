# Triage: Sprite/Glyph Loading & Basemap Rendering Issues

## Current Status: BROKEN

After implementing sprite/glyph bundling and URL rewriting, two critical issues have emerged:

1. **Sprites/Glyphs not loading** - No `transformRequest` logging, resources not loading
2. **Basemap layers disappeared** - PMTiles files load successfully (200 responses) but map renders blank

## What We Implemented

### Issue 1: TileJSON Source Matching ✅ WORKING
**Problem**: Basemap source `maptiler_planet` uses TileJSON URL instead of tiles array, wasn't being rewritten.

**Solution**: Added Strategy 2 to `_rewrite_style_sources()` for domain-based matching of TileJSON URLs.

**File**: `cli/src/webmap_archiver/api.py:353-431`

**Result**: WORKING - Modal logs show successful rewrites:
```
[StyleRewrite] Matching source 'maptiler_planet' (TileJSON URL)
[StyleRewrite]   DOMAIN MATCH FOUND!
[StyleRewrite] Rewriting 'maptiler_planet' -> tiles/maptiler.pmtiles
[StyleRewrite] Rewriting 'parking_regs' -> tiles/wxy-labs.pmtiles
```

### Issue 2: Sprite/Glyph Bundling (PARTIALLY WORKING)

#### Part A: Extension Capture ✅ WORKING
**Files Modified**:
- `extension/src/devtools/panel.ts:660-698` - Detection functions for sprites/glyphs
- `extension/src/devtools/panel.ts:22-36` - Extended CapturedRequest interface
- `extension/src/devtools/panel.ts:177-233` - Updated handleRequest()
- `extension/src/devtools/panel.ts:479-514` - Resource extraction in buildCaptureBundle()
- `extension/src/devtools/panel.ts:591` - Added resources array to bundle

**Result**: WORKING - Console shows `[WebMap Archiver] Bundle resources: N (X sprites, Y glyphs)`

#### Part B: Backend Parsing ✅ WORKING
**Files Modified**:
- `cli/src/webmap_archiver/capture/parser.py:205-218` - Handle flat array format
- `cli/src/webmap_archiver/capture/parser.py:287` - Support both contentType and type fields

**Result**: WORKING - Resources parsed correctly

#### Part C: Sprite Fallback Logic ✅ WORKING
**Problem**: Only `sprite@2x.png` and `sprite@2x.json` captured (high-DPI), but MapLibre requires base versions.

**Solution**: Duplicate @2x files as base 1x versions if missing.

**File**: `cli/src/webmap_archiver/api.py:723-768`

**Result**: WORKING - Modal logs show:
```
[Archive] No 1x sprite.png found, using @2x as fallback
[Archive] No 1x sprite.json found, using @2x as fallback
```

Archive now contains:
- `sprites/sprite.png` (duplicated from @2x)
- `sprites/sprite.json` (duplicated from @2x)
- `sprites/sprite@2x.png` (original)
- `sprites/sprite@2x.json` (original)

#### Part D: URL Rewriting ✅ WORKING (Backend)
**Files Modified**:
- `cli/src/webmap_archiver/api.py:488-495` - `_rewrite_sprite_url()`
- `cli/src/webmap_archiver/api.py:498-505` - `_rewrite_glyphs_url()`
- `cli/src/webmap_archiver/api.py:670-683` - Call rewriting functions

**Result**: WORKING - Captured style shows:
```json
"sprite": "sprites/sprite",
"glyphs": "glyphs/{fontstack}/{range}.pbf"
```

#### Part E: Viewer URL Resolution ❌ NOT WORKING
**Problem**: MapLibre's URL parser rejects relative paths, needs absolute URLs.

**Solution**: Convert relative paths to absolute at runtime based on viewer location.

**File**: `cli/src/webmap_archiver/viewer/generator.py:119-132`

**Code Added**:
```javascript
// Fix sprite and glyph URLs to be absolute paths
if (style.sprite && !style.sprite.startsWith('http') && !style.sprite.startsWith('data:')) {
    const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
    style.sprite = baseUrl + style.sprite.replace(/^\.\//, '');
    console.log(`[WebMap Archiver] Resolved sprite URL: ${style.sprite}`);
}
if (style.glyphs && !style.glyphs.startsWith('http') && !style.glyphs.startsWith('data:')) {
    const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
    style.glyphs = baseUrl + style.glyphs.replace(/^\.\//, '');
    console.log(`[WebMap Archiver] Resolved glyphs URL: ${style.glyphs}`);
}
```

**Expected Console Output**:
```
[WebMap Archiver] Resolved sprite URL: http://127.0.0.1:5500/sprites/sprite
[WebMap Archiver] Resolved glyphs URL: http://127.0.0.1:5500/glyphs/{fontstack}/{range}.pbf
```

**Actual Result**: ❌ **NOT SEEING THESE LOGS** - URLs not being resolved

#### Part F: Multi-Font Glyph Handling ❌ NOT WORKING
**Problem**: MapLibre requests multiple fonts in single path: `glyphs/Font1,Font2/0-255.pbf` but we only have individual font files.

**Solution**: Use `transformRequest` to intercept and use first font only.

**File**: `cli/src/webmap_archiver/viewer/generator.py:317-354`

**Code Added**:
```javascript
function transformRequest(url, resourceType) {
    // Handle sprite URLs
    if (resourceType === 'SpriteImage' || resourceType === 'SpriteJSON') {
        if (!url.startsWith('http') && !url.startsWith('data:')) {
            const cleanUrl = url.replace(/^\.\//, '');
            console.log(`[Sprites] Resolving: ${url} -> ${cleanUrl}`);
            return { url: cleanUrl };
        }
    }

    // Handle multi-font glyph requests
    if (resourceType === 'Glyphs') {
        const match = url.match(/\/glyphs\/([^/]+)\/(\d+-\d+\.pbf)/);
        if (match) {
            const fontStacks = match[1];
            const range = match[2];
            if (fontStacks.includes(',')) {
                const firstFont = fontStacks.split(',')[0];
                const newUrl = url.replace(
                    `/glyphs/${fontStacks}/${range}`,
                    `/glyphs/${firstFont}/${range}`
                );
                console.log(`[Glyphs] Multi-font request: ${fontStacks} -> using ${firstFont}`);
                return { url: newUrl };
            }
        }
    }

    return { url: url };
}

const map = new maplibregl.Map({
    // ...
    transformRequest: transformRequest
});
```

**Expected Console Output**:
```
[Sprites] Resolving: sprites/sprite.png -> sprites/sprite.png
[Glyphs] Multi-font request: Metropolis Semi Bold Italic,Noto Sans Bold -> using Metropolis Semi Bold Italic
```

**Actual Result**: ❌ **NO LOGS AT ALL** - `transformRequest` not being called

## Critical Issue: Basemap Disappeared

**Symptoms**:
- Basemap layers (from `maptiler.pmtiles`) no longer render
- Network tab shows successful requests: `tiles/maptiler.pmtiles` → 200 OK
- PMTiles protocol working (other sources load)
- Data layers (from `wxy-labs.pmtiles`) may still work

**When it broke**: After implementing sprite/glyph URL rewriting

**Possible causes**:
1. Style corruption during URL rewriting
2. Source rewriting broken for basemap specifically
3. Layer rendering blocked by missing sprites/glyphs
4. JavaScript error preventing map initialization (but no errors in console?)

## Testing Environment

**Serving**: VSCode Live Server at `http://127.0.0.1:5500/viewer.html`

**Browser**: Chrome with DevTools

**Archive Structure** (confirmed present):
```
parkingregulations-nyc-2025-12-14/
├── viewer.html
├── manifest.json
├── style/
│   └── captured_style.json
├── tiles/
│   ├── maptiler.pmtiles  ✅ 200 OK requests
│   └── wxy-labs.pmtiles  ✅ 200 OK requests
├── sprites/
│   ├── sprite.png         ✅ exists
│   ├── sprite.json        ✅ exists
│   ├── sprite@2x.png      ✅ exists
│   └── sprite@2x.json     ✅ exists
└── glyphs/
    └── [various fonts]/[ranges].pbf  ✅ exist
```

**Captured Style** (`style/captured_style.json`):
```json
{
  "version": 8,
  "sources": {
    "maptiler_planet": {
      "type": "vector",
      "url": "pmtiles://tiles/maptiler.pmtiles"  ✅ Rewritten correctly
    },
    "parking_regs": {
      "type": "vector",
      "url": "pmtiles://tiles/wxy-labs.pmtiles"  ✅ Rewritten correctly
    },
    "maptiler_attribution": {...},
    "location": {...}
  },
  "sprite": "sprites/sprite",  ✅ Rewritten correctly
  "glyphs": "glyphs/{fontstack}/{range}.pbf",  ✅ Rewritten correctly
  "layers": [44 layers...]
}
```

## What's NOT Working

### 1. URL Resolution Logs Missing
The viewer should log when converting relative to absolute URLs, but these logs never appear:
- ❌ `[WebMap Archiver] Resolved sprite URL: ...`
- ❌ `[WebMap Archiver] Resolved glyphs URL: ...`

This suggests the URL resolution code at `generator.py:119-132` is not executing.

### 2. transformRequest Never Called
The `transformRequest` function is defined and passed to Map constructor, but never logs:
- ❌ `[Sprites] Resolving: ...`
- ❌ `[Glyphs] Multi-font request: ...`

This suggests MapLibre isn't calling `transformRequest` at all, OR requests aren't being made.

### 3. Basemap Rendering Failed
- PMTiles loads successfully (200 OK)
- Source is correctly rewritten to `pmtiles://tiles/maptiler.pmtiles`
- But no basemap visible on map
- No JavaScript errors in console

### 4. Sprites/Glyphs Not Loading (Presumably)
- Network tab shows no requests to `sprites/` or `glyphs/` directories
- This would explain missing icons and labels
- But no errors about missing sprites/glyphs in console

## Questions for Triage

1. **Why is URL resolution code not running?** The code at `generator.py:119-132` should execute when `config.capturedStyle` exists, but logs never appear.

2. **Why is `transformRequest` never called?** Function is properly defined and attached to Map, but MapLibre never calls it.

3. **Are sprites/glyphs even being requested?** Check Network tab - do we see ANY requests to `sprites/` or `glyphs/`?

4. **Why did the basemap stop rendering?** It was working before sprite/glyph changes. Check:
   - Are basemap layers in the style?
   - Do they have correct source references?
   - Are they visible (not hidden by other layers)?
   - Is there a JavaScript error preventing rendering?

5. **Is the generated viewer.html malformed?** The template has double curly braces `{{` for escaping. Could there be a Python string formatting error causing broken JavaScript?

## Debugging Steps to Try

1. **View generated viewer.html source** - Search for:
   - `if (config.capturedStyle)` - Is the URL resolution code present?
   - `function transformRequest` - Is it properly formatted?
   - `transformRequest: transformRequest` - Is it in the Map constructor?

2. **Check browser console** for:
   - Any JavaScript syntax errors
   - Any MapLibre errors or warnings
   - What logs DO appear (to know what's executing)

3. **Check Network tab** for:
   - Are sprite/glyph requests being made at all?
   - What URLs are being requested?
   - Any 404s or failed requests?

4. **Verify PMTiles protocol** is working:
   - Are tile requests going through?
   - Is `pmtiles://` being resolved correctly?

5. **Check Map initialization**:
   - Does `console.log("Map initialized")` appear if added after Map creation?
   - Does the map load event fire?

6. **Simplify to isolate issue**:
   - Remove sprite/glyph URL from style temporarily
   - Does basemap render without them?
   - Add back one at a time to identify culprit

## Files Modified (Summary)

### Extension (TypeScript - requires rebuild):
- `extension/src/devtools/panel.ts` - Multiple sections for sprite/glyph detection and capture

### Backend (Python):
- `cli/src/webmap_archiver/api.py` - URL rewriting, sprite fallback, source matching
- `cli/src/webmap_archiver/capture/parser.py` - Resource parsing
- `cli/src/webmap_archiver/viewer/generator.py` - Viewer JavaScript injection

## Next Steps

Need to determine:
1. Is the viewer.html being generated correctly (valid JavaScript)?
2. Why is code that should execute (URL resolution) not running?
3. Why did basemap rendering break?
4. How to properly handle sprite/glyph loading in a static archive context?

Consider alternative approaches:
- Embed sprites as base64 data URIs in style
- Use a different glyph serving strategy
- Revert sprite/glyph changes and focus on basemap issue first
