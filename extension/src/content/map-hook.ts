/**
 * Map constructor hook - injected at document_start to capture map instances.
 * This script runs in the page context and intercepts Mapbox/MapLibre constructors.
 */

export const mapHookScript = `
(function() {
  const log = (msg) => console.log('[WebMap Archiver Hook] ' + msg);

  log('Installing map constructor hooks...');

  // Hook Mapbox GL JS constructor
  if (window.mapboxgl && window.mapboxgl.Map) {
    const OriginalMap = window.mapboxgl.Map;
    window.mapboxgl.Map = function(...args) {
      const instance = new OriginalMap(...args);
      log('Captured Mapbox GL JS map instance');
      window.__webmapArchiverMapInstance = instance;
      return instance;
    };
    // Preserve prototype and static properties
    window.mapboxgl.Map.prototype = OriginalMap.prototype;
    Object.setPrototypeOf(window.mapboxgl.Map, OriginalMap);
    log('Mapbox GL JS constructor hooked');
  }

  // Hook MapLibre GL JS constructor
  if (window.maplibregl && window.maplibregl.Map) {
    const OriginalMap = window.maplibregl.Map;
    window.maplibregl.Map = function(...args) {
      const instance = new OriginalMap(...args);
      log('Captured MapLibre GL JS map instance');
      window.__webmapArchiverMapInstance = instance;
      return instance;
    };
    window.maplibregl.Map.prototype = OriginalMap.prototype;
    Object.setPrototypeOf(window.maplibregl.Map, OriginalMap);
    log('MapLibre GL JS constructor hooked');
  }

  // If libraries haven't loaded yet, watch for them
  if (!window.mapboxgl && !window.maplibregl) {
    log('Libraries not loaded yet, watching for them...');

    const checkInterval = setInterval(() => {
      if (window.mapboxgl && window.mapboxgl.Map && !window.mapboxgl.Map.__hooked) {
        const OriginalMap = window.mapboxgl.Map;
        window.mapboxgl.Map = function(...args) {
          const instance = new OriginalMap(...args);
          log('Captured Mapbox GL JS map instance (delayed)');
          window.__webmapArchiverMapInstance = instance;
          return instance;
        };
        window.mapboxgl.Map.prototype = OriginalMap.prototype;
        Object.setPrototypeOf(window.mapboxgl.Map, OriginalMap);
        window.mapboxgl.Map.__hooked = true;
        log('Mapbox GL JS constructor hooked (delayed)');
        clearInterval(checkInterval);
      }

      if (window.maplibregl && window.maplibregl.Map && !window.maplibregl.Map.__hooked) {
        const OriginalMap = window.maplibregl.Map;
        window.maplibregl.Map = function(...args) {
          const instance = new OriginalMap(...args);
          log('Captured MapLibre GL JS map instance (delayed)');
          window.__webmapArchiverMapInstance = instance;
          return instance;
        };
        window.maplibregl.Map.prototype = OriginalMap.prototype;
        Object.setPrototypeOf(window.maplibregl.Map, OriginalMap);
        window.maplibregl.Map.__hooked = true;
        log('MapLibre GL JS constructor hooked (delayed)');
        clearInterval(checkInterval);
      }
    }, 50);

    // Stop checking after 5 seconds
    setTimeout(() => clearInterval(checkInterval), 5000);
  }
})();
`;
