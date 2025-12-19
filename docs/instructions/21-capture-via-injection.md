Fix: Inject Script via DOM
File: extension/src/devtools/panel.ts
Replace the captureMapState() function with this two-step approach:
typescript/**
 * Capture complete map state by injecting a script into the page.
 * This works around inspectedWindow.eval() not having access to 
 * const/let variables in the global lexical scope.
 */
async function captureMapState(): Promise<{
  style: any;
  viewport: any;
  mapLibrary: any;
  error?: string;
}> {
  // Step 1: Inject a script element that captures map state and stores on window
  const injectScript = `
    (function() {
      // Create and inject a script that runs in the page's context
      const script = document.createElement('script');
      script.id = '__webmap_archiver_capture__';
      script.textContent = \`
        (function() {
          const log = (msg) => console.log('[WebMap Archiver Injected] ' + msg);
          const error = (msg) => console.error('[WebMap Archiver Injected] ' + msg);
          
          try {
            // Clean up any previous capture
            delete window.__webmapArchiverResult;
            
            log('Starting capture...');
            
            // Try to find map using common variable names
            let map = null;
            const varNames = ['map', 'mapInstance', 'mapboxMap', 'maplibreMap', 'glMap', 'mainMap', 'myMap', 'webmap', 'mapView'];
            
            for (const varName of varNames) {
              try {
                const candidate = eval(varName);
                if (candidate && typeof candidate === 'object' && typeof candidate.getStyle === 'function') {
                  log('Found map via variable: ' + varName);
                  map = candidate;
                  break;
                }
              } catch (e) {
                // Variable doesn't exist
              }
            }
            
            // Also check window properties as fallback
            if (!map) {
              const container = document.querySelector('.maplibregl-map, .mapboxgl-map');
              if (container) {
                const containerProps = ['__maplibregl', '__mapboxgl', '_map'];
                for (const prop of containerProps) {
                  if (container[prop] && typeof container[prop].getStyle === 'function') {
                    log('Found map via container.' + prop);
                    map = container[prop];
                    break;
                  }
                }
              }
            }
            
            if (!map) {
              error('Could not find map instance');
              window.__webmapArchiverResult = { error: 'Map instance not found' };
              return;
            }
            
            // Detect library
            let mapLibrary = { type: 'unknown', version: null };
            if (window.mapboxgl) {
              mapLibrary = { type: 'mapbox', version: window.mapboxgl.version };
            } else if (window.maplibregl) {
              mapLibrary = { type: 'maplibre', version: window.maplibregl.version };
            }
            log('Library: ' + mapLibrary.type + ' v' + mapLibrary.version);
            
            // Extract style
            let style = null;
            try {
              style = map.getStyle();
              log('Got style with ' + Object.keys(style.sources || {}).length + ' sources');
            } catch (e) {
              error('Failed to get style: ' + e.message);
            }
            
            // Extract viewport
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
              log('Got viewport: zoom=' + viewport.zoom.toFixed(1));
            } catch (e) {
              error('Failed to get viewport: ' + e.message);
            }
            
            // Extract GeoJSON from sources
            if (style && style.sources) {
              const geojsonSourceIds = Object.entries(style.sources)
                .filter(([, src]) => src.type === 'geojson')
                .map(([id]) => id);
              
              log('GeoJSON sources: ' + geojsonSourceIds.join(', '));
              
              for (const sourceId of geojsonSourceIds) {
                try {
                  const features = map.querySourceFeatures(sourceId);
                  log(sourceId + ': ' + (features ? features.length : 0) + ' features from querySourceFeatures');
                  
                  if (features && features.length > 0) {
                    // Deduplicate
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
                    log(sourceId + ': injected ' + unique.length + ' unique features');
                  } else {
                    // Fallbacks
                    const source = map.getSource(sourceId);
                    if (source && source._data) {
                      style.sources[sourceId].data = source._data;
                      log(sourceId + ': used _data fallback');
                    }
                  }
                } catch (e) {
                  error(sourceId + ' extraction failed: ' + e.message);
                }
              }
            }
            
            // Store result on window
            window.__webmapArchiverResult = {
              style: style,
              viewport: viewport,
              mapLibrary: mapLibrary
            };
            
            log('Capture complete - result stored on window.__webmapArchiverResult');
            
          } catch (e) {
            error('Capture failed: ' + e.message);
            window.__webmapArchiverResult = { error: e.message };
          }
        })();
      \`;
      
      // Remove any existing script
      const existing = document.getElementById('__webmap_archiver_capture__');
      if (existing) existing.remove();
      
      // Inject and execute
      document.head.appendChild(script);
      
      return 'injected';
    })();
  `;

  // Step 2: Read the result from window
  const readResultScript = `window.__webmapArchiverResult`;

  return new Promise((resolve) => {
    // First, inject the capture script
    chrome.devtools.inspectedWindow.eval(injectScript, (injected, injectError) => {
      if (injectError) {
        console.error("[WebMap Archiver] Script injection failed:", injectError);
        resolve({ style: null, viewport: null, mapLibrary: null, error: String(injectError) });
        return;
      }

      console.log("[WebMap Archiver] Script injected, reading result...");

      // Small delay to ensure script has executed
      setTimeout(() => {
        // Then read the result
        chrome.devtools.inspectedWindow.eval(readResultScript, (result: any, readError) => {
          if (readError) {
            console.error("[WebMap Archiver] Failed to read result:", readError);
            resolve({ style: null, viewport: null, mapLibrary: null, error: String(readError) });
            return;
          }

          if (!result) {
            console.error("[WebMap Archiver] No result from injected script");
            resolve({ style: null, viewport: null, mapLibrary: null, error: "No result from capture" });
            return;
          }

          console.log("[WebMap Archiver] Capture result received:", {
            hasStyle: !!result.style,
            hasViewport: !!result.viewport,
            error: result.error
          });

          resolve(result);
        });
      }, 100); // 100ms delay for script execution
    });
  });
}
```

---

## Why This Works
```
inspectedWindow.eval() 
    → creates <script> element
        → script runs in PAGE CONTEXT (same as console!)
            → can access "const map"
            → stores result on window.__webmapArchiverResult
    
inspectedWindow.eval() (again)
    → reads window.__webmapArchiverResult
    → this DOES work because window properties are accessible
```

The key insight: we can't directly access `const map` from `inspectedWindow.eval()`, but an injected `<script>` CAN access it. Then we communicate the result via `window` which IS accessible from `inspectedWindow.eval()`.

---

## Testing

After implementing, test on the pizza map. You should see in the **page console** (not DevTools console):
```
[WebMap Archiver Injected] Starting capture...
[WebMap Archiver Injected] Found map via variable: map
[WebMap Archiver Injected] Library: mapbox v3.5.1
[WebMap Archiver Injected] Got style with N sources
[WebMap Archiver Injected] GeoJSON sources: restaurants
[WebMap Archiver Injected] restaurants: 10000 features from querySourceFeatures
[WebMap Archiver Injected] restaurants: injected 10000 unique features
[WebMap Archiver Injected] Capture complete