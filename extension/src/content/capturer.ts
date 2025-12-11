/**
 * Capture map state (style, viewport) from detected maps.
 */

import { DetectedMap, MapLibreMap, LeafletMap } from "../types/map-libraries";
import {
  CaptureViewport,
  MapLibreStyle,
  CaptureMetadata,
} from "../types/capture-bundle";

export interface CaptureResult {
  metadata: CaptureMetadata;
  viewport: CaptureViewport;
  style?: MapLibreStyle;
}

export class MapCapturer {
  /**
   * Capture state from a detected map.
   */
  async capture(map: DetectedMap): Promise<CaptureResult> {
    const metadata = this.captureMetadata(map);
    const viewport = this.captureViewport(map);
    const style = await this.captureStyle(map);

    return { metadata, viewport, style };
  }

  private captureMetadata(map: DetectedMap): CaptureMetadata {
    return {
      url: window.location.href,
      title: document.title,
      capturedAt: new Date().toISOString(),
      userAgent: navigator.userAgent,
      mapLibrary: {
        type: map.type,
        version: map.version,
      },
    };
  }

  private captureViewport(map: DetectedMap): CaptureViewport {
    const instance = map.instance;

    if (!instance) {
      // Fallback for unknown maps
      return {
        center: [0, 0],
        zoom: 0,
      };
    }

    switch (map.type) {
      case "maplibre":
      case "mapbox":
        return this.captureMapLibreViewport(instance as MapLibreMap);
      case "leaflet":
        return this.captureLeafletViewport(instance as LeafletMap);
      default:
        return { center: [0, 0], zoom: 0 };
    }
  }

  private captureMapLibreViewport(map: MapLibreMap): CaptureViewport {
    const center = map.getCenter();
    const bounds = map.getBounds();
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();

    return {
      center: [center.lng, center.lat],
      zoom: map.getZoom(),
      bounds: [
        [sw.lng, sw.lat],
        [ne.lng, ne.lat],
      ],
      bearing: map.getBearing(),
      pitch: map.getPitch(),
    };
  }

  private captureLeafletViewport(map: LeafletMap): CaptureViewport {
    const center = map.getCenter();
    const bounds = map.getBounds();
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();

    return {
      center: [center.lng, center.lat],
      zoom: map.getZoom(),
      bounds: [
        [sw.lng, sw.lat],
        [ne.lng, ne.lat],
      ],
    };
  }

  private async captureStyle(map: DetectedMap): Promise<MapLibreStyle | undefined> {
    if (map.type !== "maplibre" && map.type !== "mapbox") {
      return undefined;
    }

    const instance = map.instance as MapLibreMap | null;
    if (!instance) return undefined;

    // Wait for style to be loaded
    if (!instance.isStyleLoaded()) {
      await new Promise<void>((resolve) => {
        instance.once("style.load", resolve);
        // Timeout after 5 seconds
        setTimeout(resolve, 5000);
      });
    }

    try {
      const style = instance.getStyle();
      return style as MapLibreStyle;
    } catch (e) {
      console.error("Failed to get map style:", e);
      return undefined;
    }
  }
}

/**
 * Execute style capture via injected script.
 *
 * This is needed because content scripts run in an isolated world
 * and cannot access page JavaScript objects directly.
 */
export function captureStyleViaInjection(): Promise<MapLibreStyle | null> {
  return new Promise((resolve) => {
    // Create a script that runs in the page context
    const script = document.createElement("script");
    script.textContent = `
      (function() {
        // Helper function to capture from a map instance
        function captureFromInstance(instance) {
          if (instance && typeof instance.getStyle === 'function') {
            try {
              const style = instance.getStyle();
              const viewport = {
                center: instance.getCenter?.() || [0, 0],
                zoom: instance.getZoom?.() || 0,
                bounds: instance.getBounds?.(),
                bearing: instance.getBearing?.() || 0,
                pitch: instance.getPitch?.() || 0,
              };
              window.postMessage({
                type: 'WEBMAP_ARCHIVER_CAPTURE',
                style: style,
                viewport: viewport,
              }, '*');
              return true;
            } catch (e) {
              console.error('WebMap Archiver: Failed to capture style', e);
            }
          }
          return false;
        }

        // Strategy 1: Check common window properties
        const windowCandidates = [
          window.map,
          window.maplibreMap,
          window.mapboxMap,
        ];

        for (const candidate of windowCandidates) {
          if (captureFromInstance(candidate)) return;
        }

        // Strategy 2: Check map containers with special properties
        const containers = document.querySelectorAll('.maplibregl-map, .mapboxgl-map');
        for (const container of containers) {
          // Try MapLibre/Mapbox internal properties
          if (captureFromInstance(container.__maplibregl_map)) return;
          if (captureFromInstance(container.__mapboxgl_map)) return;

          // Try common custom property names
          for (const prop of ['_map', '__map', 'map']) {
            if (captureFromInstance(container[prop])) return;
          }
        }

        // Strategy 3: Fallback - scan all window properties
        for (const key of Object.keys(window)) {
          const obj = window[key];
          if (captureFromInstance(obj)) return;
        }

        // No map found
        window.postMessage({ type: 'WEBMAP_ARCHIVER_CAPTURE', style: null }, '*');
      })();
    `;

    // Listen for response
    const handler = (event: MessageEvent) => {
      if (event.data?.type === "WEBMAP_ARCHIVER_CAPTURE") {
        window.removeEventListener("message", handler);
        resolve(event.data.style);
      }
    };
    window.addEventListener("message", handler);

    // Inject and clean up
    document.documentElement.appendChild(script);
    script.remove();

    // Timeout
    setTimeout(() => {
      window.removeEventListener("message", handler);
      resolve(null);
    }, 5000);
  });
}
