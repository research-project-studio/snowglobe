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
        print(f"[StyleExtractor] Launching browser...", flush=True)

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

        print(f"[StyleExtractor] Navigating to {url}...", flush=True)

        await page.goto(url, {
            'waitUntil': 'networkidle2',
            'timeout': int(timeout * 1000),
        })

        title = await page.title()
        print(f"[StyleExtractor] Page loaded: {title}", flush=True)

        # Wait for initial load
        print(f"[StyleExtractor] Waiting {wait_for_load}s for map initialization...", flush=True)
        await asyncio.sleep(wait_for_load)

        # Poll for style
        print(f"[StyleExtractor] Searching for map instance...", flush=True)

        start_time = asyncio.get_event_loop().time()
        last_error = None
        attempts = 0

        while asyncio.get_event_loop().time() - start_time < wait_for_style:
            attempts += 1

            result = await page.evaluate(STYLE_EXTRACTION_SCRIPT)

            if result.get('success'):
                style = result.get('style', {})
                print(f"[StyleExtractor] SUCCESS via {result.get('debug', {}).get('method')}", flush=True)
                print(f"[StyleExtractor] Layers: {len(style.get('layers', []))}", flush=True)
                print(f"[StyleExtractor] Sources: {list(style.get('sources', {}).keys())}", flush=True)

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
                print(f"[StyleExtractor] Attempt {attempts}: searched {nodes_searched} nodes, retrying...", flush=True)

            await asyncio.sleep(1.0)

        print(f"[StyleExtractor] FAILED after {attempts} attempts: {last_error}", flush=True)

        return ExtractedStyle(
            success=False,
            error=last_error or 'Style extraction timed out',
            debug={'attempts': attempts},
        )

    except Exception as e:
        print(f"[StyleExtractor] Error: {e}", flush=True)
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
            print(f"[StyleExtractor] Retry attempt {attempt}...", flush=True)
            await asyncio.sleep(2.0)

        result = await extract_style_from_url(url, **kwargs)
        last_result = result

        if result.success:
            return result

    return last_result
