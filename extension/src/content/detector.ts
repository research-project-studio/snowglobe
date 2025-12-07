/**
 * Detect map libraries on the current page.
 *
 * Detection strategies:
 * 1. Check for global library objects (window.maplibregl, etc.)
 * 2. Look for characteristic DOM elements
 * 3. Check for canvas elements with map-like properties
 */

import { DetectedMap, MapLibraryType, MapLibreMap, LeafletMap } from "../types/map-libraries";

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
    this.detectMapLibreGL();
    this.detectMapboxGL();
    this.detectLeaflet();
    this.detectOpenLayers();
    this.detectByDOM();

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

  private detectMapLibreGL(): void {
    if (!window.maplibregl) return;

    // Find map containers
    const containers = document.querySelectorAll(".maplibregl-map");
    containers.forEach((container) => {
      const instance = this.getMapInstance(container as HTMLElement, "maplibre");
      if (instance) {
        this.detectedMaps.push({
          type: "maplibre",
          version: window.maplibregl?.version,
          element: container as HTMLElement,
          instance,
        });
      }
    });
  }

  private detectMapboxGL(): void {
    if (!window.mapboxgl) return;

    const containers = document.querySelectorAll(".mapboxgl-map");
    containers.forEach((container) => {
      const instance = this.getMapInstance(container as HTMLElement, "mapbox");
      if (instance) {
        this.detectedMaps.push({
          type: "mapbox",
          version: window.mapboxgl?.version,
          element: container as HTMLElement,
          instance,
        });
      }
    });
  }

  private detectLeaflet(): void {
    if (!window.L) return;

    const containers = document.querySelectorAll(".leaflet-container");
    containers.forEach((container) => {
      const instance = this.getMapInstance(container as HTMLElement, "leaflet");
      if (instance) {
        this.detectedMaps.push({
          type: "leaflet",
          version: window.L?.version,
          element: container as HTMLElement,
          instance,
        });
      }
    });
  }

  private detectOpenLayers(): void {
    if (!window.ol) return;

    // OpenLayers doesn't add a specific class, look for ol-viewport
    const containers = document.querySelectorAll(".ol-viewport");
    containers.forEach((container) => {
      const parent = container.parentElement;
      if (parent) {
        this.detectedMaps.push({
          type: "openlayers",
          version: undefined,
          element: parent,
          instance: null, // Harder to get OL instance
        });
      }
    });
  }

  private detectByDOM(): void {
    // Fallback: look for canvas elements that might be maps
    // This catches cases where global objects aren't exposed
    const canvases = document.querySelectorAll("canvas");
    canvases.forEach((canvas) => {
      const parent = canvas.parentElement;
      if (!parent) return;

      // Check for map-like parent classes
      const classList = parent.className.toLowerCase();
      if (
        classList.includes("map") &&
        !this.isAlreadyDetected(parent as HTMLElement)
      ) {
        this.detectedMaps.push({
          type: "unknown",
          element: parent as HTMLElement,
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
      (k) => k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance")
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
