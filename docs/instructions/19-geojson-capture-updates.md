# GeoJSON Capture via querySourceFeatures() Implementation Guide

## For Claude Code - v0.3.x

This document covers implementing reliable GeoJSON source capture using MapLibre/Mapbox's `querySourceFeatures()` API.

## Problem

`map.getStyle()` intentionally omits GeoJSON data from sources because:
- Data can be very large (megabytes)
- Data is already in the map's internal state
- API wasn't designed to serialize data back out

This means inline, fetched, and dynamically-added GeoJSON sources appear in the style as:
```javascript
{
  sources: {
    restaurants: {
      type: 'geojson'
      // ❌ No 'data' property!
    }
  }
}
```

## Solution

Use `map.querySourceFeatures(sourceId)` to extract features at capture time, then reconstruct the GeoJSON FeatureCollection and inject it into the style.

**Key insight**: This is called at "Stop Capture" time, after:
- Page has reloaded
- Async fetches have completed
- Dynamic sources have been added
- All transformations have been applied

---

## Implementation

### Step 1: Create GeoJSON Extractor Function

**File:** `extension/src/content/capturer.ts`

Add a new function to extract GeoJSON data from all sources:

```typescript
/**
 * Extract GeoJSON data from all GeoJSON sources in the map.
 * Uses querySourceFeatures() which returns the actual loaded features,
 * regardless of how they were added (inline, fetched, dynamic).
 * 
 * @param map - The MapLibre/Mapbox map instance
 * @param style - The style object from map.getStyle()
 * @returns Style with GeoJSON data injected into sources
 */
function extractGeoJSONSources(map: any, style: any): any {
  if (!style || !style.sources) {
    return style;
  }

  const geojsonSourceIds: string[] = [];
  
  // Find all GeoJSON sources
  for (const [sourceId, sourceDef] of Object.entries(style.sources)) {
    if ((sourceDef as any).type === 'geojson') {
      geojsonSourceIds.push(sourceId);
    }
  }

  if (geojsonSourceIds.length === 0) {
    console.log('[WebMap Archiver] No GeoJSON sources found');
    return style;
  }

  console.log(`[WebMap Archiver] Found ${geojsonSourceIds.length} GeoJSON sources: ${geojsonSourceIds.join(', ')}`);

  // Extract features from each GeoJSON source
  for (const sourceId of geojsonSourceIds) {
    try {
      // Check if source exists and is loaded
      const source = map.getSource(sourceId);
      if (!source) {
        console.warn(`[WebMap Archiver] Source '${sourceId}' not found on map`);
        continue;
      }

      // Query all features from this source
      // For GeoJSON sources, we don't need to specify sourceLayer
      const features = map.querySourceFeatures(sourceId);
      
      if (!features || features.length === 0) {
        console.warn(`[WebMap Archiver] No features found in source '${sourceId}' - may still be loading`);
        
        // Try alternative: check if source has _data property (internal)
        if (source._data) {
          console.log(`[WebMap Archiver] Found _data property for '${sourceId}'`);
          style.sources[sourceId].data = source._data;
          continue;
        }
        
        continue;
      }

      console.log(`[WebMap Archiver] Extracted ${features.length} features from '${sourceId}'`);

      // Reconstruct GeoJSON FeatureCollection
      // Note: querySourceFeatures returns features that may have been tiled internally,
      // so we need to deduplicate by feature ID if present
      const uniqueFeatures = deduplicateFeatures(features);
      
      const featureCollection: GeoJSON.FeatureCollection = {
        type: 'FeatureCollection',
        features: uniqueFeatures.map(f => ({
          type: 'Feature',
          geometry: f.geometry,
          properties: f.properties,
          ...(f.id !== undefined && { id: f.id }),
        })),
      };

      // Inject into style
      style.sources[sourceId].data = featureCollection;
      
      console.log(`[WebMap Archiver] Injected ${uniqueFeatures.length} unique features into '${sourceId}'`);

    } catch (error) {
      console.error(`[WebMap Archiver] Error extracting GeoJSON from '${sourceId}':`, error);
    }
  }

  return style;
}


/**
 * Deduplicate features by ID.
 * querySourceFeatures() may return duplicate features if the data has been
 * internally tiled by the map library.
 */
function deduplicateFeatures(features: any[]): any[] {
  const seen = new Map<string | number, any>();
  const noIdFeatures: any[] = [];

  for (const feature of features) {
    if (feature.id !== undefined && feature.id !== null) {
      // Use ID for deduplication
      if (!seen.has(feature.id)) {
        seen.set(feature.id, feature);
      }
    } else {
      // No ID - try to dedupe by geometry hash
      const geomKey = JSON.stringify(feature.geometry);
      if (!seen.has(geomKey)) {
        seen.set(geomKey, feature);
      }
    }
  }

  return Array.from(seen.values());
}
```

### Step 2: Integrate into Capture Flow

**File:** `extension/src/content/capturer.ts`

Find where `map.getStyle()` is called and add the GeoJSON extraction after it:

```typescript
// In the capture function (wherever getStyle is called)

async function captureMapState(map: any): Promise<CapturedMapState> {
  // Get base style
  let style = map.getStyle();
  
  if (!style) {
    throw new Error('Could not get map style');
  }

  // Extract GeoJSON data from all GeoJSON sources
  // This uses querySourceFeatures() to get actual loaded data
  style = extractGeoJSONSources(map, style);

  // Get viewport
  const center = map.getCenter();
  const viewport = {
    center: [center.lng, center.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
    bounds: map.getBounds().toArray(),
  };

  return {
    style,
    viewport,
  };
}
```

### Step 3: Update Panel to Call Capture at Right Time

**File:** `extension/src/devtools/panel.ts`

Ensure GeoJSON extraction happens when user clicks "Stop Capture", not during continuous capture:

```typescript
async function stopCapture(): Promise<CaptureBundle> {
  // Stop network capture
  isCapturing = false;
  
  // Now extract the final map state including GeoJSON
  // This runs AFTER async data has loaded
  const mapState = await chrome.devtools.inspectedWindow.eval(`
    (function() {
      // Find the map instance
      const container = document.querySelector('.maplibregl-map, .mapboxgl-map');
      if (!container) return { error: 'No map container found' };
      
      const map = container.__maplibregl || container._map || container.__mapboxgl;
      if (!map) return { error: 'No map instance found' };
      
      // Get style
      let style = map.getStyle();
      if (!style) return { error: 'Could not get style' };
      
      // Extract GeoJSON from all GeoJSON sources
      const geojsonSourceIds = Object.entries(style.sources || {})
        .filter(([id, src]) => src.type === 'geojson')
        .map(([id]) => id);
      
      console.log('[WebMap Archiver] GeoJSON sources:', geojsonSourceIds);
      
      for (const sourceId of geojsonSourceIds) {
        try {
          const features = map.querySourceFeatures(sourceId);
          console.log('[WebMap Archiver] ' + sourceId + ': ' + features.length + ' features');
          
          if (features.length > 0) {
            // Deduplicate features
            const seen = new Map();
            const unique = [];
            for (const f of features) {
              const key = f.id !== undefined ? f.id : JSON.stringify(f.geometry);
              if (!seen.has(key)) {
                seen.set(key, true);
                unique.push({
                  type: 'Feature',
                  geometry: f.geometry,
                  properties: f.properties,
                  ...(f.id !== undefined && { id: f.id }),
                });
              }
            }
            
            style.sources[sourceId].data = {
              type: 'FeatureCollection',
              features: unique,
            };
            
            console.log('[WebMap Archiver] Injected ' + unique.length + ' features into ' + sourceId);
          } else {
            // Try internal _data as fallback
            const source = map.getSource(sourceId);
            if (source && source._data) {
              style.sources[sourceId].data = source._data;
              console.log('[WebMap Archiver] Used _data fallback for ' + sourceId);
            }
          }
        } catch (e) {
          console.error('[WebMap Archiver] Error extracting ' + sourceId + ':', e);
        }
      }
      
      // Get viewport
      const center = map.getCenter();
      const bounds = map.getBounds();
      
      return {
        style: style,
        viewport: {
          center: [center.lng, center.lat],
          zoom: map.getZoom(),
          bearing: map.getBearing(),
          pitch: map.getPitch(),
          bounds: {
            west: bounds.getWest(),
            south: bounds.getSouth(),
            east: bounds.getEast(),
            north: bounds.getNorth(),
          },
        },
      };
    })();
  `);
  
  if (mapState.error) {
    console.error('[WebMap Archiver] Map state extraction failed:', mapState.error);
  }
  
  // Build the capture bundle
  return buildCaptureBundle({
    style: mapState.style,
    viewport: mapState.viewport,
    tiles: capturedTiles,
    resources: {
      sprites: capturedSprites,
      glyphs: capturedGlyphs,
    },
  });
}
```

### Step 4: Handle Large GeoJSON Sources

For very large GeoJSON sources (>1MB), we may want to:
1. Warn the user
2. Optionally convert to vector tiles via tippecanoe (backend)

**File:** `extension/src/devtools/panel.ts`

Add size checking:

```typescript
// After extracting GeoJSON, check sizes
for (const [sourceId, sourceDef] of Object.entries(style.sources || {})) {
  if (sourceDef.type === 'geojson' && sourceDef.data) {
    const sizeBytes = JSON.stringify(sourceDef.data).length;
    const sizeMB = sizeBytes / (1024 * 1024);
    
    if (sizeMB > 5) {
      console.warn(`[WebMap Archiver] Large GeoJSON source '${sourceId}': ${sizeMB.toFixed(1)}MB`);
      console.warn('[WebMap Archiver] Consider using tippecanoe conversion for better performance');
    }
    
    console.log(`[WebMap Archiver] GeoJSON '${sourceId}': ${sizeMB.toFixed(2)}MB, ${sourceDef.data.features?.length || 0} features`);
  }
}
```

---

## Edge Cases to Handle

### 1. Source Still Loading

If `querySourceFeatures()` returns empty array, the source may still be loading:

```typescript
if (features.length === 0) {
  // Check if source exists but has no data yet
  const source = map.getSource(sourceId);
  if (source) {
    console.warn(`[WebMap Archiver] Source '${sourceId}' exists but returned no features`);
    console.warn('[WebMap Archiver] Data may still be loading - try waiting longer');
  }
}
```

### 2. Clustered Sources

GeoJSON sources with clustering enabled behave differently:

```typescript
const sourceDef = style.sources[sourceId];
if (sourceDef.cluster) {
  // For clustered sources, querySourceFeatures returns cluster points at current zoom
  // We need the original unclustered data
  console.warn(`[WebMap Archiver] Source '${sourceId}' has clustering - extracting may return cluster points`);
  
  // Try to get original data from internal property
  const source = map.getSource(sourceId);
  if (source._data) {
    style.sources[sourceId].data = source._data;
  } else if (source._options?.data) {
    style.sources[sourceId].data = source._options.data;
  }
}
```

### 3. GeoJSON from URL (not inline)

If the original source was a URL, `getStyle()` may return that URL:

```typescript
const sourceDef = style.sources[sourceId];
if (typeof sourceDef.data === 'string' && sourceDef.data.startsWith('http')) {
  // Data is a URL - features are loaded, extract them
  console.log(`[WebMap Archiver] Source '${sourceId}' loaded from URL: ${sourceDef.data}`);
  
  const features = map.querySourceFeatures(sourceId);
  if (features.length > 0) {
    // Replace URL with actual data
    style.sources[sourceId].data = {
      type: 'FeatureCollection',
      features: deduplicateFeatures(features).map(f => ({
        type: 'Feature',
        geometry: f.geometry,
        properties: f.properties,
      })),
    };
  }
}
```

---

## Testing

### Test Site 1: NYC Pizza Restaurants (Dynamic Fetch + Transform)

**URL:** https://mapping-systems.github.io/sample-mapbox-webmap/sample_map.html

**Expected behavior:**
1. Open page, wait 3-5 seconds for pizza points to appear
2. Start capture in extension
3. Pan/zoom to capture tiles
4. Stop capture
5. Logs should show:
   ```
   [WebMap Archiver] GeoJSON sources: ['restaurants']
   [WebMap Archiver] restaurants: ~10000 features
   [WebMap Archiver] Injected 10000 features into 'restaurants'
   ```
6. Archive should contain restaurants as GeoJSON in style

### Test Site 2: MapLibre Inline GeoJSON (Iframe)

**URL:** https://maplibre.org/maplibre-gl-js/docs/examples/add-a-geojson-line/

**Note:** This example is in an iframe. The extension may need to target the iframe's content.

**Expected behavior:**
1. Open page
2. Start capture
3. Stop capture
4. Logs should show the line GeoJSON source captured

### Manual Console Test

Before implementing, verify the approach works in browser DevTools:

```javascript
// Run this in the console on the pizza map
const map = document.querySelector('.mapboxgl-map').__mapboxgl || 
            document.querySelector('.mapboxgl-map')._map;

// Get all GeoJSON sources
const style = map.getStyle();
const geojsonSources = Object.entries(style.sources)
  .filter(([k,v]) => v.type === 'geojson');

console.log('GeoJSON sources:', geojsonSources.map(([k]) => k));

// Query features from each
for (const [sourceId, sourceDef] of geojsonSources) {
  const features = map.querySourceFeatures(sourceId);
  console.log(`${sourceId}: ${features.length} features`);
  
  if (features.length > 0) {
    console.log('Sample feature:', features[0]);
  }
}
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `extension/src/content/capturer.ts` | Add `extractGeoJSONSources()` and `deduplicateFeatures()` functions |
| `extension/src/devtools/panel.ts` | Call GeoJSON extraction in `stopCapture()`, add size logging |

**Key points:**
1. Use `map.querySourceFeatures(sourceId)` - the official API for getting loaded features
2. Call at "Stop Capture" time, after async data has loaded
3. Deduplicate features (map may have internally tiled them)
4. Handle edge cases: clustering, URL sources, still-loading sources
5. Log sizes to warn about large sources