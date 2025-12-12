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

    // Polling fallback - check periodically for libraries that might have been loaded
    let pollAttempts = 0;
    const maxPollAttempts = 50;  // 5 seconds total

    function pollForLibraries() {
        pollAttempts++;

        // Check if maplibregl exists and hasn't been patched
        if (window.maplibregl && window.maplibregl.Map && !window.maplibregl.Map.__webmap_patched__) {
            console.log('[WebMap Archiver] Polling found maplibregl - patching now');
            patchMapLibrary(window.maplibregl, 'maplibre');
        }

        // Check if mapboxgl exists and hasn't been patched
        if (window.mapboxgl && window.mapboxgl.Map && !window.mapboxgl.Map.__webmap_patched__) {
            console.log('[WebMap Archiver] Polling found mapboxgl - patching now');
            patchMapLibrary(window.mapboxgl, 'mapbox');
        }

        // Continue polling if we haven't found anything yet and haven't exceeded max attempts
        if (window.__WEBMAP_CAPTURE__.maps.length === 0 && pollAttempts < maxPollAttempts) {
            setTimeout(pollForLibraries, 100);
        }
    }

    // Start polling
    setTimeout(pollForLibraries, 100);

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

        # Log console messages for debugging
        page.on('console', lambda msg: print(f"[Browser Console] {msg.text}"))

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

        # Track request statistics
        request_stats = {'total': 0, 'tiles': 0, 'styles': 0, 'sprites': 0, 'glyphs': 0}

        async def on_request(request):
            """Track and continue requests."""
            req_url = request.url
            request_stats['total'] += 1

            # Track relevant requests
            if is_tile_request(req_url):
                request_stats['tiles'] += 1
                pending_responses[req_url] = {'type': 'tile', 'url': req_url}
                print(f"[Capture] Tile: {req_url}", flush=True)
            elif is_style_request(req_url):
                request_stats['styles'] += 1
                pending_responses[req_url] = {'type': 'style', 'url': req_url}
                print(f"[Capture] Style: {req_url}", flush=True)
            elif is_sprite_request(req_url):
                request_stats['sprites'] += 1
                rtype = 'sprite_png' if req_url.endswith('.png') else 'sprite_json'
                pending_responses[req_url] = {'type': rtype, 'url': req_url}
                print(f"[Capture] Sprite: {req_url}", flush=True)
            elif is_glyph_request(req_url):
                request_stats['glyphs'] += 1
                pending_responses[req_url] = {'type': 'glyph', 'url': req_url}
                print(f"[Capture] Glyph: {req_url}", flush=True)

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
        print(f"[Capture] Requests during load: {request_stats}")

        # Verify interceptor is present
        try:
            interceptor_check = await page.evaluate("""
                () => {
                    return {
                        present: typeof window.__WEBMAP_CAPTURE__ !== 'undefined',
                        ready: window.__WEBMAP_CAPTURE__?.ready || false,
                        version: window.__WEBMAP_CAPTURE__?.interceptorVersion || null,
                        maplibreglExists: typeof window.maplibregl !== 'undefined',
                        mapboxglExists: typeof window.mapboxgl !== 'undefined',
                    };
                }
            """)
            print(f"[Capture] Interceptor check: {interceptor_check}")
        except Exception as e:
            print(f"[Capture] ERROR checking interceptor: {e}")
            result.errors.append(f"Interceptor check failed: {e}")

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

        print(f"[Capture] Debug: interceptorReady={result.debug['interceptorReady']}, mapCount={result.debug['mapCount']}")

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

        # Log what resources we captured
        if result.resources:
            print(f"[Capture] Resources captured:")
            for res in result.resources:
                print(f"  - {res.type}: {res.url}")

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
