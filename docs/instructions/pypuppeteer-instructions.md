# Plan B: Puppeteer-Based Capture via Modal

## For Claude Code

This document provides complete instructions for implementing URL-based map capture using Puppeteer on Modal. This approach bypasses all browser extension limitations by having the cloud service navigate to the URL directly.

---

## Overview

### Current Architecture (Complex, Unreliable)
```
User's Browser                          Modal
     │                                    │
     ├─ Extension content script          │
     │  (isolated world - can't find map) │
     │                                    │
     ├─ DevTools panel                    │
     │  (captures network, tries to       │
     │   eval for style - unreliable)     │
     │                                    │
     └─ Sends bundle ──────────────────►  Process bundle
        (tiles ✓, style ✗)                (missing style!)
```

### New Architecture (Simple, Reliable)
```
User's Browser                          Modal
     │                                    │
     └─ Extension sends URL ───────────►  Puppeteer navigates to URL
                                          │
                                          ├─ Injects interceptor BEFORE page JS
                                          ├─ Captures style via map.getStyle() ✓
                                          ├─ Captures tiles from network ✓
                                          ├─ Captures sprites/glyphs ✓
                                          │
     Download ◄─────────────────────────  Returns archive.zip
```

---

## Part 1: Modal App Updates

### 1.1 Update Modal Image

The Modal image needs Chromium and Pyppeteer:

```python
# modal_app.py

import modal

app = modal.App("webmap-archiver")

# Updated image with browser support
image = (
    modal.Image.debian_slim(python_version="3.12")
    # Install Chromium and dependencies
    .apt_install(
        "chromium",
        "chromium-driver",
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
    )
    .pip_install(
        # Core package from GitHub
        "git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli",
        # Puppeteer
        "pyppeteer>=1.0.0",
        # Web framework
        "fastapi>=0.109.0",
    )
    # Set Chromium path for Pyppeteer
    .env({"PYPPETEER_CHROMIUM_EXECUTABLE": "/usr/bin/chromium"})
)
```

### 1.2 Create Capture Module

Create `cli/src/webmap_archiver/capture/browser_capture.py`:

```python
"""
Browser-based map capture using Pyppeteer.

This module provides reliable map style capture by:
1. Injecting interceptor code BEFORE any page JavaScript runs
2. Capturing the map instance when it's created
3. Extracting the complete style including programmatic layers
"""

import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.page import Page


@dataclass
class TileCapture:
    """A captured tile."""
    url: str
    z: int
    x: int
    y: int
    source: str
    format: str
    data: str  # base64 encoded


@dataclass 
class ResourceCapture:
    """A captured resource (sprite, glyph, style)."""
    url: str
    type: str  # 'sprite_png', 'sprite_json', 'glyph', 'style'
    data: str  # base64 encoded
    content_type: str


@dataclass
class CaptureResult:
    """Complete capture result."""
    url: str
    title: str
    captured_at: str
    style: Optional[dict] = None
    viewport: Optional[dict] = None
    tiles: list[TileCapture] = field(default_factory=list)
    resources: list[ResourceCapture] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    debug: dict = field(default_factory=dict)


# JavaScript injected BEFORE page loads
MAP_INTERCEPTOR_SCRIPT = """
(function() {
    'use strict';
    
    console.log('[WebMap Archiver] Interceptor injecting...');
    
    // Global registry
    window.__WEBMAP_CAPTURE__ = {
        maps: [],
        ready: false,
        interceptorVersion: '1.0',
    };
    
    // Store original defineProperty
    const originalDefineProperty = Object.defineProperty;
    
    /**
     * Patch a map library's constructor to capture instances.
     */
    function patchMapLibrary(lib, libraryName) {
        if (!lib || !lib.Map) {
            console.log('[WebMap Archiver] Cannot patch', libraryName, '- no Map constructor');
            return false;
        }
        
        if (lib.Map.__webmap_patched__) {
            console.log('[WebMap Archiver]', libraryName, 'already patched');
            return true;
        }
        
        console.log('[WebMap Archiver] Patching', libraryName, 'Map constructor...');
        
        const OriginalMap = lib.Map;
        
        // Create wrapper constructor
        const PatchedMap = function(...args) {
            console.log('[WebMap Archiver] Map constructor called for', libraryName);
            
            // Call original constructor
            const instance = new OriginalMap(...args);
            
            // Register instance
            window.__WEBMAP_CAPTURE__.maps.push({
                instance: instance,
                library: libraryName,
                createdAt: Date.now(),
                container: args[0]?.container || null,
            });
            
            console.log('[WebMap Archiver] Map instance captured. Total:', 
                        window.__WEBMAP_CAPTURE__.maps.length);
            
            // Listen for style load
            if (typeof instance.once === 'function') {
                instance.once('style.load', function() {
                    console.log('[WebMap Archiver] Style loaded for', libraryName, 'map');
                });
            }
            
            return instance;
        };
        
        // Preserve prototype chain
        PatchedMap.prototype = OriginalMap.prototype;
        Object.setPrototypeOf(PatchedMap, OriginalMap);
        
        // Copy static properties
        for (const key of Object.getOwnPropertyNames(OriginalMap)) {
            if (key !== 'prototype' && key !== 'length' && key !== 'name') {
                try {
                    const descriptor = Object.getOwnPropertyDescriptor(OriginalMap, key);
                    if (descriptor) {
                        Object.defineProperty(PatchedMap, key, descriptor);
                    }
                } catch (e) {
                    // Some properties can't be copied
                }
            }
        }
        
        // Mark as patched
        PatchedMap.__webmap_patched__ = true;
        PatchedMap.__webmap_original__ = OriginalMap;
        
        // Replace
        lib.Map = PatchedMap;
        
        console.log('[WebMap Archiver] Successfully patched', libraryName);
        return true;
    }
    
    /**
     * Watch for library to be defined on window.
     */
    function watchForLibrary(name, friendlyName) {
        let currentValue = window[name];
        
        // If already defined, patch immediately
        if (currentValue && currentValue.Map) {
            patchMapLibrary(currentValue, friendlyName);
        }
        
        // Watch for future assignment
        try {
            Object.defineProperty(window, name, {
                get: function() {
                    return currentValue;
                },
                set: function(newValue) {
                    console.log('[WebMap Archiver]', name, 'being defined on window');
                    currentValue = newValue;
                    if (newValue && newValue.Map) {
                        // Defer slightly to let library initialize
                        setTimeout(function() {
                            patchMapLibrary(newValue, friendlyName);
                        }, 0);
                    }
                },
                configurable: true,
                enumerable: true,
            });
        } catch (e) {
            console.warn('[WebMap Archiver] Could not watch', name, ':', e.message);
        }
    }
    
    // Watch for common map libraries
    watchForLibrary('maplibregl', 'maplibre');
    watchForLibrary('mapboxgl', 'mapbox');
    
    window.__WEBMAP_CAPTURE__.ready = true;
    console.log('[WebMap Archiver] Interceptor ready');
})();
"""


# JavaScript to extract captured data
EXTRACT_DATA_SCRIPT = """
() => {
    const capture = window.__WEBMAP_CAPTURE__;
    
    const result = {
        interceptorReady: capture?.ready || false,
        mapCount: capture?.maps?.length || 0,
        style: null,
        viewport: null,
        errors: [],
    };
    
    if (!capture || !capture.maps || capture.maps.length === 0) {
        result.errors.push('No maps captured by interceptor');
        return result;
    }
    
    // Find map with loaded style
    let targetMap = null;
    
    for (const entry of capture.maps) {
        const map = entry.instance;
        if (map && typeof map.getStyle === 'function') {
            try {
                // Check if style is loaded
                if (typeof map.isStyleLoaded === 'function' && map.isStyleLoaded()) {
                    targetMap = map;
                    break;
                }
                // Fallback to any map with getStyle
                if (!targetMap) {
                    targetMap = map;
                }
            } catch (e) {
                result.errors.push('Error checking map: ' + e.message);
            }
        }
    }
    
    if (!targetMap) {
        result.errors.push('No valid map instance found');
        return result;
    }
    
    // Extract style
    try {
        result.style = targetMap.getStyle();
        
        if (result.style) {
            console.log('[WebMap Archiver] Captured style with', 
                        result.style.layers?.length || 0, 'layers');
            console.log('[WebMap Archiver] Sources:', 
                        Object.keys(result.style.sources || {}));
        }
    } catch (e) {
        result.errors.push('getStyle() failed: ' + e.message);
    }
    
    // Extract viewport
    try {
        const center = targetMap.getCenter();
        result.viewport = {
            center: [center.lng, center.lat],
            zoom: targetMap.getZoom(),
            bearing: targetMap.getBearing?.() || 0,
            pitch: targetMap.getPitch?.() || 0,
        };
        
        // Try to get bounds
        if (typeof targetMap.getBounds === 'function') {
            const bounds = targetMap.getBounds();
            result.viewport.bounds = [
                [bounds.getWest(), bounds.getSouth()],
                [bounds.getEast(), bounds.getNorth()]
            ];
        }
    } catch (e) {
        result.errors.push('Viewport extraction failed: ' + e.message);
    }
    
    return result;
}
"""


def is_tile_request(url: str) -> bool:
    """Check if URL is a map tile request."""
    url_lower = url.lower()
    
    # Must have tile-like path pattern
    if not re.search(r'/\d+/\d+/\d+', url):
        return False
    
    # Check extension or content indicators
    tile_indicators = ['.pbf', '.mvt', '.png', '.jpg', '.jpeg', '.webp', '/tiles/']
    return any(ind in url_lower for ind in tile_indicators)


def is_style_request(url: str) -> bool:
    """Check if URL is a style.json request."""
    url_lower = url.lower()
    return 'style.json' in url_lower or '/styles/' in url_lower


def is_sprite_request(url: str) -> bool:
    """Check if URL is a sprite request."""
    url_lower = url.lower()
    return 'sprite' in url_lower and (url_lower.endswith('.png') or url_lower.endswith('.json'))


def is_glyph_request(url: str) -> bool:
    """Check if URL is a glyph/font request."""
    url_lower = url.lower()
    return '/fonts/' in url_lower and url_lower.endswith('.pbf')


def parse_tile_url(url: str) -> Optional[dict]:
    """Extract tile coordinates and source from URL."""
    # Pattern: /{z}/{x}/{y}.ext or /{z}/{x}/{y}
    match = re.search(r'/(\d+)/(\d+)/(\d+)(?:\.(\w+))?', url)
    if not match:
        return None
    
    z, x, y, ext = match.groups()
    
    # Derive source name from URL
    parsed = urlparse(url)
    
    # Try to extract meaningful source name
    # e.g., api.maptiler.com -> maptiler
    # e.g., tiles.example.com/overlay -> overlay
    host_parts = parsed.netloc.split('.')
    path_parts = [p for p in parsed.path.split('/') if p and not p.isdigit()]
    
    # Filter out common non-descriptive parts
    skip_words = {'api', 'tiles', 'v1', 'v2', 'v3', 'v4', 'maps', 'data'}
    meaningful_path = [p for p in path_parts if p.lower() not in skip_words]
    
    if meaningful_path:
        source = meaningful_path[0]
    elif host_parts:
        source = host_parts[0] if host_parts[0] not in ('api', 'tiles', 'www') else host_parts[1] if len(host_parts) > 1 else host_parts[0]
    else:
        source = 'tiles'
    
    # Determine format
    tile_format = 'pbf'
    if ext:
        if ext in ('png', 'jpg', 'jpeg', 'webp'):
            tile_format = ext
        elif ext in ('pbf', 'mvt'):
            tile_format = 'pbf'
    elif '.png' in url:
        tile_format = 'png'
    
    return {
        'z': int(z),
        'x': int(x),
        'y': int(y),
        'source': source,
        'format': tile_format,
    }


async def capture_map_from_url(
    url: str,
    wait_for_idle: float = 5.0,
    wait_for_style: float = 10.0,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    timeout: float = 60.0,
) -> CaptureResult:
    """
    Capture a web map by navigating to its URL.
    
    Args:
        url: URL of the page containing the map
        wait_for_idle: Seconds to wait after network idle
        wait_for_style: Max seconds to wait for style to load
        headless: Run browser in headless mode
        viewport_width: Browser viewport width
        viewport_height: Browser viewport height
        timeout: Overall timeout in seconds
        
    Returns:
        CaptureResult with style, tiles, and resources
    """
    result = CaptureResult(
        url=url,
        title="",
        captured_at=datetime.utcnow().isoformat() + "Z",
    )
    
    # Track network requests
    pending_responses: dict[str, dict] = {}
    
    browser: Optional[Browser] = None
    
    try:
        print(f"[Capture] Launching browser (headless={headless})...")
        
        browser = await launch(
            headless=headless,
            executablePath='/usr/bin/chromium',  # Modal's Chromium path
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ],
            handleSIGINT=False,
            handleSIGTERM=False,
            handleSIGHUP=False,
        )
        
        page: Page = await browser.newPage()
        
        await page.setViewport({
            'width': viewport_width,
            'height': viewport_height,
        })
        
        # Set a reasonable user agent
        await page.setUserAgent(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        # CRITICAL: Inject interceptor BEFORE any page JavaScript runs
        print("[Capture] Injecting map interceptor...")
        await page.evaluateOnNewDocument(MAP_INTERCEPTOR_SCRIPT)
        
        # Enable request interception
        await page.setRequestInterception(True)
        
        async def on_request(request):
            """Track and continue requests."""
            req_url = request.url
            
            # Track relevant requests
            if is_tile_request(req_url):
                pending_responses[req_url] = {'type': 'tile', 'url': req_url}
            elif is_style_request(req_url):
                pending_responses[req_url] = {'type': 'style', 'url': req_url}
            elif is_sprite_request(req_url):
                rtype = 'sprite_png' if req_url.endswith('.png') else 'sprite_json'
                pending_responses[req_url] = {'type': rtype, 'url': req_url}
            elif is_glyph_request(req_url):
                pending_responses[req_url] = {'type': 'glyph', 'url': req_url}
            
            await request.continue_()
        
        async def on_response(response):
            """Capture response bodies."""
            resp_url = response.url
            
            if resp_url in pending_responses:
                entry = pending_responses[resp_url]
                try:
                    if response.status == 200:
                        body = await response.buffer()
                        entry['data'] = base64.b64encode(body).decode('utf-8')
                        entry['content_type'] = response.headers.get('content-type', '')
                        entry['status'] = 200
                except Exception as e:
                    entry['error'] = str(e)
        
        page.on('request', lambda req: asyncio.ensure_future(on_request(req)))
        page.on('response', lambda resp: asyncio.ensure_future(on_response(resp)))
        
        # Navigate to URL
        print(f"[Capture] Navigating to {url}...")
        await page.goto(url, {
            'waitUntil': 'networkidle2',
            'timeout': int(timeout * 1000),
        })
        
        result.title = await page.title()
        print(f"[Capture] Page loaded: {result.title}")
        
        # Wait for map to initialize
        print(f"[Capture] Waiting {wait_for_idle}s for map initialization...")
        await asyncio.sleep(wait_for_idle)
        
        # Poll for style to be ready
        print("[Capture] Waiting for map style to load...")
        style_ready = False
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < wait_for_style:
            check_result = await page.evaluate("""
                () => {
                    const capture = window.__WEBMAP_CAPTURE__;
                    if (!capture || !capture.maps || capture.maps.length === 0) {
                        return { ready: false, reason: 'no_maps' };
                    }
                    
                    for (const entry of capture.maps) {
                        if (entry.instance && 
                            typeof entry.instance.isStyleLoaded === 'function' &&
                            entry.instance.isStyleLoaded()) {
                            return { ready: true };
                        }
                    }
                    
                    return { ready: false, reason: 'style_not_loaded' };
                }
            """)
            
            if check_result.get('ready'):
                style_ready = True
                print("[Capture] Style is ready!")
                break
            
            await asyncio.sleep(0.5)
        
        if not style_ready:
            print("[Capture] Warning: Style may not be fully loaded")
            result.errors.append("Style load timeout - capture may be incomplete")
        
        # Extract captured data
        print("[Capture] Extracting map data...")
        extract_result = await page.evaluate(EXTRACT_DATA_SCRIPT)
        
        result.debug['interceptorReady'] = extract_result.get('interceptorReady')
        result.debug['mapCount'] = extract_result.get('mapCount')
        
        if extract_result.get('style'):
            result.style = extract_result['style']
            layer_count = len(result.style.get('layers', []))
            source_names = list(result.style.get('sources', {}).keys())
            print(f"[Capture] Style captured: {layer_count} layers, sources: {source_names}")
        else:
            result.errors.extend(extract_result.get('errors', []))
            print(f"[Capture] Style capture failed: {extract_result.get('errors')}")
        
        if extract_result.get('viewport'):
            result.viewport = extract_result['viewport']
            print(f"[Capture] Viewport: center={result.viewport['center']}, zoom={result.viewport['zoom']}")
        
        # Process captured network requests
        for url_key, entry in pending_responses.items():
            if 'data' not in entry:
                continue
            
            if entry['type'] == 'tile':
                tile_info = parse_tile_url(entry['url'])
                if tile_info:
                    result.tiles.append(TileCapture(
                        url=entry['url'],
                        z=tile_info['z'],
                        x=tile_info['x'],
                        y=tile_info['y'],
                        source=tile_info['source'],
                        format=tile_info['format'],
                        data=entry['data'],
                    ))
            else:
                result.resources.append(ResourceCapture(
                    url=entry['url'],
                    type=entry['type'],
                    data=entry['data'],
                    content_type=entry.get('content_type', ''),
                ))
        
        print(f"[Capture] Captured {len(result.tiles)} tiles, {len(result.resources)} resources")
        
        # Group tiles by source for logging
        tiles_by_source: dict[str, int] = {}
        for tile in result.tiles:
            tiles_by_source[tile.source] = tiles_by_source.get(tile.source, 0) + 1
        print(f"[Capture] Tiles by source: {tiles_by_source}")
        
    except Exception as e:
        error_msg = f"Capture failed: {str(e)}"
        print(f"[Capture] {error_msg}")
        result.errors.append(error_msg)
        
    finally:
        if browser:
            await browser.close()
    
    return result


def capture_result_to_bundle(result: CaptureResult) -> dict:
    """Convert CaptureResult to a capture bundle dict."""
    return {
        'version': '1.0',
        'metadata': {
            'url': result.url,
            'title': result.title,
            'capturedAt': result.captured_at,
            'captureMethod': 'puppeteer',
        },
        'viewport': result.viewport or {'center': [0, 0], 'zoom': 10},
        'style': result.style,
        'tiles': [
            {
                'sourceId': t.source,
                'z': t.z,
                'x': t.x,
                'y': t.y,
                'data': t.data,
                'format': t.format,
            }
            for t in result.tiles
        ],
        'resources': {
            r.url: {
                'type': r.type,
                'data': r.data,
                'contentType': r.content_type,
            }
            for r in result.resources
        },
        '_errors': result.errors,
        '_debug': result.debug,
    }
```

### 1.3 Update Modal App with Capture Endpoint

Update `cli/src/webmap_archiver/modal_app.py`:

```python
"""
Modal cloud deployment for WebMap Archiver.

Provides two capture methods:
1. POST /process - Process a pre-captured bundle (from extension DevTools)
2. POST /capture - Capture directly from URL using Puppeteer (recommended)
"""

import modal
import asyncio
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

app = modal.App("webmap-archiver")

# Image with Chromium for Puppeteer
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        # Chromium and dependencies
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
    memory=2048,  # Browser needs more memory
    cpu=2.0,
)
@modal.asgi_app()
def fastapi_app():
    """FastAPI app for the WebMap Archiver API."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
    from typing import Optional
    
    from webmap_archiver import (
        create_archive_from_bundle,
        inspect_bundle,
        CaptureValidationError,
        __version__,
    )
    from webmap_archiver.capture.browser_capture import (
        capture_map_from_url,
        capture_result_to_bundle,
    )

    web_app = FastAPI(
        title="WebMap Archiver API",
        description="Archive web maps with full style preservation",
        version=__version__,
    )

    class CaptureRequest(BaseModel):
        """Request to capture a map from URL."""
        url: str
        wait_for_idle: Optional[float] = 5.0
        wait_for_style: Optional[float] = 15.0

    @web_app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "webmap-archiver",
            "version": __version__,
            "capabilities": ["process", "capture"],
        }

    @web_app.post("/capture")
    async def capture(request: CaptureRequest):
        """
        Capture a map directly from URL using Puppeteer.
        
        This is the recommended method - it captures the complete style
        including programmatically added layers.
        """
        try:
            archive_id = str(uuid.uuid4())[:8]
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"
            
            print(f"[API] Capture request for {request.url}")
            print(f"[API] Archive ID: {archive_id}")
            
            # Capture using Puppeteer
            result = await capture_map_from_url(
                url=request.url,
                wait_for_idle=request.wait_for_idle,
                wait_for_style=request.wait_for_style,
                headless=True,
            )
            
            if not result.tiles:
                raise HTTPException(
                    status_code=400,
                    detail=f"No tiles captured from {request.url}. Errors: {result.errors}"
                )
            
            # Convert to bundle and create archive
            bundle = capture_result_to_bundle(result)
            
            archive_result = create_archive_from_bundle(
                bundle=bundle,
                output_path=output_path,
                verbose=True,
            )
            
            volume.commit()
            
            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            
            # Generate filename
            from urllib.parse import urlparse
            host = urlparse(request.url).netloc.replace(".", "-")
            date = datetime.now().strftime("%Y-%m-%d")
            filename = f"{host}-{date}.zip"
            
            return {
                "success": True,
                "archiveId": archive_id,
                "filename": filename,
                "downloadUrl": f"/download/{archive_id}",
                "expiresAt": expires_at.isoformat() + "Z",
                "size": archive_result.size,
                "tileCount": archive_result.tile_count,
                "tileSources": [
                    {
                        "name": ts.name,
                        "tileCount": ts.tile_count,
                        "discoveredLayers": ts.discovered_layers,
                    }
                    for ts in archive_result.tile_sources
                ],
                "styleInfo": {
                    "captured": result.style is not None,
                    "layerCount": len(result.style.get('layers', [])) if result.style else 0,
                    "sources": list(result.style.get('sources', {}).keys()) if result.style else [],
                },
                "warnings": result.errors,
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"[API] Capture error: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    @web_app.post("/process")
    async def process(bundle: dict):
        """
        Process a pre-captured bundle (from extension).
        
        Use /capture instead if possible - it provides better style capture.
        """
        try:
            archive_id = str(uuid.uuid4())[:8]
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"
            
            print(f"[API] Process request -> {archive_id}")
            
            result = create_archive_from_bundle(
                bundle=bundle,
                output_path=output_path,
                verbose=True,
            )
            
            volume.commit()
            
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            
            return {
                "success": True,
                "archiveId": archive_id,
                "downloadUrl": f"/download/{archive_id}",
                "expiresAt": expires_at.isoformat() + "Z",
                "size": result.size,
                "tileCount": result.tile_count,
            }
            
        except CaptureValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            print(f"[API] Process error: {e}")
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

## Part 2: Simplified Extension

With URL-based capture, the extension becomes much simpler.

### 2.1 Update Extension Popup

Update `extension/src/popup/popup.ts`:

```typescript
/**
 * Simplified popup for URL-based capture.
 */

import { API_BASE_URL } from '../config';

interface CaptureResponse {
  success: boolean;
  archiveId: string;
  filename: string;
  downloadUrl: string;
  size: number;
  tileCount: number;
  styleInfo?: {
    captured: boolean;
    layerCount: number;
    sources: string[];
  };
  warnings?: string[];
  error?: string;
}

class PopupController {
  private captureButton: HTMLButtonElement;
  private statusDiv: HTMLDivElement;
  private progressDiv: HTMLDivElement;
  private currentUrl: string = '';

  constructor() {
    this.captureButton = document.getElementById('capture-btn') as HTMLButtonElement;
    this.statusDiv = document.getElementById('status') as HTMLDivElement;
    this.progressDiv = document.getElementById('progress') as HTMLDivElement;
    
    this.init();
  }

  private async init() {
    // Get current tab URL
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    
    if (tab?.url) {
      this.currentUrl = tab.url;
      this.statusDiv.textContent = `Ready to archive: ${new URL(tab.url).hostname}`;
      this.captureButton.disabled = false;
    } else {
      this.statusDiv.textContent = 'Cannot archive this page';
      this.captureButton.disabled = true;
    }
    
    this.captureButton.addEventListener('click', () => this.startCapture());
  }

  private async startCapture() {
    if (!this.currentUrl) return;
    
    this.captureButton.disabled = true;
    this.statusDiv.textContent = 'Starting capture...';
    this.progressDiv.style.display = 'block';
    this.updateProgress(10, 'Sending to archive service...');
    
    try {
      // Call Modal capture endpoint
      const response = await fetch(`${API_BASE_URL}/capture`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: this.currentUrl,
          wait_for_idle: 5.0,
          wait_for_style: 15.0,
        }),
      });
      
      this.updateProgress(50, 'Processing map...');
      
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Capture failed');
      }
      
      const result: CaptureResponse = await response.json();
      
      this.updateProgress(80, 'Preparing download...');
      
      if (result.success) {
        // Trigger download
        const downloadUrl = `${API_BASE_URL}${result.downloadUrl}`;
        
        chrome.downloads.download({
          url: downloadUrl,
          filename: result.filename,
          saveAs: true,
        });
        
        this.updateProgress(100, 'Complete!');
        this.showSuccess(result);
      } else {
        throw new Error(result.error || 'Unknown error');
      }
      
    } catch (error) {
      console.error('Capture error:', error);
      this.showError(error instanceof Error ? error.message : 'Capture failed');
    } finally {
      this.captureButton.disabled = false;
    }
  }

  private updateProgress(percent: number, message: string) {
    const bar = this.progressDiv.querySelector('.progress-bar') as HTMLDivElement;
    const text = this.progressDiv.querySelector('.progress-text') as HTMLSpanElement;
    
    if (bar) bar.style.width = `${percent}%`;
    if (text) text.textContent = message;
  }

  private showSuccess(result: CaptureResponse) {
    let message = `✓ Archived ${result.tileCount} tiles`;
    
    if (result.styleInfo?.captured) {
      message += ` with ${result.styleInfo.layerCount} style layers`;
    }
    
    this.statusDiv.innerHTML = `
      <div class="success">
        ${message}
        <br>
        <small>Size: ${(result.size / 1024 / 1024).toFixed(2)} MB</small>
      </div>
    `;
    
    if (result.warnings?.length) {
      this.statusDiv.innerHTML += `
        <div class="warnings">
          <small>⚠ ${result.warnings.join(', ')}</small>
        </div>
      `;
    }
  }

  private showError(message: string) {
    this.statusDiv.innerHTML = `
      <div class="error">
        ✗ ${message}
      </div>
    `;
    this.progressDiv.style.display = 'none';
  }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  new PopupController();
});
```

### 2.2 Updated Popup HTML

Update `extension/src/popup/popup.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>WebMap Archiver</title>
  <link rel="stylesheet" href="popup.css">
</head>
<body>
  <div class="container">
    <h1>🗺️ WebMap Archiver</h1>
    
    <div id="status" class="status">
      Checking page...
    </div>
    
    <button id="capture-btn" class="capture-btn" disabled>
      Archive This Map
    </button>
    
    <div id="progress" class="progress" style="display: none;">
      <div class="progress-bar"></div>
      <span class="progress-text">Starting...</span>
    </div>
    
    <div class="footer">
      <small>Maps are captured and processed in the cloud.</small>
    </div>
  </div>
  
  <script src="popup.js"></script>
</body>
</html>
```

### 2.3 Simplified Manifest

The extension no longer needs DevTools or complex permissions:

```json
{
  "manifest_version": 3,
  "name": "WebMap Archiver",
  "version": "0.4.0",
  "description": "Archive web maps with one click",
  
  "permissions": [
    "activeTab",
    "downloads"
  ],
  
  "host_permissions": [
    "https://*.modal.run/*"
  ],
  
  "action": {
    "default_popup": "popup/popup.html",
    "default_icon": {
      "16": "icons/icon-16.png",
      "48": "icons/icon-48.png",
      "128": "icons/icon-128.png"
    }
  },
  
  "icons": {
    "16": "icons/icon-16.png",
    "48": "icons/icon-48.png",
    "128": "icons/icon-128.png"
  }
}
```

---

## Part 3: Deployment

### 3.1 Deploy Modal App

```bash
cd cli

# Install capture dependencies locally for testing
pip install -e ".[capture]"

# Deploy to Modal
modal deploy src/webmap_archiver/modal_app.py
```

### 3.2 Test Capture Endpoint

```bash
# Test with curl
curl -X POST https://your-modal-app.modal.run/capture \
  -H "Content-Type: application/json" \
  -d '{"url": "https://parkingregulations.nyc"}'

# Expected response:
{
  "success": true,
  "archiveId": "abc12345",
  "filename": "parkingregulations-nyc-2024-01-15.zip",
  "downloadUrl": "/download/abc12345",
  "tileCount": 150,
  "styleInfo": {
    "captured": true,
    "layerCount": 45,
    "sources": ["maptiler", "parking-regulations"]
  }
}
```

### 3.3 Build and Test Extension

```bash
cd extension

# Install dependencies
npm install

# Build
npm run build

# Load unpacked extension in Chrome
# Navigate to chrome://extensions
# Enable Developer mode
# Click "Load unpacked" and select extension/dist
```

---

## Part 4: Verification Checklist

After implementation, verify:

### Modal Capture
- [ ] `/health` returns capabilities: ["process", "capture"]
- [ ] `/capture` with parkingregulations.nyc URL succeeds
- [ ] Response shows `styleInfo.captured: true`
- [ ] Response shows both maptiler and parking-regulations sources
- [ ] Downloaded archive opens in pmtiles.io
- [ ] viewer.html shows properly styled layers

### Extension
- [ ] Popup shows current page URL
- [ ] "Archive This Map" button triggers capture
- [ ] Progress updates during capture
- [ ] Download triggers automatically
- [ ] Success message shows tile and layer counts

### Archive Quality
- [ ] viewer.html renders basemap with correct colors
- [ ] viewer.html renders data layer with correct colors
- [ ] Layer toggle controls work
- [ ] Tiles load in pmtiles.io independently

---

## Summary

This approach:

1. **Eliminates style capture problems** - Puppeteer's `evaluateOnNewDocument` guarantees we intercept the Map constructor
2. **Simplifies the extension** - Just sends URL, no complex DevTools integration
3. **Works with any framework** - React, Vue, vanilla JS, whatever
4. **Captures everything** - Style, tiles, sprites, glyphs
5. **Scales easily** - Modal handles compute, extension is stateless

The tradeoff is that cloud capture only works for public URLs. For authenticated/internal sites, users would need the local CLI with Puppeteer installed. But for the research/academic use case with public maps, this is the ideal solution.