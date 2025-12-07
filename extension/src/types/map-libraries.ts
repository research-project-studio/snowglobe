/**
 * Type definitions for detected map libraries.
 */

export type MapLibraryType = "maplibre" | "mapbox" | "leaflet" | "openlayers" | "unknown";

export interface DetectedMap {
  type: MapLibraryType;
  version?: string;
  element: HTMLElement;
  instance: unknown; // The actual map object
}

// MapLibre/Mapbox GL JS interface (subset)
export interface MapLibreMap {
  getStyle(): object;
  getCenter(): { lng: number; lat: number };
  getZoom(): number;
  getBounds(): {
    getSouthWest(): { lng: number; lat: number };
    getNorthEast(): { lng: number; lat: number };
  };
  getBearing(): number;
  getPitch(): number;
  on(event: string, callback: () => void): void;
  once(event: string, callback: () => void): void;
  isStyleLoaded(): boolean;
}

// Leaflet interface (subset)
export interface LeafletMap {
  getCenter(): { lat: number; lng: number };
  getZoom(): number;
  getBounds(): {
    getSouthWest(): { lat: number; lng: number };
    getNorthEast(): { lat: number; lng: number };
  };
  eachLayer(callback: (layer: unknown) => void): void;
}

// OpenLayers interface (subset)
export interface OpenLayersMap {
  getView(): {
    getCenter(): [number, number];
    getZoom(): number;
  };
  getLayers(): { getArray(): unknown[] };
}

// Window augmentation for map library globals
declare global {
  interface Window {
    maplibregl?: {
      Map: new (...args: unknown[]) => MapLibreMap;
      version?: string;
    };
    mapboxgl?: {
      Map: new (...args: unknown[]) => MapLibreMap;
      version?: string;
    };
    L?: {
      Map: new (...args: unknown[]) => LeafletMap;
      version?: string;
    };
    ol?: {
      Map: new (...args: unknown[]) => OpenLayersMap;
      version?: string;
    };
  }
}
