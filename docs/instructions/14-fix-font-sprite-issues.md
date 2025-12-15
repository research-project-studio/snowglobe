# Comprehensive Fix: Sprite/Glyph Loading & Basemap Rendering

## For Claude Code

This document provides a complete diagnosis and fix for the issues described in the triage document.

---

## Root Cause Analysis

### Issue 1: URL Resolution Code Not Executing

**Diagnosis**: The code at `generator.py:119-132` that should resolve relative URLs to absolute URLs is likely:
1. Not being included in the generated HTML due to Python template issues
2. Or is in a code path that's not executing

**Why this matters**: MapLibre GL JS **requires absolute URLs** for sprites and glyphs. From the MapLibre Style Spec:
> "The URL must be absolute, containing the scheme, authority and path components."

Relative URLs like `sprites/sprite` will fail URL validation.

### Issue 2: transformRequest Never Called

**Diagnosis**: This is a **known MapLibre bug** (GitHub Issue #3897). 

The sprite URL gets validated BEFORE `transformRequest` is called:
```javascript
// MapLibre source code (load_sprite.ts:31)
// URL validation happens here, BEFORE transformRequest
```

**Result**: If the sprite URL is relative or invalid, MapLibre throws an error before `transformRequest` ever gets a chance to fix it.

**Conclusion**: We cannot rely on `transformRequest` to fix sprite/glyph URLs. They must be absolute BEFORE being passed to MapLibre.

### Issue 3: Basemap Disappeared

**Most Likely Cause**: A silent JavaScript error is preventing proper map initialization. When sprite/glyph loading fails early (due to invalid URLs), it can cascade into rendering failures for all layers, not just text/icons.

---

## The Solution: Pre-Process Style Before Map Creation

Instead of trying to fix URLs via `transformRequest` (which doesn't work for sprites), we must:
1. **Resolve all relative URLs to absolute URLs** before creating the map
2. **Handle the style object directly** in JavaScript before passing to MapLibre

### Complete Fix for generator.py

**File**: `cli/src/webmap_archiver/viewer/generator.py`

The key is to ensure URL resolution happens **synchronously** before the Map constructor is called. Here's the corrected approach:

```python
# In the ViewerGenerator class, update the template to include this JavaScript:

VIEWER_SCRIPT = '''
<script>
    // ==== URL RESOLUTION (MUST RUN BEFORE MAP CREATION) ====
    function resolveStyleUrls(style) {
        // Get base URL for resolving relative paths
        const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
        console.log('[WebMap Archiver] Base URL for resolution:', baseUrl);
        
        // Resolve sprite URL
        if (style.sprite) {
            if (typeof style.sprite === 'string' && !style.sprite.startsWith('http') && !style.sprite.startsWith('data:')) {
                const originalSprite = style.sprite;
                style.sprite = baseUrl + style.sprite.replace(/^\\.?\\//, '');
                console.log('[WebMap Archiver] Resolved sprite:', originalSprite, '->', style.sprite);
            }
        }
        
        // Resolve glyphs URL
        if (style.glyphs) {
            if (!style.glyphs.startsWith('http') && !style.glyphs.startsWith('data:')) {
                const originalGlyphs = style.glyphs;
                style.glyphs = baseUrl + style.glyphs.replace(/^\\.?\\//, '');
                console.log('[WebMap Archiver] Resolved glyphs:', originalGlyphs, '->', style.glyphs);
            }
        }
        
        return style;
    }
    
    // ==== FONT STACK SIMPLIFICATION ====
    // MapLibre requests fonts as comma-separated lists like "Font1,Font2"
    // But we only have individual font files. This simplifies to first font only.
    function simplifyFontReferences(style) {
        if (!style.layers) return style;
        
        let modified = 0;
        style.layers.forEach(layer => {
            if (layer.layout && layer.layout['text-font']) {
                const fonts = layer.layout['text-font'];
                if (Array.isArray(fonts) && fonts.length > 1) {
                    // Keep only the first font
                    layer.layout['text-font'] = [fonts[0]];
                    modified++;
                }
            }
        });
        
        if (modified > 0) {
            console.log('[WebMap Archiver] Simplified', modified, 'font stack references to single fonts');
        }
        
        return style;
    }
    
    // ==== PMTILES PROTOCOL SETUP ====
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol('pmtiles', protocol.tile);
    
    // ==== LOAD AND PROCESS STYLE ====
    async function initMap() {
        try {
            // Load the captured style
            const styleResponse = await fetch('style/captured_style.json');
            if (!styleResponse.ok) {
                throw new Error('Failed to load captured_style.json: ' + styleResponse.status);
            }
            let style = await styleResponse.json();
            console.log('[WebMap Archiver] Loaded style with', style.layers?.length || 0, 'layers');
            
            // CRITICAL: Resolve URLs before creating map
            style = resolveStyleUrls(style);
            
            // Simplify font stacks to avoid multi-font requests
            style = simplifyFontReferences(style);
            
            // Create map with processed style
            const map = new maplibregl.Map({
                container: 'map',
                style: style,
                center: [{{ center_lng }}, {{ center_lat }}],
                zoom: {{ zoom }},
                maxZoom: {{ max_zoom }},
                minZoom: {{ min_zoom }}
            });
            
            map.on('load', () => {
                console.log('[WebMap Archiver] Map loaded successfully');
            });
            
            map.on('error', (e) => {
                console.error('[WebMap Archiver] Map error:', e.error?.message || e);
            });
            
            // Add navigation controls
            map.addControl(new maplibregl.NavigationControl());
            
        } catch (error) {
            console.error('[WebMap Archiver] Failed to initialize map:', error);
            document.getElementById('map').innerHTML = 
                '<div style="padding: 20px; color: red;">Error loading map: ' + error.message + '</div>';
        }
    }
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initMap);
    } else {
        initMap();
    }
</script>
'''
```

### Key Changes from Current Implementation

1. **Async/await pattern**: Load style via `fetch()` first, process it, THEN create the map
2. **URL resolution BEFORE map creation**: Not in `transformRequest` which is too late
3. **Font simplification**: Reduce multi-font requests to single fonts we actually have
4. **Error handling**: Catch and display errors clearly

---

## Alternative Approach: Embed Resources as Data URIs

If file:// protocol issues persist, consider embedding small resources directly:

### Embed Sprites as Base64

In `api.py`, when the sprite files are small enough:

```python
def _embed_sprite_as_data_uri(style: dict, sprite_data: dict) -> dict:
    """
    Embed sprite data directly in the style as data URIs.
    This avoids all URL resolution issues.
    """
    import base64
    import json
    
    if 'sprite.png' in sprite_data and 'sprite.json' in sprite_data:
        png_b64 = base64.b64encode(sprite_data['sprite.png']).decode('ascii')
        json_content = sprite_data['sprite.json'].decode('utf-8')
        
        # Use data URI format that some MapLibre versions support
        # Note: This is experimental and may not work in all versions
        style['sprite'] = f"data:application/json;base64,{base64.b64encode(json_content.encode()).decode()}"
        
    return style
```

**Note**: Data URI sprites have limited support. The file-based approach is more reliable.

---

## Debugging Steps to Verify Fix

### Step 1: Check Generated viewer.html

After deploying changes, examine the generated `viewer.html`:

```bash
# In the extracted archive
cat viewer.html | grep -A 50 "resolveStyleUrls"
```

Verify:
- [ ] `resolveStyleUrls` function is present
- [ ] It's called BEFORE `new maplibregl.Map()`
- [ ] No JavaScript syntax errors (check browser console)

### Step 2: Check Browser Console

Open viewer.html and look for these logs:

Expected logs (in order):
```
[WebMap Archiver] Base URL for resolution: http://127.0.0.1:5500/
[WebMap Archiver] Loaded style with 44 layers
[WebMap Archiver] Resolved sprite: sprites/sprite -> http://127.0.0.1:5500/sprites/sprite
[WebMap Archiver] Resolved glyphs: glyphs/{fontstack}/{range}.pbf -> http://127.0.0.1:5500/glyphs/{fontstack}/{range}.pbf
[WebMap Archiver] Simplified N font stack references to single fonts
[WebMap Archiver] Map loaded successfully
```

If you see errors instead, note:
- What error message?
- At what point does it fail?

### Step 3: Check Network Tab

Look for requests to:
- [ ] `style/captured_style.json` → Should be 200 OK
- [ ] `sprites/sprite.png` → Should be 200 OK (with absolute URL)
- [ ] `sprites/sprite.json` → Should be 200 OK
- [ ] `glyphs/[fontname]/0-255.pbf` → Should be 200 OK
- [ ] `tiles/maptiler.pmtiles` → Should be 200 OK

### Step 4: Verify Style Content

Check that `style/captured_style.json` has correct content:

```json
{
  "version": 8,
  "sources": {
    "maptiler_planet": {
      "type": "vector",
      "url": "pmtiles://tiles/maptiler.pmtiles"  // ✓ Correct
    }
  },
  "sprite": "sprites/sprite",  // Will be resolved at runtime
  "glyphs": "glyphs/{fontstack}/{range}.pbf",  // Will be resolved at runtime
  "layers": [...]
}
```

---

## Complete generator.py Update

Here's the minimal change needed to `generator.py`:

```python
# Find where the map initialization JavaScript is generated
# Replace the map creation code with async loading pattern

def _generate_map_script(self, config: ViewerConfig) -> str:
    """Generate the map initialization script."""
    
    # Escape for JavaScript
    center_lng = config.bounds.west + (config.bounds.east - config.bounds.west) / 2
    center_lat = config.bounds.south + (config.bounds.north - config.bounds.south) / 2
    
    return f'''
    <script>
        // PMTiles protocol
        const protocol = new pmtiles.Protocol();
        maplibregl.addProtocol('pmtiles', protocol.tile);
        
        async function initMap() {{
            const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
            console.log('[WebMap Archiver] Base URL:', baseUrl);
            
            try {{
                // Load captured style
                const response = await fetch('style/captured_style.json');
                if (!response.ok) throw new Error('Failed to load style: ' + response.status);
                let style = await response.json();
                
                // Resolve relative URLs to absolute (REQUIRED by MapLibre)
                if (style.sprite && !style.sprite.startsWith('http')) {{
                    style.sprite = baseUrl + style.sprite.replace(/^\\.?\\//, '');
                    console.log('[WebMap Archiver] Resolved sprite URL:', style.sprite);
                }}
                if (style.glyphs && !style.glyphs.startsWith('http')) {{
                    style.glyphs = baseUrl + style.glyphs.replace(/^\\.?\\//, '');
                    console.log('[WebMap Archiver] Resolved glyphs URL:', style.glyphs);
                }}
                
                // Simplify font stacks (multi-font -> single font)
                if (style.layers) {{
                    style.layers.forEach(layer => {{
                        if (layer.layout && Array.isArray(layer.layout['text-font']) && layer.layout['text-font'].length > 1) {{
                            layer.layout['text-font'] = [layer.layout['text-font'][0]];
                        }}
                    }});
                }}
                
                // Create map
                const map = new maplibregl.Map({{
                    container: 'map',
                    style: style,
                    center: [{center_lng}, {center_lat}],
                    zoom: {config.min_zoom + 2},
                    minZoom: {config.min_zoom},
                    maxZoom: {config.max_zoom}
                }});
                
                map.on('load', () => console.log('[WebMap Archiver] Map loaded'));
                map.on('error', (e) => console.error('[WebMap Archiver] Error:', e.error?.message || e));
                map.addControl(new maplibregl.NavigationControl());
                
            }} catch (error) {{
                console.error('[WebMap Archiver] Init failed:', error);
                document.getElementById('map').innerHTML = '<p style="color:red;padding:20px;">Error: ' + error.message + '</p>';
            }}
        }}
        
        document.addEventListener('DOMContentLoaded', initMap);
    </script>
    '''
```

**Important**: Note the double curly braces `{{` and `}}` which are Python f-string escapes that become single braces `{` and `}` in the output JavaScript.

---

## Font Handling Strategy

### The Problem

MapLibre requests fonts as comma-separated stacks:
```
GET /glyphs/Metropolis Semi Bold Italic,Noto Sans Bold/0-255.pbf
```

But we only capture individual font files:
```
glyphs/Metropolis Semi Bold Italic/0-255.pbf
glyphs/Noto Sans Bold/0-255.pbf
```

### Solution Options

**Option A: Simplify at style level (Recommended)**
Modify the style's `text-font` arrays to use only the first font:
```javascript
layer.layout['text-font'] = [fonts[0]];  // Drop fallback fonts
```

**Option B: Use transformRequest (Only for glyphs, not sprites)**
```javascript
transformRequest: (url, resourceType) => {
    if (resourceType === 'Glyphs' && url.includes(',')) {
        // Extract first font from comma-separated list
        const match = url.match(/\/glyphs\/([^/]+)\/(\d+-\d+\.pbf)/);
        if (match) {
            const fonts = match[1];
            const range = match[2];
            const firstFont = fonts.split(',')[0].trim();
            return { url: url.replace(`/glyphs/${fonts}/`, `/glyphs/${firstFont}/`) };
        }
    }
    return { url };
}
```

**Option C: Create combined font files (Complex)**
Pre-process to merge font stacks into single files. Not recommended for MVP.

---

## Summary Checklist

### Immediate Fixes (Priority 1)

1. [ ] Update `generator.py` to use async style loading pattern
2. [ ] Ensure URLs are resolved to absolute BEFORE map creation
3. [ ] Add font stack simplification to reduce multi-font requests
4. [ ] Add comprehensive error logging

### Verification Steps

1. [ ] Check generated viewer.html has correct JavaScript
2. [ ] Verify browser console shows URL resolution logs
3. [ ] Confirm Network tab shows 200 responses for sprites/glyphs
4. [ ] Verify basemap layers render

### If Basemap Still Doesn't Render

Check these additional items:
1. [ ] Are there JavaScript errors in console?
2. [ ] Is the PMTiles protocol working? (Check for `pmtiles://` requests)
3. [ ] Do the layers have correct `source` and `source-layer` references?
4. [ ] Try loading just the PMTiles in pmtiles.io to verify tile content

---

## Files to Modify

| File | Changes |
|------|---------|
| `cli/src/webmap_archiver/viewer/generator.py` | Async style loading, URL resolution before map creation |
| `cli/src/webmap_archiver/api.py` | (No changes needed if generator.py handles resolution) |

The key insight is: **URL resolution must happen BEFORE MapLibre sees the style, not via transformRequest**. The current implementation likely has the code in the wrong place or with Python template escaping issues.