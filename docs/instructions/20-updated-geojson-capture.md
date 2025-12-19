# Unified Map Capture Implementation Guide

## For Claude Code - Fix GeoJSON and Style Capture

This document fixes the issue where the extension cannot find map instances in Mapbox GL JS sites (like the pizza restaurant example). The solution consolidates style capture, viewport extraction, and GeoJSON extraction into a single `inspectedWindow.eval()` call with robust map-finding logic.

## Problem

The current implementation:
1. `sendToContentScript({ type: "CAPTURE_STYLE" })` - relies on content script to find map
2. `extractGeoJSONSources()` - separate call that also tries to find map

Both are failing because:
- Mapbox GL JS doesn't set `container.__mapboxgl` or `container._map`
- The `map` variable is declared with `const map = ...` in script scope, NOT on `window`
- Content script's map detection logic is incomplete

## Solution

Replace both calls with a single `captureMapState()` function that:
1. Runs entirely in page context via `inspectedWindow.eval()`
2. Uses 5 different strategies to find the map instance
3. Extracts style, viewport, AND GeoJSON in one pass
4. Has extensive logging for debugging

---

## File to Modify

**File:** `extension/src/devtools/panel.ts`

---

## Step 1: Add the `captureMapState()` Function

Add this new function after the `extractGeoJSONSources()` function (around line 580, before the `// BUNDLE BUILDING` section):

```typescript
// ============================================================================
// UNIFIED MAP CAPTURE
// ============================================================================

/**
 * Capture complete map state: style, viewport, and GeoJSON data.
 * Uses robust map instance detection that works with Mapbox, MapLibre, and React apps.
 * This replaces both sendToContentScript({ type: "CAPTURE_STYLE" }) and extractGeoJSONSources().
 */
async function captureMapState(): Promise<{
  style: any;
  viewport: any;
  mapLibrary: any;
  error?: string;
}> {
  const captureScript = `
    (function() {
      const log = (msg) => console.log('[WebMap Archiver] ' + msg);
      const warn = (msg) => console.warn('[WebMap Archiver] ' + msg);
      const error = (msg) => console.error('[WebMap Archiver] ' + msg);

      try {
        // ============================================================
        // STEP 1: Find the map container
        // ============================================================
        const container = document.querySelector('.maplibregl-map, .mapboxgl-map');
        if (!container) {
          error('No map container found (.maplibregl-map or .mapboxgl-map)');
          return { error: 'No map container found' };
        }
        log('Found map container: ' + container.className);

        // ============================================================
        // STEP 2: Find the map instance using multiple strategies
        // ============================================================
        let map = null;
        let mapLibrary = { type: 'unknown', version: null };

        // Strategy 1: Check container properties
        const containerProps = ['__maplibregl', '__mapboxgl', '_map', 'map', '_maplibre', '_mapbox'];
        for (const prop of containerProps) {
          const candidate = container[prop];
          if (candidate && typeof candidate.getStyle === 'function') {
            log('Found map via container.' + prop);
            map = candidate;
            if (prop.includes('maplibre')) mapLibrary.type = 'maplibre';
            else if (prop.includes('mapbox')) mapLibrary.type = 'mapbox';
            break;
          }
        }

        // Strategy 2: Check window globals
        if (!map) {
          const windowProps = ['map', 'mapboxMap', 'maplibreMap', 'glMap', 'mainMap'];
          for (const prop of windowProps) {
            const candidate = window[prop];
            if (candidate && typeof candidate.getStyle === 'function') {
              log('Found map via window.' + prop);
              map = candidate;
              break;
            }
          }
        }

        // Strategy 3: Check mapboxgl/maplibregl globals for version info and map instances
        if (!map) {
          if (window.mapboxgl) {
            mapLibrary = { type: 'mapbox', version: window.mapboxgl.version };
            log('Detected Mapbox GL JS v' + (mapLibrary.version || 'unknown'));
          }
          if (window.maplibregl) {
            mapLibrary = { type: 'maplibre', version: window.maplibregl.version };
            log('Detected MapLibre GL JS v' + (mapLibrary.version || 'unknown'));
          }
        }

        // Strategy 4: Search all window properties for map-like objects
        if (!map) {
          log('Searching window properties for map instance...');
          const windowKeys = Object.getOwnPropertyNames(window);
          for (const key of windowKeys) {
            try {
              const obj = window[key];
              if (obj && 
                  typeof obj === 'object' && 
                  obj !== window &&
                  typeof obj.getStyle === 'function' &&
                  typeof obj.getCenter === 'function' &&
                  typeof obj.getZoom === 'function' &&
                  typeof obj.querySourceFeatures === 'function') {
                log('Found map via window["' + key + '"]');
                map = obj;
                break;
              }
            } catch (e) {
              // Some properties throw on access - ignore
            }
          }
        }

        // Strategy 5: React fiber deep search
        if (!map) {
          log('Trying React fiber search...');
          const fiberKey = Object.keys(container).find(k => 
            k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
          );
          
          if (fiberKey) {
            log('Found React fiber key: ' + fiberKey);
            
            const searchValue = (obj, depth, visited) => {
              if (!obj || depth > 30 || visited.has(obj)) return null;
              visited.add(obj);
              
              if (typeof obj === 'object' && 
                  typeof obj.getStyle === 'function' &&
                  typeof obj.getCenter === 'function') {
                return obj;
              }
              
              if (typeof obj !== 'object') return null;
              
              // Priority properties to check
              const priorityProps = ['current', 'map', '_map', 'memoizedState', 'memoizedProps', 
                                     'stateNode', 'child', 'return', 'ref', 'pendingProps'];
              
              for (const prop of priorityProps) {
                if (obj[prop]) {
                  const result = searchValue(obj[prop], depth + 1, visited);
                  if (result) return result;
                }
              }
              
              return null;
            };
            
            const fiber = container[fiberKey];
            map = searchValue(fiber, 0, new Set());
            
            if (map) {
              log('Found map via React fiber search');
            }
          }
        }

        if (!map) {
          error('Could not find map instance after all strategies');
          return { error: 'Map instance not found - the map variable may not be accessible' };
        }

        // Get library info from map if available
        if (map.version) {
          mapLibrary.version = map.version;
        }

        // ============================================================
        // STEP 3: Extract style
        // ============================================================
        log('Extracting style...');
        let style = null;
        try {
          style = map.getStyle();
          if (style) {
            log('Got style with ' + Object.keys(style.sources || {}).length + ' sources and ' + (style.layers || []).length + ' layers');
            log('Sources: ' + Object.keys(style.sources || {}).join(', '));
          }
        } catch (e) {
          error('Failed to get style: ' + e.message);
        }

        // ============================================================
        // STEP 4: Extract viewport
        // ============================================================
        log('Extracting viewport...');
        let viewport = null;
        try {
          const center = map.getCenter();
          const bounds = map.getBounds();
          viewport = {
            center: { lng: center.lng, lat: center.lat },
            zoom: map.getZoom(),
            bearing: map.getBearing ? map.getBearing() : 0,
            pitch: map.getPitch ? map.getPitch() : 0,
            bounds: bounds ? {
              _sw: { lng: bounds.getWest(), lat: bounds.getSouth() },
              _ne: { lng: bounds.getEast(), lat: bounds.getNorth() }
            } : null
          };
          log('Got viewport: center=' + center.lng.toFixed(4) + ',' + center.lat.toFixed(4) + ' zoom=' + viewport.zoom.toFixed(1));
        } catch (e) {
          error('Failed to get viewport: ' + e.message);
        }

        // ============================================================
        // STEP 5: Extract GeoJSON from all GeoJSON sources
        // ============================================================
        if (style && style.sources) {
          const geojsonSourceIds = Object.entries(style.sources)
            .filter(([, src]) => src.type === 'geojson')
            .map(([id]) => id);

          if (geojsonSourceIds.length > 0) {
            log('Found ' + geojsonSourceIds.length + ' GeoJSON sources: ' + geojsonSourceIds.join(', '));

            for (const sourceId of geojsonSourceIds) {
              try {
                // First try querySourceFeatures
                const features = map.querySourceFeatures(sourceId);
                log(sourceId + ': querySourceFeatures returned ' + (features ? features.length : 0) + ' features');

                if (features && features.length > 0) {
                  // Deduplicate by ID or geometry
                  const seen = new Map();
                  const unique = [];
                  for (const f of features) {
                    const key = f.id !== undefined ? String(f.id) : JSON.stringify(f.geometry);
                    if (!seen.has(key)) {
                      seen.set(key, true);
                      unique.push({
                        type: 'Feature',
                        geometry: f.geometry,
                        properties: f.properties || {},
                        ...(f.id !== undefined && { id: f.id })
                      });
                    }
                  }

                  style.sources[sourceId].data = {
                    type: 'FeatureCollection',
                    features: unique
                  };
                  
                  const sizeMB = (JSON.stringify(style.sources[sourceId].data).length / 1024 / 1024).toFixed(2);
                  log('Injected ' + unique.length + ' unique features into ' + sourceId + ' (' + sizeMB + ' MB)');
                } else {
                  // Fallback: try to get data from source object
                  const source = map.getSource(sourceId);
                  if (source) {
                    if (source._data) {
                      log('Using _data fallback for ' + sourceId);
                      style.sources[sourceId].data = source._data;
                    } else if (source._options && source._options.data) {
                      log('Using _options.data fallback for ' + sourceId);
                      style.sources[sourceId].data = source._options.data;
                    } else if (source.serialize) {
                      try {
                        const serialized = source.serialize();
                        if (serialized && serialized.data) {
                          log('Using serialize() fallback for ' + sourceId);
                          style.sources[sourceId].data = serialized.data;
                        }
                      } catch (e) {
                        warn('serialize() failed for ' + sourceId + ': ' + e.message);
                      }
                    } else {
                      warn('No data found for GeoJSON source ' + sourceId + ' - source may still be loading');
                    }
                  }
                }
              } catch (e) {
                error('Error extracting GeoJSON from ' + sourceId + ': ' + e.message);
              }
            }
          } else {
            log('No GeoJSON sources found in style');
          }
        }

        // ============================================================
        // STEP 6: Return results
        // ============================================================
        log('Capture complete');
        return {
          style: style,
          viewport: viewport,
          mapLibrary: mapLibrary
        };

      } catch (e) {
        error('Capture failed: ' + e.message);
        return { error: 'Capture failed: ' + e.message };
      }
    })();
  `;

  return new Promise((resolve) => {
    chrome.devtools.inspectedWindow.eval(
      captureScript,
      (result: any, error: any) => {
        if (error) {
          console.error("[WebMap Archiver] inspectedWindow.eval error:", error);
          resolve({ style: null, viewport: null, mapLibrary: null, error: String(error) });
          return;
        }
        resolve(result || { style: null, viewport: null, mapLibrary: null, error: 'No result returned' });
      }
    );
  });
}
```

---

## Step 2: Update the `stopRecording()` Function

Replace the entire `stopRecording()` function with this updated version:

```typescript
async function stopRecording(): Promise<void> {
  console.log("[WebMap Archiver] Stopping recording...");

  isRecording = false;
  chrome.devtools.network.onRequestFinished.removeListener(handleRequest);

  if (durationInterval) {
    clearInterval(durationInterval);
    durationInterval = null;
  }

  showState("processing");
  updateProgress(10, "Extracting map state...");

  try {
    // Get page info
    const pageInfo = await new Promise<{ url: string; title: string }>(
      (resolve) => {
        chrome.devtools.inspectedWindow.eval(
          `({ url: window.location.href, title: document.title })`,
          (result, error) => {
            if (error) {
              console.error("[WebMap Archiver] Failed to get page info:", error);
              resolve({ url: "https://unknown", title: "Unknown Page" });
            } else if (result && typeof result === "object") {
              const r = result as { url?: string; title?: string };
              resolve({
                url: r.url || "https://unknown",
                title: r.title || "Unknown Page",
              });
            } else {
              resolve({ url: "https://unknown", title: "Unknown Page" });
            }
          }
        );
      }
    );

    console.log("[WebMap Archiver] Page info:", pageInfo);

    updateProgress(20, "Extracting map style and GeoJSON...");

    // Capture style, viewport, AND GeoJSON in one unified call
    const captureResult = await captureMapState();

    console.log("[WebMap Archiver] Capture result:", {
      hasStyle: !!captureResult.style,
      hasViewport: !!captureResult.viewport,
      mapLibrary: captureResult.mapLibrary,
      error: captureResult.error,
    });

    if (captureResult.error) {
      console.warn("[WebMap Archiver] Capture warning:", captureResult.error);
      // Continue anyway - we may still have tiles from network capture
    }

    if (captureResult.style?.sources) {
      console.log("[WebMap Archiver] Captured sources:", Object.keys(captureResult.style.sources));
      
      // Log GeoJSON sources specifically
      const geojsonSources = Object.entries(captureResult.style.sources)
        .filter(([, src]: [string, any]) => src.type === 'geojson');
      
      for (const [name, src] of geojsonSources) {
        const data = (src as any).data;
        if (data && data.features) {
          console.log(`[WebMap Archiver] GeoJSON '${name}': ${data.features.length} features`);
        } else {
          console.log(`[WebMap Archiver] GeoJSON '${name}': no data captured`);
        }
      }
    }

    updateProgress(30, "Building capture bundle...");

    // Build capture bundle with the unified result
    const bundle = buildCaptureBundle(
      {
        style: captureResult.style || null,
        viewport: captureResult.viewport || null,
        mapLibrary: captureResult.mapLibrary || null,
      },
      pageInfo
    );
    lastBundle = bundle;

    console.log("[WebMap Archiver] Bundle built:", {
      tiles: bundle.tiles?.length || 0,
      harEntries: bundle.har?.log?.entries?.length || 0,
      hasStyle: !!bundle.style,
      styleSourceCount: bundle.style ? Object.keys(bundle.style.sources || {}).length : 0,
      styleLayerCount: bundle.style?.layers?.length || 0,
    });

    updateProgress(50, "Uploading to cloud...");

    // Send to service worker for processing
    const result = await chrome.runtime.sendMessage({
      type: "PROCESS_BUNDLE",
      bundle,
    });

    if (result.success) {
      updateProgress(90, "Preparing download...");

      lastDownloadUrl = result.downloadUrl;
      lastFilename = result.filename;

      chrome.runtime.sendMessage({
        type: "CAPTURE_STOPPED",
        tabId: chrome.devtools.inspectedWindow.tabId,
      });

      showComplete(
        result.filename,
        bundle.tiles?.length || 0,
        result.size || 0
      );
    } else if (result.fallbackToDownload) {
      lastFilename = generateFilename(pageInfo.url);

      chrome.runtime.sendMessage({
        type: "CAPTURE_STOPPED",
        tabId: chrome.devtools.inspectedWindow.tabId,
      });

      showComplete(lastFilename, bundle.tiles?.length || 0, totalSize, true);
    } else {
      throw new Error(result.error || "Processing failed");
    }
  } catch (e) {
    console.error("[WebMap Archiver] Error:", e);
    showError(String(e));
  }
}
```

---

## Step 3: Remove the Old `extractGeoJSONSources()` Function

Find and **delete** the entire `extractGeoJSONSources()` function. It should be around lines 470-570 in the original file. The function starts with:

```typescript
/**
 * Extract GeoJSON data from all GeoJSON sources using querySourceFeatures().
 * This is called at "Stop Capture" time, after async data has loaded.
 */
async function extractGeoJSONSources(style: any): Promise<any> {
```

Delete from that comment to the closing brace of the function.

---

## Step 4: Keep `sendToContentScript()` for Other Uses

The `sendToContentScript()` function is still used by `checkForMaps()` for the initial map detection. **Do not delete it.** Only the `CAPTURE_STYLE` message type is no longer used.

---

## Testing

### Test 1: Pizza Restaurant Map (Mapbox GL JS with dynamic GeoJSON)

**URL:** https://mapping-systems.github.io/sample-mapbox-webmap/sample_map.html

1. Open the page and wait 3-5 seconds for pizza points to load
2. Open DevTools, go to WebMap Archiver panel
3. Click "Start Capture"
4. Pan/zoom briefly to capture some basemap tiles
5. Click "Stop Capture"

**Expected console output:**
```
[WebMap Archiver] Found map container: mapboxgl-map
[WebMap Archiver] Searching window properties for map instance...
[WebMap Archiver] Found map via window["map"]
[WebMap Archiver] Got style with N sources and M layers
[WebMap Archiver] Sources: mapbox, restaurants
[WebMap Archiver] Found 1 GeoJSON sources: restaurants
[WebMap Archiver] restaurants: querySourceFeatures returned ~10000 features
[WebMap Archiver] Injected 10000 unique features into restaurants (X.XX MB)
[WebMap Archiver] Capture complete
```

### Test 2: MapLibre Example (Inline GeoJSON)

**URL:** https://maplibre.org/maplibre-gl-js/docs/examples/add-a-geojson-line/

Note: This example is in an iframe, which may require additional handling.

### Test 3: Parking Regulations (React + MapLibre)

**URL:** https://parkingregulations.nyc/

This should continue to work with the React fiber search strategy.

---

## Summary of Changes

| Action | Location | Description |
|--------|----------|-------------|
| ADD | After line ~580 | New `captureMapState()` function |
| REPLACE | `stopRecording()` function | Use `captureMapState()` instead of `sendToContentScript` + `extractGeoJSONSources` |
| DELETE | `extractGeoJSONSources()` | No longer needed - functionality moved to `captureMapState()` |
| KEEP | `sendToContentScript()` | Still used for `checkForMaps()` |

## Key Improvements

1. **5 map-finding strategies** in order:
   - Container properties (`__maplibregl`, `__mapboxgl`, `_map`, etc.)
   - Window globals (`window.map`, `window.mapboxMap`, etc.)
   - Library globals (`window.mapboxgl`, `window.maplibregl`)
   - Window property search (finds `const map = ...` declarations)
   - React fiber deep search (for React-wrapped maps)

2. **Unified extraction** - Style, viewport, and GeoJSON in one eval call

3. **Multiple GeoJSON fallbacks**:
   - `querySourceFeatures()` (official API)
   - `source._data` (internal property)
   - `source._options.data` (internal property)
   - `source.serialize()` (some implementations)

4. **Extensive logging** - Every step logged to console for debugging