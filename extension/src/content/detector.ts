/**
 * Detect map libraries on the current page.
 *
 * Detection strategies:
 * 1. Check for global library objects (window.maplibregl, etc.)
 * 2. Look for characteristic DOM elements (works even when libraries are bundled)
 * 3. Check for canvas elements with map-like properties
 */

import {
  DetectedMap,
  MapLibraryType,
  MapLibreMap,
  LeafletMap,
} from "../types/map-libraries";

export class MapDetector {
  private detectedMaps: DetectedMap[] = [];
  private observer: MutationObserver | null = null;

  /**
   * Start detecting maps on the page.
   * Returns initial detected maps and sets up observer for dynamic additions.
   */
  detect(): DetectedMap[] {
    this.detectedMaps = [];

    // Try each detection strategy
    // Note: detectByDOM now runs first and handles bundled libraries
    this.detectByDOM();
    this.detectMapLibreGL();
    this.detectMapboxGL();
    this.detectLeaflet();
    this.detectOpenLayers();

    return this.detectedMaps;
  }

  /**
   * Set up mutation observer to detect dynamically added maps.
   */
  observe(callback: (maps: DetectedMap[]) => void): void {
    if (this.observer) {
      this.observer.disconnect();
    }

    this.observer = new MutationObserver(() => {
      const newMaps = this.detect();
      if (newMaps.length > 0) {
        callback(newMaps);
      }
    });

    this.observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  /**
   * Stop observing for map changes.
   */
  disconnect(): void {
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
  }

  /**
   * DOM-based detection - works even when map libraries are bundled
   * and not exposed as globals. This is the primary detection method.
   */
  private detectByDOM(): void {
    // MapLibre GL containers (works even without window.maplibregl)
    const maplibreContainers = document.querySelectorAll(".maplibregl-map");
    maplibreContainers.forEach((container) => {
      if (!this.isAlreadyDetected(container as HTMLElement)) {
        const instance = this.getMapInstance(
          container as HTMLElement,
          "maplibre"
        );
        this.detectedMaps.push({
          type: "maplibre",
          version: window.maplibregl?.version, // May be undefined if bundled
          element: container as HTMLElement,
          instance,
        });
      }
    });

    // Mapbox GL containers (works even without window.mapboxgl)
    const mapboxContainers = document.querySelectorAll(".mapboxgl-map");
    mapboxContainers.forEach((container) => {
      if (!this.isAlreadyDetected(container as HTMLElement)) {
        const instance = this.getMapInstance(
          container as HTMLElement,
          "mapbox"
        );
        this.detectedMaps.push({
          type: "mapbox",
          version: window.mapboxgl?.version,
          element: container as HTMLElement,
          instance,
        });
      }
    });

    // Leaflet containers
    const leafletContainers = document.querySelectorAll(".leaflet-container");
    leafletContainers.forEach((container) => {
      if (!this.isAlreadyDetected(container as HTMLElement)) {
        const instance = this.getMapInstance(
          container as HTMLElement,
          "leaflet"
        );
        this.detectedMaps.push({
          type: "leaflet",
          version: window.L?.version,
          element: container as HTMLElement,
          instance,
        });
      }
    });

    // OpenLayers containers
    const olContainers = document.querySelectorAll(".ol-viewport");
    olContainers.forEach((container) => {
      const parent = container.parentElement;
      if (parent && !this.isAlreadyDetected(parent)) {
        this.detectedMaps.push({
          type: "openlayers",
          version: undefined,
          element: parent,
          instance: null,
        });
      }
    });

    // Fallback: canvas elements with map-like parent classes
    const canvases = document.querySelectorAll("canvas");
    canvases.forEach((canvas) => {
      const parent = canvas.parentElement;
      if (!parent) return;

      // Check if this canvas is already part of a detected map
      if (this.isAlreadyDetected(parent as HTMLElement)) return;
      if (this.isAlreadyDetected(canvas as HTMLElement)) return;

      // Check for map-like parent classes
      const classList = parent.className.toLowerCase();
      if (classList.includes("map")) {
        this.detectedMaps.push({
          type: "unknown",
          element: parent as HTMLElement,
          instance: null,
        });
      }
    });
  }

  /**
   * MapLibre detection via global object (supplements DOM detection)
   */
  private detectMapLibreGL(): void {
    if (!window.maplibregl) return;

    // Find map containers not already detected
    const containers = document.querySelectorAll(".maplibregl-map");
    containers.forEach((container) => {
      if (this.isAlreadyDetected(container as HTMLElement)) {
        // Update existing detection with version if we now have the global
        const existing = this.detectedMaps.find((m) => m.element === container);
        if (existing && !existing.version) {
          existing.version = window.maplibregl?.version;
        }
        // Try to get instance if we don't have it
        if (existing && !existing.instance) {
          existing.instance =
            this.getMapInstance(container as HTMLElement, "maplibre") ||
            this.findMapInstanceOnWindow(container as HTMLElement, "maplibre");
        }
        return;
      }

      let instance = this.getMapInstance(container as HTMLElement, "maplibre");
      if (!instance) {
        instance = this.findMapInstanceOnWindow(
          container as HTMLElement,
          "maplibre"
        );
      }

      this.detectedMaps.push({
        type: "maplibre",
        version: window.maplibregl?.version,
        element: container as HTMLElement,
        instance,
      });
    });
  }

  private detectMapboxGL(): void {
    if (!window.mapboxgl) return;

    const containers = document.querySelectorAll(".mapboxgl-map");
    containers.forEach((container) => {
      if (this.isAlreadyDetected(container as HTMLElement)) {
        const existing = this.detectedMaps.find((m) => m.element === container);
        if (existing && !existing.version) {
          existing.version = window.mapboxgl?.version;
        }
        if (existing && !existing.instance) {
          existing.instance = this.getMapInstance(
            container as HTMLElement,
            "mapbox"
          );
        }
        return;
      }

      const instance = this.getMapInstance(container as HTMLElement, "mapbox");
      this.detectedMaps.push({
        type: "mapbox",
        version: window.mapboxgl?.version,
        element: container as HTMLElement,
        instance,
      });
    });
  }

  private detectLeaflet(): void {
    if (!window.L) return;

    const containers = document.querySelectorAll(".leaflet-container");
    containers.forEach((container) => {
      if (this.isAlreadyDetected(container as HTMLElement)) {
        const existing = this.detectedMaps.find((m) => m.element === container);
        if (existing && !existing.version) {
          existing.version = window.L?.version;
        }
        if (existing && !existing.instance) {
          existing.instance = this.getMapInstance(
            container as HTMLElement,
            "leaflet"
          );
        }
        return;
      }

      const instance = this.getMapInstance(container as HTMLElement, "leaflet");
      this.detectedMaps.push({
        type: "leaflet",
        version: window.L?.version,
        element: container as HTMLElement,
        instance,
      });
    });
  }

  private detectOpenLayers(): void {
    if (!window.ol) return;

    // OpenLayers doesn't add a specific class, look for ol-viewport
    const containers = document.querySelectorAll(".ol-viewport");
    containers.forEach((container) => {
      const parent = container.parentElement;
      if (parent && !this.isAlreadyDetected(parent)) {
        this.detectedMaps.push({
          type: "openlayers",
          version: undefined,
          element: parent,
          instance: null,
        });
      }
    });
  }

  private getMapInstance(
    container: HTMLElement,
    type: MapLibraryType
  ): unknown | null {
    // Try to find the map instance attached to the element
    // Many libraries store the instance on the element

    // Check common property names
    const possibleProps = [
      "_map",
      "__map",
      "map",
      "_leaflet_map",
      "__maplibregl",
      "__mapboxgl",
    ];

    for (const prop of possibleProps) {
      const instance = (container as unknown as Record<string, unknown>)[prop];
      if (instance && typeof instance === "object") {
        // Verify it has expected methods
        if (this.isValidMapInstance(instance, type)) {
          return instance;
        }
      }
    }

    // Try React fiber
    const fiberKey = Object.keys(container).find(
      (k) =>
        k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance")
    );
    if (fiberKey) {
      // Could traverse React tree to find map prop
      // This is complex and library-specific
    }

    return null;
  }

  private isValidMapInstance(instance: unknown, type: MapLibraryType): boolean {
    if (!instance || typeof instance !== "object") return false;

    const obj = instance as Record<string, unknown>;

    switch (type) {
      case "maplibre":
      case "mapbox":
        return (
          typeof obj.getStyle === "function" &&
          typeof obj.getCenter === "function" &&
          typeof obj.getZoom === "function"
        );
      case "leaflet":
        return (
          typeof obj.getCenter === "function" &&
          typeof obj.getZoom === "function" &&
          typeof obj.getBounds === "function"
        );
      default:
        return false;
    }
  }

  private findMapInstanceOnWindow(
    container: HTMLElement,
    type: MapLibraryType
  ): unknown | null {
    // Try to find map instance on window object by checking if any property
    // is a valid map instance whose container matches
    const win = window as unknown as Record<string, unknown>;

    for (const key of Object.keys(win)) {
      try {
        const value = win[key];
        if (value && typeof value === "object") {
          const obj = value as Record<string, unknown>;

          // Check if this is a valid map instance
          if (this.isValidMapInstance(value, type)) {
            // Check if its container matches
            if (typeof obj.getContainer === "function") {
              const mapContainer = obj.getContainer();
              if (
                mapContainer === container ||
                (mapContainer as HTMLElement)?.contains?.(container)
              ) {
                return value;
              }
            }
          }
        }
      } catch {
        // Skip properties that throw on access
        continue;
      }
    }

    return null;
  }

  private isAlreadyDetected(element: HTMLElement): boolean {
    return this.detectedMaps.some((m) => m.element === element);
  }
}

/**
 * Check if current page has any maps.
 */
export function hasMap(): boolean {
  const detector = new MapDetector();
  return detector.detect().length > 0;
}

/**
 * Get all detected maps on current page.
 */
export function getDetectedMaps(): DetectedMap[] {
  const detector = new MapDetector();
  return detector.detect();
}
