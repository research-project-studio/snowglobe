# Style Extraction via React Fiber Deep Search

## For Claude Code

This document provides implementation instructions for extracting map styles from React applications by traversing the React fiber tree to find map instances.

---

## Background

### The Problem
React applications using MapLibre/Mapbox GL (via react-map-gl or direct integration) store map instances in component state, refs, or hooks - not on the DOM or window. Constructor interception fails because ES module bundling bypasses window globals.

### The Solution
After the page loads, traverse React's internal fiber tree to find any object that implements the MapLibre/Mapbox GL Map interface (`getStyle`, `getCenter`, `getZoom`). This works because:

1. React attaches fiber nodes to DOM elements via `__reactFiber$xxx` properties
2. The fiber tree contains all component state, including refs and hooks
3. A deep search will find the map instance regardless of how the app stores it
4. The approach is generic - it doesn't depend on specific component structure

### Tested On
- parkingregulations.nyc (React + MapLibre, useRef pattern)
- Found map at depth 11, extracted 44 layers including user data layers

---

## Part 1: Style Extraction Module

Create `cli/src/webmap_archiver/capture/style_extractor.py`:

```python
"""
Extract map styles from web pages using Puppeteer.

This module navigates to a URL and extracts the runtime map style by:
1. Finding the map container element
2. Traversing React's fiber tree (if React app)
3. Searching for objects with MapLibre/Mapbox GL Map interface
4. Calling map.getStyle() to get the complete runtime style

This captures programmatically-added layers that don't exist in style.json.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional
from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.page import Page


@dataclass
class ExtractedStyle:
    """Result of style extraction."""
    success: bool
    style: Optional[dict] = None
    viewport: Optional[dict] = None
    error: Optional[str] = None
    debug: Optional[dict] = None


# JavaScript to extract style from React fiber tree
# This is framework-agnostic in detection but handles React specifically
STYLE_EXTRACTION_SCRIPT = """
() => {
  const result = {
    success: false,
    style: null,
    viewport: null,
    error: null,
    debug: {
      method: null,
      nodesSearched: 0,
      containerFound: false,
      fiberFound: false,
    }
  };
  
  // Helper to check if object is a MapLibre/Mapbox map instance
  function isMapInstance(obj) {
    if (!obj || typeof obj !== 'object') return false;
    
    // Must have these core methods
    if (typeof obj.getStyle !== 'function') return false;
    if (typeof obj.getCenter !== 'function') return false;
    if (typeof obj.getZoom !== 'function') return false;
    
    // Verify getStyle returns something valid
    try {
      const style = obj.getStyle();
      return style && style.version === 8 && Array.isArray(style.layers);
    } catch (e) {
      return false;
    }
  }
  
  // Extract style and viewport from a map instance
  function extractFromMap(map) {
    try {
      const style = map.getStyle();
      const center = map.getCenter();
      
      result.success = true;
      result.style = style;
      result.viewport = {
        center: [center.lng, center.lat],
        zoom: map.getZoom(),
        bearing: typeof map.getBearing === 'function' ? map.getBearing() : 0,
        pitch: typeof map.getPitch === 'function' ? map.getPitch() : 0,
      };
      
      // Try to get bounds
      if (typeof map.getBounds === 'function') {
        try {
          const bounds = map.getBounds();
          result.viewport.bounds = [
            [bounds.getWest(), bounds.getSouth()],
            [bounds.getEast(), bounds.getNorth()]
          ];
        } catch (e) {}
      }
      
      return true;
    } catch (e) {
      result.error = 'Failed to extract from map: ' + e.message;
      return false;
    }
  }
  
  // STRATEGY 1: Check window globals (non-React apps, some Vue/Angular apps)
  function tryWindowGlobals() {
    const globalNames = ['map', 'mapInstance', 'mapRef', 'mainMap', 'leafletMap'];
    
    for (const name of globalNames) {
      if (window[name] && isMapInstance(window[name])) {
        result.debug.method = 'window.' + name;
        return extractFromMap(window[name]);
      }
    }
    
    // Check for maplibregl/mapboxgl globals with instances
    if (window.maplibregl && window.maplibregl._instances) {
      for (const instance of Object.values(window.maplibregl._instances)) {
        if (isMapInstance(instance)) {
          result.debug.method = 'maplibregl._instances';
          return extractFromMap(instance);
        }
      }
    }
    
    return false;
  }
  
  // STRATEGY 2: Check map container properties (some frameworks attach here)
  function tryContainerProperties(container) {
    // Properties where frameworks might attach map
    const propNames = [
      '_map', '__map', 'map', 'mapInstance',
      '_maplibregl', '__maplibregl', '_mapboxgl', '__mapboxgl',
      '_leaflet', '_leaflet_map'
    ];
    
    // Check enumerable properties
    for (const prop of propNames) {
      if (container[prop] && isMapInstance(container[prop])) {
        result.debug.method = 'container.' + prop;
        return extractFromMap(container[prop]);
      }
    }
    
    // Check all own properties (including non-enumerable)
    try {
      const allProps = Object.getOwnPropertyNames(container);
      for (const prop of allProps) {
        try {
          const val = container[prop];
          if (isMapInstance(val)) {
            result.debug.method = 'container.' + prop;
            return extractFromMap(val);
          }
        } catch (e) {}
      }
    } catch (e) {}
    
    return false;
  }
  
  // STRATEGY 3: Deep search React fiber tree
  function tryReactFiber(container) {
    const fiberKey = Object.keys(container).find(k => 
      k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
    );
    
    if (!fiberKey) {
      return false;
    }
    
    result.debug.fiberFound = true;
    
    const fiber = container[fiberKey];
    const visited = new WeakSet();
    let mapInstance = null;
    
    function deepSearch(obj, depth) {
      // Limits to prevent infinite loops and performance issues
      if (depth > 35) return;
      if (result.debug.nodesSearched > 100000) return;
      if (mapInstance) return;
      
      if (!obj || typeof obj !== 'object') return;
      
      // Avoid cycles
      try {
        if (visited.has(obj)) return;
        visited.add(obj);
      } catch (e) {
        return;
      }
      
      result.debug.nodesSearched++;
      
      // Check if this is a map instance
      if (isMapInstance(obj)) {
        mapInstance = obj;
        return;
      }
      
      // Get keys to search
      let keys;
      try {
        keys = Object.keys(obj);
      } catch (e) {
        return;
      }
      
      // Prioritize likely property names
      const priorityKeys = [
        'current', 'map', '_map', 'mapRef', 'memoizedState', 
        'memoizedProps', 'stateNode', 'ref', 'deps',
        'child', 'return', 'alternate', 'updateQueue',
        'next', 'lastEffect', 'baseState'
      ];
      
      const sortedKeys = [
        ...priorityKeys.filter(k => keys.includes(k)),
        ...keys.filter(k => !priorityKeys.includes(k))
      ];
      
      for (const key of sortedKeys) {
        if (mapInstance) return;
        
        try {
          const val = obj[key];
          if (val && typeof val === 'object') {
            deepSearch(val, depth + 1);
          }
        } catch (e) {}
      }
    }
    
    deepSearch(fiber, 0);
    
    if (mapInstance) {
      result.debug.method = 'react-fiber-search';
      return extractFromMap(mapInstance);
    }
    
    return false;
  }
  
  // STRATEGY 4: Search from canvas element (last resort)
  function tryCanvasSearch(container) {
    const canvas = container.querySelector('canvas');
    if (!canvas) return false;
    
    // Check canvas properties
    const allProps = [
      ...Object.keys(canvas),
      ...Object.getOwnPropertyNames(canvas)
    ];
    
    for (const prop of allProps) {
      try {
        const val = canvas[prop];
        if (isMapInstance(val)) {
          result.debug.method = 'canvas.' + prop;
          return extractFromMap(val);
        }
      } catch (e) {}
    }
    
    // Check canvas parent chain
    let parent = canvas.parentElement;
    let depth = 0;
    while (parent && depth < 5) {
      if (tryContainerProperties(parent)) {
        return true;
      }
      parent = parent.parentElement;
      depth++;
    }
    
    return false;
  }
  
  // MAIN EXECUTION
  
  // First try window globals (fastest)
  if (tryWindowGlobals()) {
    return result;
  }
  
  // Find map container
  const container = document.querySelector('.maplibregl-map, .mapboxgl-map, .leaflet-container');
  
  if (!container) {
    result.error = 'No map container found';
    return result;
  }
  
  result.debug.containerFound = true;
  
  // Try container properties
  if (tryContainerProperties(container)) {
    return result;
  }
  
  // Try React fiber (most common for modern apps)
  if (tryReactFiber(container)) {
    return result;
  }
  
  // Try canvas search (last resort)
  if (tryCanvasSearch(container)) {
    return result;
  }
  
  // Nothing worked
  result.error = 'Map instance not found. Searched ' + result.debug.nodesSearched + ' nodes.';
  return result;
}
"""


async def extract_style_from_url(
    url: str,
    wait_for_load: float = 3.0,
    wait_for_style: float = 10.0,
    headless: bool = True,
    timeout: float = 60.0,
) -> ExtractedStyle:
    """
    Extract map style from a URL using Puppeteer.
    
    This navigates to the URL, waits for the map to initialize,
    then extracts the runtime style including programmatic layers.
    
    Args:
        url: URL of the page containing the map
        wait_for_load: Seconds to wait after page load before searching
        wait_for_style: Max seconds to poll for style
        headless: Run browser in headless mode
        timeout: Overall timeout in seconds
        
    Returns:
        ExtractedStyle with style dict if successful
    """
    browser: Optional[Browser] = None
    
    try:
        print(f"[StyleExtractor] Launching browser...")
        
        browser = await launch(
            headless=headless,
            executablePath='/usr/bin/chromium',
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ],
            handleSIGINT=False,
            handleSIGTERM=False,
            handleSIGHUP=False,
        )
        
        page: Page = await browser.newPage()
        
        await page.setViewport({'width': 1280, 'height': 800})
        
        # Set user agent to avoid bot detection
        await page.setUserAgent(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        print(f"[StyleExtractor] Navigating to {url}...")
        
        await page.goto(url, {
            'waitUntil': 'networkidle2',
            'timeout': int(timeout * 1000),
        })
        
        title = await page.title()
        print(f"[StyleExtractor] Page loaded: {title}")
        
        # Wait for initial load
        print(f"[StyleExtractor] Waiting {wait_for_load}s for map initialization...")
        await asyncio.sleep(wait_for_load)
        
        # Poll for style
        print(f"[StyleExtractor] Searching for map instance...")
        
        start_time = asyncio.get_event_loop().time()
        last_error = None
        attempts = 0
        
        while asyncio.get_event_loop().time() - start_time < wait_for_style:
            attempts += 1
            
            result = await page.evaluate(STYLE_EXTRACTION_SCRIPT)
            
            if result.get('success'):
                style = result.get('style', {})
                print(f"[StyleExtractor] SUCCESS via {result.get('debug', {}).get('method')}")
                print(f"[StyleExtractor] Layers: {len(style.get('layers', []))}")
                print(f"[StyleExtractor] Sources: {list(style.get('sources', {}).keys())}")
                
                return ExtractedStyle(
                    success=True,
                    style=result.get('style'),
                    viewport=result.get('viewport'),
                    debug=result.get('debug'),
                )
            
            last_error = result.get('error')
            nodes_searched = result.get('debug', {}).get('nodesSearched', 0)
            
            # If we searched a lot of nodes and didn't find it, keep trying
            # (map might still be initializing)
            if nodes_searched > 0:
                print(f"[StyleExtractor] Attempt {attempts}: searched {nodes_searched} nodes, retrying...")
            
            await asyncio.sleep(1.0)
        
        print(f"[StyleExtractor] FAILED after {attempts} attempts: {last_error}")
        
        return ExtractedStyle(
            success=False,
            error=last_error or 'Style extraction timed out',
            debug={'attempts': attempts},
        )
        
    except Exception as e:
        print(f"[StyleExtractor] Error: {e}")
        return ExtractedStyle(
            success=False,
            error=str(e),
        )
        
    finally:
        if browser:
            await browser.close()


async def extract_style_with_retry(
    url: str,
    max_retries: int = 2,
    **kwargs
) -> ExtractedStyle:
    """
    Extract style with retries on failure.
    
    Some pages may have intermittent issues; this provides resilience.
    """
    last_result = None
    
    for attempt in range(max_retries + 1):
        if attempt > 0:
            print(f"[StyleExtractor] Retry attempt {attempt}...")
            await asyncio.sleep(2.0)
        
        result = await extract_style_from_url(url, **kwargs)
        last_result = result
        
        if result.success:
            return result
    
    return last_result
```

---

## Part 2: Update Modal App

Update `cli/src/webmap_archiver/modal_app.py`:

```python
"""
Modal cloud deployment for WebMap Archiver.

Provides:
- POST /process - Process bundle, optionally fetching style from URL
- POST /fetch-style - Fetch only the style from a URL
- GET /download/{id} - Download processed archive
"""

import modal
import asyncio
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

app = modal.App("webmap-archiver")

# Image with Chromium for Puppeteer-based style extraction
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        # Chromium dependencies
        "chromium",
        "libnss3",
        "libatk1.0-0",
        "libatk-bridge2.0-0",
        "libcups2",
        "libdrm2",
        "libxkbcommon0",
        "libxcomposite1",
        "libxdamage1",
        "libxfixes3",
        "libxrandr2",
        "libgbm1",
        "libasound2",
        "libpango-1.0-0",
        "libcairo2",
        "fonts-liberation",
    )
    .pip_install(
        "git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli",
        "pyppeteer>=1.0.0",
        "fastapi>=0.109.0",
    )
    .env({
        "PYPPETEER_CHROMIUM_EXECUTABLE": "/usr/bin/chromium",
        "PYPPETEER_HOME": "/tmp/pyppeteer",
    })
)

volume = modal.Volume.from_name("webmap-archiver-outputs", create_if_missing=True)
VOLUME_PATH = "/outputs"


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    timeout=300,
    memory=2048,
    cpu=2.0,
)
@modal.asgi_app()
def fastapi_app():
    """FastAPI app for the WebMap Archiver API."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
    from typing import Optional
    import asyncio
    
    from webmap_archiver import (
        create_archive_from_bundle,
        inspect_bundle,
        CaptureValidationError,
        __version__,
    )
    from webmap_archiver.capture.style_extractor import extract_style_from_url

    web_app = FastAPI(
        title="WebMap Archiver API",
        description="Archive web maps with full style preservation",
        version=__version__,
    )

    class FetchStyleRequest(BaseModel):
        """Request to fetch style from URL."""
        url: str
        wait_for_load: Optional[float] = 3.0
        wait_for_style: Optional[float] = 10.0

    @web_app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "webmap-archiver",
            "version": __version__,
            "capabilities": ["process", "fetch-style"],
        }

    @web_app.post("/fetch-style")
    async def fetch_style(request: FetchStyleRequest):
        """
        Fetch the runtime map style from a URL.
        
        Uses Puppeteer to navigate to the page, find the map instance,
        and extract the complete style including programmatic layers.
        
        Returns the style JSON for use with /process endpoint.
        """
        try:
            print(f"[API] Fetching style from {request.url}")
            
            result = await extract_style_from_url(
                url=request.url,
                wait_for_load=request.wait_for_load,
                wait_for_style=request.wait_for_style,
                headless=True,
            )
            
            if result.success:
                return {
                    "success": True,
                    "style": result.style,
                    "viewport": result.viewport,
                    "debug": result.debug,
                }
            else:
                return {
                    "success": False,
                    "error": result.error,
                    "debug": result.debug,
                }
                
        except Exception as e:
            print(f"[API] fetch-style error: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    @web_app.post("/process")
    async def process(bundle: dict):
        """
        Process a capture bundle into an archive.
        
        If the bundle has no style but includes a URL in metadata,
        will attempt to fetch the style via Puppeteer.
        
        Args:
            bundle: Capture bundle dict with tiles, metadata, etc.
                    If bundle.metadata.url exists and bundle.style is null,
                    style will be fetched from that URL.
        """
        try:
            archive_id = str(uuid.uuid4())[:8]
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"
            
            print(f"[API] Process request -> {archive_id}")
            print(f"[API] Tiles in bundle: {len(bundle.get('tiles', []))}")
            print(f"[API] Style in bundle: {bundle.get('style') is not None}")
            
            # Check if we need to fetch style
            url = bundle.get('metadata', {}).get('url')
            has_style = bundle.get('style') is not None
            
            style_source = 'bundle'
            
            if not has_style and url:
                print(f"[API] No style in bundle, fetching from {url}")
                
                style_result = await extract_style_from_url(
                    url=url,
                    wait_for_load=3.0,
                    wait_for_style=15.0,
                    headless=True,
                )
                
                if style_result.success:
                    bundle['style'] = style_result.style
                    style_source = 'extracted'
                    print(f"[API] Style extracted: {len(style_result.style.get('layers', []))} layers")
                    
                    # Also update viewport if we got better info
                    if style_result.viewport and not bundle.get('viewport', {}).get('bounds'):
                        bundle['viewport'] = {
                            **bundle.get('viewport', {}),
                            **style_result.viewport,
                        }
                else:
                    print(f"[API] Style extraction failed: {style_result.error}")
                    # Continue without style - will use generated fallback
            
            # Create archive
            result = create_archive_from_bundle(
                bundle=bundle,
                output_path=output_path,
                verbose=True,
            )
            
            volume.commit()
            
            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            
            # Generate filename
            from urllib.parse import urlparse
            if url:
                host = urlparse(url).netloc.replace(".", "-").replace(":", "-")
            else:
                host = "archive"
            date = datetime.now().strftime("%Y-%m-%d")
            filename = f"{host}-{date}.zip"
            
            return {
                "success": True,
                "archiveId": archive_id,
                "filename": filename,
                "downloadUrl": f"/download/{archive_id}",
                "expiresAt": expires_at.isoformat() + "Z",
                "size": result.size,
                "tileCount": result.tile_count,
                "tileSources": [
                    {
                        "name": ts.name,
                        "tileCount": ts.tile_count,
                        "discoveredLayers": ts.discovered_layers,
                    }
                    for ts in result.tile_sources
                ],
                "styleSource": style_source,
                "styleInfo": {
                    "present": bundle.get('style') is not None,
                    "layerCount": len(bundle.get('style', {}).get('layers', [])) if bundle.get('style') else 0,
                    "sources": list(bundle.get('style', {}).get('sources', {}).keys()) if bundle.get('style') else [],
                },
            }
            
        except CaptureValidationError as e:
            print(f"[API] Validation error: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            print(f"[API] Process error: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    @web_app.get("/download/{archive_id}")
    async def download(archive_id: str):
        """Download a processed archive."""
        if not archive_id.isalnum() or len(archive_id) != 8:
            raise HTTPException(status_code=400, detail="Invalid archive ID")
        
        archive_path = Path(VOLUME_PATH) / f"{archive_id}.zip"
        
        if not archive_path.exists():
            raise HTTPException(status_code=404, detail="Archive not found or expired")
        
        return FileResponse(
            path=str(archive_path),
            media_type="application/zip",
            filename=f"webmap-archive-{archive_id}.zip",
        )

    return web_app
```

---

## Part 3: Update Package Init

Update `cli/src/webmap_archiver/capture/__init__.py`:

```python
"""
Capture module for WebMap Archiver.

Provides browser-based capture capabilities using Pyppeteer.
"""

# Style extractor is always available (main use case)
from .style_extractor import (
    extract_style_from_url,
    extract_style_with_retry,
    ExtractedStyle,
)

__all__ = [
    'extract_style_from_url',
    'extract_style_with_retry',
    'ExtractedStyle',
]
```

---

## Part 4: Test Script

Create `cli/tests/test_style_extractor.py`:

```python
"""
Test style extraction on various React-based maps.

Run with: pytest tests/test_style_extractor.py -v -s
Or directly: python tests/test_style_extractor.py
"""

import asyncio
import sys
sys.path.insert(0, 'src')

from webmap_archiver.capture.style_extractor import extract_style_from_url


# Test URLs - mix of React and non-React map implementations
TEST_URLS = [
    {
        'name': 'parkingregulations.nyc',
        'url': 'https://parkingregulations.nyc',
        'expected_sources': ['parking_regs', 'maptiler_planet'],
        'framework': 'react',
    },
    # Add more test URLs as discovered
    # {
    #     'name': 'Example MapTiler',
    #     'url': 'https://...',
    #     'expected_sources': [...],
    #     'framework': 'vanilla',
    # },
]


async def test_url(test_case: dict):
    """Test style extraction for a single URL."""
    print(f"\n{'='*60}")
    print(f"Testing: {test_case['name']}")
    print(f"URL: {test_case['url']}")
    print(f"Framework: {test_case['framework']}")
    print('='*60)
    
    result = await extract_style_from_url(
        url=test_case['url'],
        wait_for_load=5.0,
        wait_for_style=15.0,
        headless=True,
    )
    
    print(f"\nResult:")
    print(f"  Success: {result.success}")
    print(f"  Error: {result.error}")
    print(f"  Debug: {result.debug}")
    
    if result.success:
        style = result.style
        print(f"\nStyle Info:")
        print(f"  Version: {style.get('version')}")
        print(f"  Layers: {len(style.get('layers', []))}")
        print(f"  Sources: {list(style.get('sources', {}).keys())}")
        
        if test_case.get('expected_sources'):
            found_sources = set(style.get('sources', {}).keys())
            expected_sources = set(test_case['expected_sources'])
            missing = expected_sources - found_sources
            if missing:
                print(f"  WARNING: Missing expected sources: {missing}")
            else:
                print(f"  ✓ All expected sources found")
        
        if result.viewport:
            print(f"\nViewport:")
            print(f"  Center: {result.viewport.get('center')}")
            print(f"  Zoom: {result.viewport.get('zoom')}")
    
    return result


async def main():
    """Run all tests."""
    results = []
    
    for test_case in TEST_URLS:
        result = await test_url(test_case)
        results.append({
            'name': test_case['name'],
            'success': result.success,
            'layers': len(result.style.get('layers', [])) if result.style else 0,
        })
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print('='*60)
    
    for r in results:
        status = '✓' if r['success'] else '✗'
        print(f"  {status} {r['name']}: {r['layers']} layers")


if __name__ == '__main__':
    asyncio.run(main())
```

---

## Part 5: Framework Flexibility

The extraction script handles multiple frameworks:

### Currently Supported

1. **React with hooks (useRef, useState)**
   - Traverses fiber tree via `__reactFiber$xxx`
   - Searches `memoizedState`, `deps`, `current`, etc.
   - Works with react-map-gl and direct MapLibre usage

2. **React class components**
   - Searches `stateNode` for class instance state

3. **Non-React (Vue, Angular, vanilla JS)**
   - Checks window globals (`window.map`, etc.)
   - Checks container element properties
   - Checks canvas element properties

### Adding Support for New Frameworks

If a framework isn't working, add detection logic:

```javascript
// In STYLE_EXTRACTION_SCRIPT, add new strategy:

// STRATEGY N: Vue.js
function tryVue(container) {
  // Vue 3 stores component instance on __vueParentComponent
  if (container.__vueParentComponent) {
    const instance = container.__vueParentComponent;
    // Search instance.ctx, instance.data, etc.
  }
  
  // Vue 2 uses __vue__
  if (container.__vue__) {
    // Search container.__vue__.$data, etc.
  }
  
  return false;
}
```

---

## Part 6: Deployment & Testing

### Deploy to Modal

```bash
cd cli
modal deploy src/webmap_archiver/modal_app.py
```

### Test the /fetch-style endpoint

```bash
curl -X POST https://YOUR-MODAL-APP.modal.run/fetch-style \
  -H "Content-Type: application/json" \
  -d '{"url": "https://parkingregulations.nyc"}'
```

Expected response:
```json
{
  "success": true,
  "style": {
    "version": 8,
    "layers": [...],
    "sources": {
      "maptiler_planet": {...},
      "parking_regs": {...}
    }
  },
  "viewport": {
    "center": [-73.9857, 40.7484],
    "zoom": 12
  },
  "debug": {
    "method": "react-fiber-search",
    "nodesSearched": 412
  }
}
```

### Test the /process endpoint with style fetching

```bash
# Extension sends bundle without style
curl -X POST https://YOUR-MODAL-APP.modal.run/process \
  -H "Content-Type: application/json" \
  -d '{
    "version": "1.0",
    "metadata": {
      "url": "https://parkingregulations.nyc",
      "title": "Test"
    },
    "viewport": {"center": [0,0], "zoom": 10},
    "style": null,
    "tiles": [...]
  }'
```

Expected: Server fetches style automatically, merges with tiles, returns archive.

---

## Part 7: Extension Updates (Minimal)

The extension doesn't need major changes. Just ensure it:

1. **Always includes URL** in `bundle.metadata.url`
2. **Sets `style: null`** if style capture fails (don't omit the key)
3. **Continues capturing tiles** via DevTools network interception

The server will handle style extraction when `style` is null and `url` is present.

---

## Verification Checklist

After implementation:

- [ ] `/health` returns `capabilities: ["process", "fetch-style"]`
- [ ] `/fetch-style` returns style with 44 layers for parkingregulations.nyc
- [ ] `/fetch-style` returns `parking_regs` source (the user data layer)
- [ ] `/process` with `style: null` auto-fetches style
- [ ] `/process` response shows `styleSource: "extracted"`
- [ ] Downloaded archive viewer shows properly styled layers
- [ ] Test on at least one other React map site

---

## Troubleshooting

### "No map container found"
- Page may use different class names
- Add more selectors to the querySelector

### "Map not found in fiber tree"
- Increase depth limit (currently 35)
- Increase node limit (currently 100,000)
- Map may be in a different data structure

### "Style extraction timed out"
- Increase `wait_for_load` (map may be slow to initialize)
- Check if page requires interaction to load map

### Works locally but not on Modal
- Ensure Chromium is properly installed in Modal image
- Check Modal logs for browser launch errors
- Try increasing memory allocation