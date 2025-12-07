/**
 * Capture Bundle Format v1.0
 *
 * Interchange format between browser extension and Python CLI/Modal backend.
 * See docs/capture-bundle-spec.md for full specification.
 */

export interface CaptureBundle {
  version: "1.0";
  metadata: CaptureMetadata;
  viewport: CaptureViewport;
  style?: MapLibreStyle;
  har?: HARLog;
  tiles?: CapturedTile[];
  resources?: CapturedResources;
}

export interface CaptureMetadata {
  url: string;
  title: string;
  capturedAt: string; // ISO 8601
  userAgent?: string;
  mapLibrary?: {
    type: "maplibre" | "mapbox" | "leaflet" | "openlayers" | "unknown";
    version?: string;
  };
  captureStats?: {
    totalRequests: number;
    tileCount: number;
    zoomLevels: number[];
    estimatedSize: number;
    recordingDuration: number;
  };
}

export interface CaptureViewport {
  center: [number, number]; // [lng, lat]
  zoom: number;
  bounds?: [[number, number], [number, number]]; // [[sw_lng, sw_lat], [ne_lng, ne_lat]]
  bearing?: number;
  pitch?: number;
}

export interface CapturedTile {
  z: number;
  x: number;
  y: number;
  source: string;
  data: string; // base64-encoded
  format: "pbf" | "mvt" | "png" | "jpg" | "webp";
}

export interface CapturedResources {
  sprites?: Record<string, string>; // filename -> base64 content
  glyphs?: Record<string, string>; // path -> base64 content
}

// Simplified HAR types (subset of HAR 1.2 spec)
export interface HARLog {
  log: {
    version: string;
    creator: { name: string; version: string };
    entries: HAREntry[];
  };
}

export interface HAREntry {
  startedDateTime: string;
  request: {
    method: string;
    url: string;
    headers: Array<{ name: string; value: string }>;
  };
  response: {
    status: number;
    statusText: string;
    headers: Array<{ name: string; value: string }>;
    content: {
      size: number;
      mimeType: string;
      text?: string;
      encoding?: string;
    };
  };
  timings: {
    wait: number;
    receive: number;
  };
}

// MapLibre Style Spec (simplified)
export interface MapLibreStyle {
  version: 8;
  name?: string;
  sources: Record<string, MapSource>;
  layers: MapLayer[];
  sprite?: string;
  glyphs?: string;
  center?: [number, number];
  zoom?: number;
}

export interface MapSource {
  type: "vector" | "raster" | "raster-dem" | "geojson" | "image" | "video";
  url?: string;
  tiles?: string[];
  tileSize?: number;
  attribution?: string;
  [key: string]: unknown;
}

export interface MapLayer {
  id: string;
  type: string;
  source?: string;
  "source-layer"?: string;
  paint?: Record<string, unknown>;
  layout?: Record<string, unknown>;
  filter?: unknown[];
  minzoom?: number;
  maxzoom?: number;
}
