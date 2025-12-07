# WebMap Archiver: Phase 3 Implementation Guide

## For Claude Code

This document provides complete instructions for implementing Phase 3: Browser Extension. This extension captures web maps and produces capture bundles that are processed into archives via Modal cloud backend.

**Related Documents:**
- `docs/capture-bundle-spec.md` - Capture bundle JSON specification
- `modal-deployment-guide.md` - Modal cloud backend deployment
- `architecture-overview.md` - Complete system architecture

---

## 1. Context

### Project Overview

WebMap Archiver preserves web maps as self-contained archives. The system has three components:

1. **Python CLI** (Phases 1-2, complete) - Processes captures into archives
2. **Browser Extension** (Phase 3, this phase) - Captures maps in the browser  
3. **Modal Cloud Backend** (Phase 3, this phase) - Serverless processing endpoint
4. **Integrations** (Phase 4-5, future) - Zotero, Are.na, batch automation

### What the Extension Does

1. Detects when the user is viewing a web map (MapLibre, Mapbox, Leaflet, OpenLayers)
2. Shows a browser action icon indicating a map is detected
3. **Two-step capture flow:**
   - User clicks "Start Capture" → recording begins
   - User pans/zooms map to capture desired areas and zoom levels
   - User clicks extension again → "Stop & Archive"
4. During recording, captures via `chrome.debugger` API:
   - All network requests (tiles, styles, sprites, glyphs)
   - Response bodies (actual tile data)
5. On stop, also captures:
   - Runtime map style via `map.getStyle()`
   - Final viewport state (center, zoom, bounds)
   - Page metadata
6. Bundles everything into a capture bundle
7. Sends to Modal cloud for processing → returns download URL for `.zip` archive
8. Falls back to local service (`localhost:8765`) if cloud unavailable
9. Falls back to raw bundle download if no processing available

### Repository Structure

The extension lives alongside the Python CLI in a monorepo:

```
webmap-archiver/
├── cli/                      # Python CLI (existing)
│   ├── pyproject.toml
│   └── webmap_archiver/
│       ├── __init__.py
│       ├── cli.py
│       ├── capture/
│       ├── tiles/
│       ├── viewer/
│       ├── archive/
│       └── modal_app.py      # Modal cloud deployment
├── extension/                # Browser extension (Phase 3)
│   ├── manifest.json
│   ├── package.json
│   ├── src/
│   └── README.md
├── docs/
│   ├── capture-bundle-spec.md  # Capture bundle JSON spec
│   ├── architecture.md
│   └── user-guide.md
└── README.md
```

---

## 2. Extension Architecture

### Manifest V3 Overview

Chrome requires Manifest V3 for new extensions. Key differences from V2:
- Service workers instead of background pages
- Declarative net request instead of webRequest blocking
- Promises-based APIs
- Stricter CSP

### Component Structure

```
extension/
├── manifest.json              # Extension manifest (V3)
├── package.json               # Build tooling
├── tsconfig.json              # TypeScript config
├── webpack.config.js          # Bundler config
├── src/
│   ├── config.ts              # API endpoints configuration
│   ├── background/
│   │   └── service-worker.ts  # Background service worker
│   ├── content/
│   │   ├── detector.ts        # Map detection logic
│   │   ├── capturer.ts        # Style/viewport capture
│   │   └── index.ts           # Content script entry
│   ├── popup/
│   │   ├── popup.html         # Popup UI
│   │   ├── popup.ts           # Popup logic
│   │   └── popup.css          # Popup styles
│   ├── devtools/
│   │   ├── devtools.html      # DevTools page
│   │   ├── panel.html         # DevTools panel
│   │   └── panel.ts           # HAR capture via devtools API
│   ├── types/
│   │   ├── capture-bundle.ts  # Capture bundle types
│   │   └── map-libraries.ts   # Map library type definitions
│   └── utils/
│       ├── messaging.ts       # Chrome message utilities
│       └── storage.ts         # Chrome storage utilities
├── icons/
│   ├── icon-16.png
│   ├── icon-48.png
│   └── icon-128.png
└── _locales/
    └── en/
        └── messages.json
```

---

## 3. Implementation Tasks

### Task 1: Project Setup

Create `extension/package.json`:

```json
{
  "name": "webmap-archiver-extension",
  "version": "0.1.0",
  "description": "Capture web maps for offline archiving",
  "scripts": {
    "dev": "webpack --mode development --watch",
    "build": "webpack --mode production",
    "clean": "rm -rf dist",
    "lint": "eslint src --ext .ts,.tsx",
    "typecheck": "tsc --noEmit"
  },
  "devDependencies": {
    "@types/chrome": "^0.0.260",
    "@typescript-eslint/eslint-plugin": "^6.0.0",
    "@typescript-eslint/parser": "^6.0.0",
    "copy-webpack-plugin": "^12.0.0",
    "css-loader": "^6.8.0",
    "eslint": "^8.50.0",
    "html-webpack-plugin": "^5.5.0",
    "style-loader": "^3.3.0",
    "ts-loader": "^9.5.0",
    "typescript": "^5.3.0",
    "webpack": "^5.89.0",
    "webpack-cli": "^5.1.0"
  }
}
```

Create `extension/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2020", "DOM"],
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "outDir": "./dist",
    "rootDir": "./src",
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

Create `extension/webpack.config.js`:

```javascript
const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');
const HtmlPlugin = require('html-webpack-plugin');

module.exports = {
  entry: {
    'service-worker': './src/background/service-worker.ts',
    'content-script': './src/content/index.ts',
    'popup': './src/popup/popup.ts',
    'devtools': './src/devtools/devtools.ts',
    'panel': './src/devtools/panel.ts',
  },
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: '[name].js',
    clean: true,
  },
  module: {
    rules: [
      {
        test: /\.tsx?$/,
        use: 'ts-loader',
        exclude: /node_modules/,
      },
      {
        test: /\.css$/,
        use: ['style-loader', 'css-loader'],
      },
    ],
  },
  resolve: {
    extensions: ['.tsx', '.ts', '.js'],
  },
  plugins: [
    new CopyPlugin({
      patterns: [
        { from: 'manifest.json', to: 'manifest.json' },
        { from: 'icons', to: 'icons' },
        { from: '_locales', to: '_locales' },
        { from: 'src/popup/popup.html', to: 'popup.html' },
        { from: 'src/popup/popup.css', to: 'popup.css' },
        { from: 'src/devtools/devtools.html', to: 'devtools.html' },
        { from: 'src/devtools/panel.html', to: 'panel.html' },
      ],
    }),
  ],
  optimization: {
    splitChunks: false,
  },
};
```

### Task 2: Manifest V3

Create `extension/manifest.json`:

```json
{
  "manifest_version": 3,
  "name": "WebMap Archiver",
  "version": "0.1.0",
  "description": "Capture web maps for offline archiving",
  
  "permissions": [
    "activeTab",
    "scripting",
    "storage",
    "downloads",
    "notifications"
  ],
  
  "optional_permissions": [
    "debugger"
  ],
  
  "optional_host_permissions": [
    "<all_urls>"
  ],
  
  "host_permissions": [
    "<all_urls>"
  ],
  
  "background": {
    "service_worker": "service-worker.js",
    "type": "module"
  },
  
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["content-script.js"],
      "run_at": "document_idle"
    }
  ],
  
  "action": {
    "default_popup": "popup.html",
    "default_icon": {
      "16": "icons/icon-16.png",
      "48": "icons/icon-48.png",
      "128": "icons/icon-128.png"
    },
    "default_title": "WebMap Archiver"
  },
  
  "devtools_page": "devtools.html",
  
  "icons": {
    "16": "icons/icon-16.png",
    "48": "icons/icon-48.png",
    "128": "icons/icon-128.png"
  },
  
  "web_accessible_resources": [
    {
      "resources": ["icons/*"],
      "matches": ["<all_urls>"]
    }
  ]
}
```

### Task 3: Configuration

Create `extension/src/config.ts`:

```typescript
/**
 * Extension configuration.
 * 
 * API endpoints for capture bundle processing.
 */

export const CONFIG = {
  // Modal cloud endpoint (primary)
  // Replace YOUR_USERNAME with your Modal username after deployment
  cloudEndpoint: "https://YOUR_USERNAME--webmap-archiver-process.modal.run",
  
  // Local development endpoint (modal serve)
  localDevEndpoint: "http://localhost:8000",
  
  // Local Python service (webmap-archive serve)
  localServiceEndpoint: "http://localhost:8765",
  
  // Processing timeout (5 minutes)
  processingTimeout: 300000,
  
  // Enable local fallback when cloud is unavailable
  enableLocalFallback: true,
  
  // Archive download expiry notice (hours)
  archiveExpiryHours: 24,
};

/**
 * Capture state machine.
 * Tracks recording state per tab.
 */
export type CaptureState = 
  | { status: "idle" }
  | { 
      status: "recording"; 
      startedAt: string; 
      tileCount: number; 
      totalRequests: number;
      zoomLevels: number[];
      estimatedSize: number; // bytes
    }
  | { status: "processing"; progress: number; message: string }
  | { status: "complete"; filename: string; size: number }
  | { status: "error"; message: string };

/**
 * Captured network request during recording.
 */
export interface CapturedRequest {
  url: string;
  method: string;
  status: number;
  mimeType: string;
  responseSize: number;
  responseBody?: string; // base64 encoded
  timestamp: number;
  // Tile-specific fields (if detected as tile request)
  isTile: boolean;
  tileCoords?: { z: number; x: number; y: number };
  tileSource?: string;
}

/**
 * Get ordered list of processing endpoints to try.
 * Cloud first, then local service, then local dev.
 */
export function getProcessingEndpoints(): string[] {
  const endpoints = [CONFIG.cloudEndpoint];
  
  if (CONFIG.enableLocalFallback) {
    endpoints.push(CONFIG.localServiceEndpoint);
    endpoints.push(CONFIG.localDevEndpoint);
  }
  
  return endpoints;
}
```

**Important:** After deploying the Modal backend, update `cloudEndpoint` with your actual Modal username.

### Task 4: Type Definitions

Create `extension/src/types/capture-bundle.ts`:

> **Note:** These types must match the specification in `docs/capture-bundle-spec.md`.

```typescript
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
  sprites?: Record<string, string>;  // filename -> base64 content
  glyphs?: Record<string, string>;   // path -> base64 content
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
```

Create `extension/src/types/map-libraries.ts`:

```typescript
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
```

### Task 5: Map Detection (Content Script)

Create `extension/src/content/detector.ts`:

```typescript
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
          version: window.ol?.version,
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
      const instance = (container as Record<string, unknown>)[prop];
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
```

### Task 6: Map Capture (Content Script)

Create `extension/src/content/capturer.ts`:

```typescript
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
        // Find map instance
        const containers = document.querySelectorAll('.maplibregl-map, .mapboxgl-map');
        for (const container of containers) {
          // Try common property names for map instance
          for (const prop of ['_map', '__map', 'map']) {
            const instance = container[prop];
            if (instance && typeof instance.getStyle === 'function') {
              try {
                const style = instance.getStyle();
                const viewport = {
                  center: instance.getCenter(),
                  zoom: instance.getZoom(),
                  bounds: instance.getBounds(),
                  bearing: instance.getBearing?.() || 0,
                  pitch: instance.getPitch?.() || 0,
                };
                window.postMessage({
                  type: 'WEBMAP_ARCHIVER_CAPTURE',
                  style: style,
                  viewport: viewport,
                }, '*');
                return;
              } catch (e) {
                console.error('WebMap Archiver: Failed to capture style', e);
              }
            }
          }
        }
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
```

### Task 7: Content Script Entry Point

Create `extension/src/content/index.ts`:

```typescript
/**
 * Content script entry point.
 * 
 * Runs on every page to detect maps and handle capture requests.
 */

import { MapDetector, getDetectedMaps } from "./detector";
import { captureStyleViaInjection } from "./capturer";
import { DetectedMap } from "../types/map-libraries";

let detectedMaps: DetectedMap[] = [];
let detector: MapDetector | null = null;

/**
 * Initialize detection on page load.
 */
function init(): void {
  detector = new MapDetector();
  
  // Initial detection
  detectedMaps = detector.detect();
  notifyBackground();

  // Watch for dynamically added maps
  detector.observe((maps) => {
    detectedMaps = maps;
    notifyBackground();
  });

  // Re-detect after a delay (for maps that take time to initialize)
  setTimeout(() => {
    detectedMaps = detector!.detect();
    notifyBackground();
  }, 2000);
}

/**
 * Notify background script of detected maps.
 */
function notifyBackground(): void {
  chrome.runtime.sendMessage({
    type: "MAPS_DETECTED",
    count: detectedMaps.length,
    maps: detectedMaps.map((m) => ({
      type: m.type,
      version: m.version,
    })),
  });
}

/**
 * Handle messages from popup/background.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case "GET_MAPS":
      sendResponse({
        count: detectedMaps.length,
        maps: detectedMaps.map((m) => ({
          type: m.type,
          version: m.version,
        })),
      });
      break;

    case "CAPTURE_STYLE":
      // Use injection to capture from page context
      captureStyleViaInjection().then((result) => {
        sendResponse(result);
      });
      return true; // Keep channel open for async response

    case "GET_PAGE_INFO":
      sendResponse({
        url: window.location.href,
        title: document.title,
      });
      break;

    default:
      break;
  }
});

// Initialize when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
```

### Task 8: Background Service Worker (Two-Step Capture)

Create `extension/src/background/service-worker.ts`:

```typescript
/**
 * Background service worker.
 * 
 * Handles:
 * - Badge updates based on map detection
 * - Two-step capture flow: Start Recording → Stop & Archive
 * - Network capture via chrome.debugger API
 * - Processing via Modal cloud (primary) or local service (fallback)
 * - File downloads
 */

import { CaptureBundle, HARLog } from "../types/capture-bundle";
import { CONFIG, getProcessingEndpoints, CaptureState, CapturedRequest } from "../config";

// Track detected maps per tab
const tabMapState = new Map<number, { count: number; types: string[] }>();

// Track capture state per tab
const tabCaptureState = new Map<number, CaptureState>();

// Track captured requests during recording
const tabCapturedRequests = new Map<number, CapturedRequest[]>();

// Track pending response bodies (debugger returns body separately)
const pendingBodies = new Map<string, { tabId: number; requestId: string }>();

/**
 * Handle messages from content scripts and popup.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id || message.tabId;

  switch (message.type) {
    case "MAPS_DETECTED":
      if (tabId) {
        handleMapsDetected(tabId, message.count, message.maps);
      }
      break;

    case "GET_TAB_STATE":
      if (message.tabId) {
        const mapState = tabMapState.get(message.tabId);
        const captureState = tabCaptureState.get(message.tabId) || { status: "idle" };
        sendResponse({ 
          maps: mapState || { count: 0, types: [] },
          capture: captureState,
        });
      }
      break;

    case "START_CAPTURE":
      if (message.tabId) {
        startCapture(message.tabId).then(sendResponse);
      }
      return true; // Async response

    case "STOP_CAPTURE":
      if (message.tabId) {
        stopCapture(message.tabId).then(sendResponse);
      }
      return true; // Async response

    case "CANCEL_CAPTURE":
      if (message.tabId) {
        cancelCapture(message.tabId);
        sendResponse({ success: true });
      }
      break;

    case "PROCESS_BUNDLE":
      processCapture(message.bundle).then(sendResponse);
      return true;

    case "DOWNLOAD_BUNDLE":
      downloadBundle(message.bundle, message.filename);
      break;

    default:
      break;
  }
});

/**
 * Update badge when maps are detected.
 */
function handleMapsDetected(
  tabId: number,
  count: number,
  maps: Array<{ type: string; version?: string }>
): void {
  tabMapState.set(tabId, {
    count,
    types: maps.map((m) => m.type),
  });

  // Only update badge if not recording
  const captureState = tabCaptureState.get(tabId);
  if (!captureState || captureState.status !== "recording") {
    updateBadgeForMapDetection(tabId, count);
  }
}

function updateBadgeForMapDetection(tabId: number, count: number): void {
  if (count > 0) {
    chrome.action.setBadgeText({ text: count.toString(), tabId });
    chrome.action.setBadgeBackgroundColor({ color: "#4CAF50", tabId });
    chrome.action.setTitle({ title: `WebMap Archiver (${count} map${count > 1 ? "s" : ""} detected)`, tabId });
  } else {
    chrome.action.setBadgeText({ text: "", tabId });
    chrome.action.setTitle({ title: "WebMap Archiver", tabId });
  }
}

// ============================================================================
// TWO-STEP CAPTURE FLOW
// ============================================================================

/**
 * Start recording network traffic for a tab.
 */
async function startCapture(tabId: number): Promise<{ success: boolean; error?: string }> {
  try {
    // Check if debugger permission is granted
    const hasPermission = await chrome.permissions.contains({
      permissions: ["debugger"],
    });

    if (!hasPermission) {
      // Request permission
      const granted = await chrome.permissions.request({
        permissions: ["debugger"],
      });
      if (!granted) {
        return { success: false, error: "Debugger permission required for tile capture" };
      }
    }

    // Initialize capture state
    tabCaptureState.set(tabId, {
      status: "recording",
      startedAt: new Date().toISOString(),
      tileCount: 0,
      totalRequests: 0,
      zoomLevels: [],
      estimatedSize: 0,
    });
    tabCapturedRequests.set(tabId, []);

    // Attach debugger to tab
    await chrome.debugger.attach({ tabId }, "1.3");
    
    // Enable network capture
    await chrome.debugger.sendCommand({ tabId }, "Network.enable", {
      maxResourceBufferSize: 100 * 1024 * 1024, // 100MB buffer
      maxTotalBufferSize: 200 * 1024 * 1024,    // 200MB total
    });

    // Update badge to show recording
    chrome.action.setBadgeText({ text: "REC", tabId });
    chrome.action.setBadgeBackgroundColor({ color: "#f44336", tabId }); // Red

    console.log(`[WebMap Archiver] Started recording for tab ${tabId}`);
    return { success: true };

  } catch (e) {
    console.error("[WebMap Archiver] Failed to start capture:", e);
    tabCaptureState.set(tabId, { status: "error", message: String(e) });
    return { success: false, error: String(e) };
  }
}

/**
 * Stop recording and build capture bundle.
 */
async function stopCapture(tabId: number): Promise<{ success: boolean; bundle?: CaptureBundle; error?: string }> {
  const state = tabCaptureState.get(tabId);
  if (!state || state.status !== "recording") {
    return { success: false, error: "Not recording" };
  }

  try {
    // Detach debugger
    await chrome.debugger.detach({ tabId });

    // Update state to processing
    tabCaptureState.set(tabId, { status: "processing", progress: 10, message: "Building capture bundle..." });
    updateBadgeForProcessing(tabId, 10);

    // Get captured requests
    const requests = tabCapturedRequests.get(tabId) || [];
    
    // Get style and viewport from content script
    tabCaptureState.set(tabId, { status: "processing", progress: 30, message: "Capturing map style..." });
    updateBadgeForProcessing(tabId, 30);
    
    const styleResult = await chrome.tabs.sendMessage(tabId, { type: "CAPTURE_STYLE" });
    const pageInfo = await chrome.tabs.sendMessage(tabId, { type: "GET_PAGE_INFO" });

    // Build capture bundle
    tabCaptureState.set(tabId, { status: "processing", progress: 50, message: "Processing tiles..." });
    updateBadgeForProcessing(tabId, 50);

    const bundle = buildCaptureBundle(requests, styleResult, pageInfo, state);

    // Clean up
    tabCapturedRequests.delete(tabId);

    console.log(`[WebMap Archiver] Capture complete: ${bundle.tiles?.length || 0} tiles`);
    return { success: true, bundle };

  } catch (e) {
    console.error("[WebMap Archiver] Failed to stop capture:", e);
    tabCaptureState.set(tabId, { status: "error", message: String(e) });
    return { success: false, error: String(e) };
  }
}

/**
 * Cancel recording without processing.
 */
async function cancelCapture(tabId: number): Promise<void> {
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    // May already be detached
  }
  
  tabCaptureState.set(tabId, { status: "idle" });
  tabCapturedRequests.delete(tabId);
  
  // Restore map detection badge
  const mapState = tabMapState.get(tabId);
  updateBadgeForMapDetection(tabId, mapState?.count || 0);
}

function updateBadgeForProcessing(tabId: number, progress: number): void {
  const text = progress < 100 ? `${progress}%` : "✓";
  chrome.action.setBadgeText({ text, tabId });
  chrome.action.setBadgeBackgroundColor({ color: "#2196F3", tabId }); // Blue
}

// ============================================================================
// DEBUGGER EVENT HANDLERS
// ============================================================================

/**
 * Handle debugger events for network capture.
 */
chrome.debugger.onEvent.addListener((source, method, params) => {
  const tabId = source.tabId;
  if (!tabId) return;

  const state = tabCaptureState.get(tabId);
  if (!state || state.status !== "recording") return;

  switch (method) {
    case "Network.responseReceived":
      handleResponseReceived(tabId, params as NetworkResponseParams);
      break;
    case "Network.loadingFinished":
      handleLoadingFinished(tabId, params as NetworkLoadingFinishedParams);
      break;
  }
});

interface NetworkResponseParams {
  requestId: string;
  response: {
    url: string;
    status: number;
    mimeType: string;
    headers: Record<string, string>;
  };
  type: string;
}

interface NetworkLoadingFinishedParams {
  requestId: string;
  encodedDataLength: number;
}

/**
 * Handle network response metadata.
 */
function handleResponseReceived(tabId: number, params: NetworkResponseParams): void {
  const { requestId, response } = params;
  const { url, status, mimeType } = response;

  // Check if this looks like a tile request
  const tileInfo = parseTileUrl(url);
  
  const request: CapturedRequest = {
    url,
    method: "GET",
    status,
    mimeType,
    responseSize: 0,
    timestamp: Date.now(),
    isTile: tileInfo !== null,
    tileCoords: tileInfo?.coords,
    tileSource: tileInfo?.source,
  };

  // Store request (body will be added in loadingFinished)
  const requests = tabCapturedRequests.get(tabId) || [];
  requests.push(request);
  tabCapturedRequests.set(tabId, requests);

  // Track for body retrieval
  pendingBodies.set(requestId, { tabId, requestId });

  // Update capture state
  const state = tabCaptureState.get(tabId);
  if (state?.status === "recording") {
    state.totalRequests++;
    if (tileInfo) {
      state.tileCount++;
      if (!state.zoomLevels.includes(tileInfo.coords.z)) {
        state.zoomLevels.push(tileInfo.coords.z);
        state.zoomLevels.sort((a, b) => a - b);
      }
    }
  }
}

/**
 * Handle loading finished - retrieve response body.
 */
async function handleLoadingFinished(tabId: number, params: NetworkLoadingFinishedParams): Promise<void> {
  const { requestId, encodedDataLength } = params;
  
  const pending = pendingBodies.get(requestId);
  if (!pending) return;
  pendingBodies.delete(requestId);

  const requests = tabCapturedRequests.get(tabId);
  if (!requests) return;

  // Find the request
  const request = requests.find(r => r.url && pendingBodies.has(requestId) === false);
  if (!request) return;

  request.responseSize = encodedDataLength;

  // Only fetch body for tiles and important resources
  const shouldFetchBody = request.isTile || 
    request.mimeType.includes("json") ||
    request.url.includes("sprite") ||
    request.url.includes("glyphs");

  if (shouldFetchBody && encodedDataLength < 10 * 1024 * 1024) { // < 10MB
    try {
      const result = await chrome.debugger.sendCommand(
        { tabId },
        "Network.getResponseBody",
        { requestId }
      ) as { body: string; base64Encoded: boolean };

      request.responseBody = result.base64Encoded 
        ? result.body 
        : btoa(result.body);

      // Update estimated size
      const state = tabCaptureState.get(tabId);
      if (state?.status === "recording") {
        state.estimatedSize += encodedDataLength;
      }
    } catch (e) {
      // Body may not be available (e.g., cached)
      console.debug(`[WebMap Archiver] Could not get body for ${request.url}`);
    }
  }
}

/**
 * Parse tile coordinates from URL.
 */
function parseTileUrl(url: string): { coords: { z: number; x: number; y: number }; source: string } | null {
  // Common tile URL patterns:
  // /{z}/{x}/{y}.pbf
  // /{z}/{x}/{y}.png
  // /tiles/{z}/{x}/{y}
  // ?x={x}&y={y}&z={z}
  
  const patterns = [
    /\/(\d+)\/(\d+)\/(\d+)\.(pbf|mvt|png|jpg|jpeg|webp|avif)/,
    /\/tiles\/(\d+)\/(\d+)\/(\d+)/,
    /[?&]z=(\d+)&x=(\d+)&y=(\d+)/,
  ];

  for (const pattern of patterns) {
    const match = url.match(pattern);
    if (match) {
      const [, z, x, y] = match;
      
      // Extract source name from URL
      const urlObj = new URL(url);
      const source = urlObj.hostname.split(".")[0] || "tiles";
      
      return {
        coords: { z: parseInt(z), x: parseInt(x), y: parseInt(y) },
        source,
      };
    }
  }

  return null;
}

// ============================================================================
// BUNDLE BUILDING
// ============================================================================

/**
 * Build capture bundle from recorded data.
 */
function buildCaptureBundle(
  requests: CapturedRequest[],
  styleResult: any,
  pageInfo: { url: string; title: string },
  recordingState: CaptureState & { status: "recording" }
): CaptureBundle {
  // Extract tiles
  const tiles = requests
    .filter(r => r.isTile && r.responseBody)
    .map(r => ({
      z: r.tileCoords!.z,
      x: r.tileCoords!.x,
      y: r.tileCoords!.y,
      source: r.tileSource!,
      data: r.responseBody!,
      format: r.mimeType.includes("png") ? "png" as const : "pbf" as const,
    }));

  // Build HAR from all requests
  const har: HARLog = {
    log: {
      version: "1.2",
      creator: { name: "WebMap Archiver", version: "0.1.0" },
      entries: requests.map(r => ({
        request: { method: r.method, url: r.url },
        response: {
          status: r.status,
          content: {
            size: r.responseSize,
            mimeType: r.mimeType,
            text: r.responseBody,
            encoding: r.responseBody ? "base64" : undefined,
          },
        },
        startedDateTime: new Date(r.timestamp).toISOString(),
      })),
    },
  };

  return {
    version: "1.0",
    metadata: {
      url: pageInfo.url,
      title: pageInfo.title,
      capturedAt: new Date().toISOString(),
      userAgent: navigator.userAgent,
      mapLibrary: styleResult?.mapLibrary,
      captureStats: {
        totalRequests: requests.length,
        tileCount: tiles.length,
        zoomLevels: recordingState.zoomLevels,
        estimatedSize: recordingState.estimatedSize,
        recordingDuration: Date.now() - new Date(recordingState.startedAt).getTime(),
      },
    },
    viewport: styleResult?.viewport ? {
      center: [styleResult.viewport.center.lng, styleResult.viewport.center.lat],
      zoom: styleResult.viewport.zoom,
      bounds: styleResult.viewport.bounds ? [
        [styleResult.viewport.bounds._sw.lng, styleResult.viewport.bounds._sw.lat],
        [styleResult.viewport.bounds._ne.lng, styleResult.viewport.bounds._ne.lat],
      ] : undefined,
      bearing: styleResult.viewport.bearing || 0,
      pitch: styleResult.viewport.pitch || 0,
    } : {
      center: [0, 0],
      zoom: 10,
    },
    style: styleResult?.style,
    har,
    tiles,
  };
}

/**
 * Update badge when maps are detected.
 */
function handleMapsDetected(
  tabId: number,
  count: number,
  maps: Array<{ type: string; version?: string }>
): void {
  tabMapState.set(tabId, {
    count,
    types: maps.map((m) => m.type),
  });

  // Update badge
  if (count > 0) {
    chrome.action.setBadgeText({ text: count.toString(), tabId });
    chrome.action.setBadgeBackgroundColor({ color: "#4CAF50", tabId });
    chrome.action.setTitle({ title: `WebMap Archiver (${count} map${count > 1 ? "s" : ""} detected)`, tabId });
  } else {
    chrome.action.setBadgeText({ text: "", tabId });
    chrome.action.setTitle({ title: "WebMap Archiver", tabId });
  }
}

/**
 * Handle completed capture - process via cloud or local service.
 */
async function handleCaptureComplete(bundle: CaptureBundle): Promise<void> {
  const result = await processCapture(bundle);

  if (result.success && result.downloadUrl) {
    // Download the processed archive from cloud/service
    chrome.downloads.download({
      url: result.downloadUrl,
      filename: result.filename || "webmap-archive.zip",
      saveAs: true,
    });
  } else if (result.fallbackToDownload) {
    // All processing endpoints failed - download raw bundle
    const filename = generateFilename(bundle);
    downloadBundle(bundle, filename);
    
    // Notify user about fallback
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon-48.png",
      title: "WebMap Archiver",
      message: "Cloud processing unavailable. Downloaded capture bundle for manual processing with CLI.",
    });
  } else {
    // Show error notification
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon-48.png",
      title: "WebMap Archiver - Error",
      message: result.error || "Failed to process capture",
    });
  }
}

/**
 * Start capturing HAR entries for a tab.
 */
function startHarCapture(tabId: number): void {
  tabHarCapture.set(tabId, {
    log: {
      version: "1.2",
      creator: { name: "WebMap Archiver", version: "0.1.0" },
      entries: [],
    },
  });
}

/**
 * Add HAR entry from devtools panel.
 */
function handleHarEntry(tabId: number, entry: unknown): void {
  const har = tabHarCapture.get(tabId);
  if (har) {
    har.log.entries.push(entry as never);
  }
}

/**
 * Stop HAR capture and return collected entries.
 */
function stopHarCapture(tabId: number): HARLog | null {
  const har = tabHarCapture.get(tabId);
  tabHarCapture.delete(tabId);
  return har || null;
}

/**
 * Process capture via cloud or local service.
 * Tries endpoints in order: Modal cloud → local service → local dev.
 */
interface ProcessResult {
  success: boolean;
  downloadUrl?: string;
  filename?: string;
  error?: string;
  fallbackToDownload?: boolean;
}

async function processCapture(bundle: CaptureBundle): Promise<ProcessResult> {
  const endpoints = getProcessingEndpoints();

  for (const endpoint of endpoints) {
    try {
      console.log(`[WebMap Archiver] Trying endpoint: ${endpoint}`);
      
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bundle),
        signal: AbortSignal.timeout(CONFIG.processingTimeout),
      });

      if (!response.ok) {
        console.warn(`[WebMap Archiver] ${endpoint} returned ${response.status}`);
        continue;
      }

      const result = await response.json();

      if (result.success) {
        console.log(`[WebMap Archiver] Processing successful via ${endpoint}`);
        return {
          success: true,
          downloadUrl: result.downloadUrl,
          filename: result.filename,
        };
      } else {
        console.warn(`[WebMap Archiver] ${endpoint} processing failed:`, result.error);
        continue;
      }
    } catch (e) {
      console.warn(`[WebMap Archiver] ${endpoint} request failed:`, e);
      continue;
    }
  }

  // All endpoints failed - fall back to raw bundle download
  console.log("[WebMap Archiver] All processing endpoints failed, falling back to bundle download");
  return {
    success: false,
    fallbackToDownload: true,
    error: "Processing services unavailable",
  };
}

/**
 * Check if a specific endpoint is available.
 */
async function checkEndpoint(endpoint: string): Promise<boolean> {
  try {
    const healthUrl = endpoint.replace(/\/process$/, "/health");
    const response = await fetch(healthUrl, {
      method: "GET",
      signal: AbortSignal.timeout(2000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Download capture bundle as JSON file.
 */
function downloadBundle(bundle: CaptureBundle, filename: string): void {
  const json = JSON.stringify(bundle, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);

  chrome.downloads.download({
    url,
    filename,
    saveAs: true,
  });

  // Clean up blob URL after download starts
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/**
 * Generate filename from bundle metadata.
 */
function generateFilename(bundle: CaptureBundle): string {
  const url = new URL(bundle.metadata.url);
  const host = url.hostname.replace(/\./g, "-");
  const date = bundle.metadata.capturedAt.split("T")[0];
  return `${host}-${date}.webmap-capture.json`;
}

// Clean up state when tab is closed
chrome.tabs.onRemoved.addListener((tabId) => {
  tabMapState.delete(tabId);
  tabHarCapture.delete(tabId);
});
```

### Task 9: Popup UI

Create `extension/src/popup/popup.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="popup.css">
</head>
<body>
  <div class="container">
    <header>
      <img src="icons/icon-48.png" alt="WebMap Archiver" class="logo">
      <h1>WebMap Archiver</h1>
    </header>

    <!-- State: No map detected -->
    <div id="no-map" class="state">
      <p class="icon">🗺️</p>
      <p>No web map detected on this page.</p>
      <p class="hint">Navigate to a page with a MapLibre, Mapbox, or Leaflet map.</p>
    </div>

    <!-- State: Map found, ready to capture -->
    <div id="map-found" class="state hidden">
      <p class="icon">✓</p>
      <p id="map-info">Map detected!</p>
      
      <div class="actions">
        <button id="start-capture-btn" class="primary">
          🔴 Start Capture
        </button>
      </div>
      
      <p class="hint">Click to begin recording. Pan and zoom to capture the areas you need.</p>
    </div>

    <!-- State: Recording in progress -->
    <div id="recording" class="state hidden">
      <p class="icon recording-icon">🔴</p>
      <p class="recording-label">Recording...</p>
      
      <div class="stats">
        <div class="stat">
          <span class="stat-value" id="tile-count">0</span>
          <span class="stat-label">tiles</span>
        </div>
        <div class="stat">
          <span class="stat-value" id="zoom-levels">-</span>
          <span class="stat-label">zoom levels</span>
        </div>
        <div class="stat">
          <span class="stat-value" id="data-size">0 KB</span>
          <span class="stat-label">data</span>
        </div>
      </div>
      
      <p class="hint">Pan and zoom the map to capture areas you need.</p>
      
      <div class="actions">
        <button id="stop-capture-btn" class="primary">
          ⏹ Stop & Archive
        </button>
        <button id="cancel-capture-btn" class="secondary">
          ✖ Cancel
        </button>
      </div>
    </div>

    <!-- State: Processing -->
    <div id="processing" class="state hidden">
      <p class="icon">⏳</p>
      <p id="processing-message">Processing...</p>
      
      <div class="progress-bar">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
      <p class="progress-text" id="progress-text">Uploading to cloud...</p>
    </div>

    <!-- State: Complete -->
    <div id="complete" class="state hidden">
      <p class="icon">✅</p>
      <p>Archive complete!</p>
      <p class="filename" id="filename"></p>
      <p class="stats-summary" id="stats-summary"></p>
      
      <div class="actions">
        <button id="new-capture-btn" class="secondary">
          📸 New Capture
        </button>
      </div>
    </div>

    <!-- State: Error -->
    <div id="error" class="state hidden">
      <p class="icon">❌</p>
      <p id="error-message">An error occurred.</p>
      <button id="retry-btn" class="secondary">Try Again</button>
    </div>

    <footer>
      <a href="#" id="settings-link">Settings</a>
      <span class="separator">•</span>
      <a href="https://github.com/username/webmap-archiver" target="_blank">Help</a>
    </footer>
  </div>

  <script src="popup.js"></script>
</body>
</html>
```

Create `extension/src/popup/popup.css`:

```css
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  color: #333;
  background: #fff;
  min-width: 300px;
}

.container {
  padding: 16px;
}

header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e0e0e0;
}

.logo {
  width: 32px;
  height: 32px;
}

h1 {
  font-size: 16px;
  font-weight: 600;
}

.state {
  text-align: center;
  padding: 20px 0;
}

.state.hidden {
  display: none;
}

.state .icon {
  font-size: 48px;
  margin-bottom: 12px;
}

.state p {
  margin-bottom: 8px;
}

.hint {
  color: #666;
  font-size: 12px;
}

.actions {
  margin-top: 16px;
}

button {
  display: inline-block;
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s, transform 0.1s;
}

button:hover {
  transform: translateY(-1px);
}

button:active {
  transform: translateY(0);
}

button.primary {
  background: #4CAF50;
  color: white;
}

button.primary:hover {
  background: #43A047;
}

button.primary:disabled {
  background: #ccc;
  cursor: not-allowed;
  transform: none;
}

.progress-bar {
  height: 8px;
  background: #e0e0e0;
  border-radius: 4px;
  overflow: hidden;
  margin: 16px 0 8px;
}

.progress-fill {
  height: 100%;
  background: #4CAF50;
  width: 0%;
  transition: width 0.3s ease;
}

#progress-text {
  font-size: 12px;
  color: #666;
}

.filename {
  font-family: monospace;
  font-size: 12px;
  color: #666;
  word-break: break-all;
}

footer {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #e0e0e0;
  text-align: center;
  font-size: 12px;
}

footer a {
  color: #1976D2;
  text-decoration: none;
}

footer a:hover {
  text-decoration: underline;
}

.separator {
  margin: 0 8px;
  color: #ccc;
}

/* Recording state styles */
.recording-icon {
  animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.recording-label {
  font-weight: 600;
  color: #f44336;
}

.stats {
  display: flex;
  justify-content: space-around;
  margin: 16px 0;
  padding: 12px;
  background: #f5f5f5;
  border-radius: 8px;
}

.stat {
  text-align: center;
}

.stat-value {
  display: block;
  font-size: 20px;
  font-weight: 600;
  color: #333;
}

.stat-label {
  font-size: 11px;
  color: #666;
  text-transform: uppercase;
}

button.secondary {
  background: #f5f5f5;
  color: #333;
  border: 1px solid #ddd;
  margin-left: 8px;
}

button.secondary:hover {
  background: #e0e0e0;
}

.actions {
  display: flex;
  justify-content: center;
  gap: 8px;
  margin-top: 16px;
}

.stats-summary {
  font-size: 12px;
  color: #666;
  margin-top: 8px;
}
```

Create `extension/src/popup/popup.ts`:

```typescript
/**
 * Popup UI logic for two-step capture flow.
 * 
 * States:
 * - no-map: No map detected on page
 * - map-found: Map detected, ready to start capture
 * - recording: Actively recording network traffic
 * - processing: Building archive
 * - complete: Archive ready
 * - error: Something went wrong
 */

import { CaptureState } from "../config";

// UI Elements - States
const noMapState = document.getElementById("no-map")!;
const mapFoundState = document.getElementById("map-found")!;
const recordingState = document.getElementById("recording")!;
const processingState = document.getElementById("processing")!;
const completeState = document.getElementById("complete")!;
const errorState = document.getElementById("error")!;

// UI Elements - Map Found
const mapInfo = document.getElementById("map-info")!;
const startCaptureBtn = document.getElementById("start-capture-btn")!;

// UI Elements - Recording
const tileCount = document.getElementById("tile-count")!;
const zoomLevels = document.getElementById("zoom-levels")!;
const dataSize = document.getElementById("data-size")!;
const stopCaptureBtn = document.getElementById("stop-capture-btn")!;
const cancelCaptureBtn = document.getElementById("cancel-capture-btn")!;

// UI Elements - Processing
const processingMessage = document.getElementById("processing-message")!;
const progressFill = document.getElementById("progress-fill")!;
const progressText = document.getElementById("progress-text")!;

// UI Elements - Complete
const filenameEl = document.getElementById("filename")!;
const statsSummary = document.getElementById("stats-summary")!;
const newCaptureBtn = document.getElementById("new-capture-btn")!;

// UI Elements - Error
const errorMessage = document.getElementById("error-message")!;
const retryBtn = document.getElementById("retry-btn")!;

// Current tab ID
let currentTabId: number | null = null;

// Polling interval for recording stats
let statsInterval: ReturnType<typeof setInterval> | null = null;

/**
 * Initialize popup.
 */
async function init(): Promise<void> {
  // Get current tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    showError("Cannot access current tab");
    return;
  }
  currentTabId = tab.id;

  // Get current state from background
  const state = await chrome.runtime.sendMessage({ 
    type: "GET_TAB_STATE", 
    tabId: tab.id 
  });

  // Route to appropriate UI state
  if (state.capture?.status === "recording") {
    showRecording(state.capture);
    startStatsPolling();
  } else if (state.capture?.status === "processing") {
    showProcessing(state.capture.progress, state.capture.message);
  } else if (state.maps?.count > 0) {
    showMapFound(state.maps);
  } else {
    showNoMap();
  }

  // Set up event handlers
  setupEventHandlers();
}

function setupEventHandlers(): void {
  startCaptureBtn.addEventListener("click", handleStartCapture);
  stopCaptureBtn.addEventListener("click", handleStopCapture);
  cancelCaptureBtn.addEventListener("click", handleCancelCapture);
  newCaptureBtn.addEventListener("click", handleNewCapture);
  retryBtn.addEventListener("click", init);
}

// ============================================================================
// STATE DISPLAY FUNCTIONS
// ============================================================================

function hideAllStates(): void {
  noMapState.classList.add("hidden");
  mapFoundState.classList.add("hidden");
  recordingState.classList.add("hidden");
  processingState.classList.add("hidden");
  completeState.classList.add("hidden");
  errorState.classList.add("hidden");
}

function showNoMap(): void {
  hideAllStates();
  noMapState.classList.remove("hidden");
}

function showMapFound(info: { count: number; types: string[] }): void {
  hideAllStates();
  mapFoundState.classList.remove("hidden");
  
  const mapTypes = info.types.join(", ");
  mapInfo.textContent = `${info.count} map${info.count > 1 ? "s" : ""} detected (${mapTypes})`;
}

function showRecording(state: CaptureState & { status: "recording" }): void {
  hideAllStates();
  recordingState.classList.remove("hidden");
  
  updateRecordingStats(state);
}

function updateRecordingStats(state: CaptureState & { status: "recording" }): void {
  tileCount.textContent = state.tileCount.toString();
  zoomLevels.textContent = state.zoomLevels.length > 0 
    ? `${Math.min(...state.zoomLevels)}-${Math.max(...state.zoomLevels)}`
    : "-";
  dataSize.textContent = formatBytes(state.estimatedSize);
}

function showProcessing(progress: number, message: string): void {
  hideAllStates();
  processingState.classList.remove("hidden");
  
  progressFill.style.width = `${progress}%`;
  progressText.textContent = message;
}

function showComplete(filename: string, stats?: { tiles: number; size: number }): void {
  hideAllStates();
  completeState.classList.remove("hidden");
  
  filenameEl.textContent = filename;
  if (stats) {
    statsSummary.textContent = `${stats.tiles} tiles • ${formatBytes(stats.size)}`;
  }
}

function showError(message: string): void {
  hideAllStates();
  errorState.classList.remove("hidden");
  errorMessage.textContent = message;
  stopStatsPolling();
}

// ============================================================================
// EVENT HANDLERS
// ============================================================================

async function handleStartCapture(): Promise<void> {
  if (!currentTabId) return;
  
  startCaptureBtn.setAttribute("disabled", "true");
  
  const result = await chrome.runtime.sendMessage({
    type: "START_CAPTURE",
    tabId: currentTabId,
  });

  if (result.success) {
    showRecording({
      status: "recording",
      startedAt: new Date().toISOString(),
      tileCount: 0,
      totalRequests: 0,
      zoomLevels: [],
      estimatedSize: 0,
    });
    startStatsPolling();
  } else {
    showError(result.error || "Failed to start capture");
  }
  
  startCaptureBtn.removeAttribute("disabled");
}

async function handleStopCapture(): Promise<void> {
  if (!currentTabId) return;
  
  stopCaptureBtn.setAttribute("disabled", "true");
  stopStatsPolling();
  
  showProcessing(10, "Stopping capture...");

  // Stop capture and get bundle
  const stopResult = await chrome.runtime.sendMessage({
    type: "STOP_CAPTURE",
    tabId: currentTabId,
  });

  if (!stopResult.success) {
    showError(stopResult.error || "Failed to stop capture");
    return;
  }

  // Process the bundle
  showProcessing(40, "Uploading to cloud...");

  const processResult = await chrome.runtime.sendMessage({
    type: "PROCESS_BUNDLE",
    bundle: stopResult.bundle,
  });

  if (processResult.success) {
    showProcessing(80, "Downloading archive...");
    
    // Trigger download
    chrome.downloads.download({
      url: processResult.downloadUrl,
      filename: processResult.filename,
      saveAs: true,
    });
    
    await new Promise(resolve => setTimeout(resolve, 500));
    
    showComplete(processResult.filename, {
      tiles: stopResult.bundle.tiles?.length || 0,
      size: processResult.size || 0,
    });
  } else if (processResult.fallbackToDownload) {
    // Download raw bundle
    showProcessing(80, "Downloading capture bundle...");
    
    const filename = generateFilename(stopResult.bundle);
    await chrome.runtime.sendMessage({
      type: "DOWNLOAD_BUNDLE",
      bundle: stopResult.bundle,
      filename,
    });
    
    showFallback(filename);
  } else {
    showError(processResult.error || "Processing failed");
  }
}

async function handleCancelCapture(): Promise<void> {
  if (!currentTabId) return;
  
  stopStatsPolling();
  
  await chrome.runtime.sendMessage({
    type: "CANCEL_CAPTURE",
    tabId: currentTabId,
  });

  // Return to map-found state
  const state = await chrome.runtime.sendMessage({ 
    type: "GET_TAB_STATE", 
    tabId: currentTabId 
  });
  
  if (state.maps?.count > 0) {
    showMapFound(state.maps);
  } else {
    showNoMap();
  }
}

function handleNewCapture(): void {
  init();
}

// ============================================================================
// STATS POLLING
// ============================================================================

function startStatsPolling(): void {
  stopStatsPolling();
  
  statsInterval = setInterval(async () => {
    if (!currentTabId) return;
    
    const state = await chrome.runtime.sendMessage({ 
      type: "GET_TAB_STATE", 
      tabId: currentTabId 
    });
    
    if (state.capture?.status === "recording") {
      updateRecordingStats(state.capture);
    } else {
      stopStatsPolling();
    }
  }, 500);
}

function stopStatsPolling(): void {
  if (statsInterval) {
    clearInterval(statsInterval);
    statsInterval = null;
  }
}

// ============================================================================
// HELPERS
// ============================================================================

function showFallback(filename: string): void {
  hideAllStates();
  completeState.classList.remove("hidden");
  
  filenameEl.textContent = filename;
  completeState.innerHTML = `
    <p class="icon">📦</p>
    <p>Capture bundle downloaded!</p>
    <p class="filename">${filename}</p>
    <p class="hint" style="margin-top: 8px;">
      Cloud processing unavailable.<br>
      Process manually with CLI:<br>
      <code style="background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 11px;">
        webmap-archive process &lt;file&gt;
      </code>
    </p>
    <div class="actions">
      <button id="new-capture-btn" class="secondary">📸 New Capture</button>
    </div>
  `;
  
  // Re-attach event handler
  document.getElementById("new-capture-btn")?.addEventListener("click", handleNewCapture);
}

function generateFilename(bundle: any): string {
  const url = new URL(bundle.metadata.url);
  const host = url.hostname.replace(/\./g, "-");
  const date = bundle.metadata.capturedAt.split("T")[0];
  return `${host}-${date}.webmap-capture.json`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// Initialize on load
document.addEventListener("DOMContentLoaded", init);

// Clean up on unload
window.addEventListener("unload", () => {
  stopStatsPolling();
});
```

### Task 10: DevTools Panel (optional advanced capture)

Create `extension/src/devtools/devtools.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
</head>
<body>
  <script src="devtools.js"></script>
</body>
</html>
```

Create `extension/src/devtools/devtools.ts`:

```typescript
/**
 * DevTools page - creates the panel.
 */

chrome.devtools.panels.create(
  "WebMap Archiver",
  "icons/icon-16.png",
  "panel.html",
  (panel) => {
    // Panel created
  }
);
```

Create `extension/src/devtools/panel.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      padding: 12px;
      background: #fff;
    }
    h2 { font-size: 14px; margin-bottom: 12px; }
    button {
      padding: 6px 12px;
      border: 1px solid #ccc;
      border-radius: 4px;
      background: #fff;
      cursor: pointer;
      margin-right: 8px;
    }
    button:hover { background: #f5f5f5; }
    button.recording { background: #ffebee; border-color: #f44336; }
    .status { margin-top: 12px; color: #666; }
    .count { font-weight: bold; color: #333; }
  </style>
</head>
<body>
  <h2>Network Capture</h2>
  <p>Record network requests to include tile data in your capture.</p>
  
  <div style="margin-top: 12px;">
    <button id="start-btn">▶️ Start Recording</button>
    <button id="stop-btn" disabled>⏹️ Stop & Export</button>
  </div>
  
  <div class="status" id="status">
    Ready to record.
  </div>

  <script src="panel.js"></script>
</body>
</html>
```

Create `extension/src/devtools/panel.ts`:

```typescript
/**
 * DevTools panel for HAR capture.
 * 
 * Uses chrome.devtools.network API to capture requests.
 */

const startBtn = document.getElementById("start-btn") as HTMLButtonElement;
const stopBtn = document.getElementById("stop-btn") as HTMLButtonElement;
const status = document.getElementById("status")!;

let isRecording = false;
let entries: chrome.devtools.network.Request[] = [];

startBtn.addEventListener("click", startRecording);
stopBtn.addEventListener("click", stopRecording);

function startRecording(): void {
  isRecording = true;
  entries = [];
  
  startBtn.disabled = true;
  startBtn.classList.add("recording");
  stopBtn.disabled = false;
  
  updateStatus();
  
  // Listen for network requests
  chrome.devtools.network.onRequestFinished.addListener(handleRequest);
}

function stopRecording(): void {
  isRecording = false;
  
  startBtn.disabled = false;
  startBtn.classList.remove("recording");
  stopBtn.disabled = true;
  
  chrome.devtools.network.onRequestFinished.removeListener(handleRequest);
  
  // Export HAR
  chrome.devtools.network.getHAR((harLog) => {
    // Send to background script
    chrome.runtime.sendMessage({
      type: "HAR_CAPTURED",
      har: harLog,
      tabId: chrome.devtools.inspectedWindow.tabId,
    });
    
    status.innerHTML = `Captured ${harLog.entries.length} requests. HAR data sent to extension.`;
  });
}

function handleRequest(request: chrome.devtools.network.Request): void {
  entries.push(request);
  updateStatus();
}

function updateStatus(): void {
  if (isRecording) {
    const tileCount = entries.filter((e) => isTileRequest(e.request.url)).length;
    status.innerHTML = `Recording... <span class="count">${entries.length}</span> requests (<span class="count">${tileCount}</span> tiles)`;
  }
}

function isTileRequest(url: string): boolean {
  // Check for common tile URL patterns
  return /\/\d+\/\d+\/\d+\.(pbf|mvt|png|jpg|jpeg|webp)/.test(url) ||
         /tiles.*\/\d+\/\d+\/\d+/.test(url);
}
```

### Task 11: Icons

Create placeholder icons. For `extension/icons/`, create 16x16, 48x48, and 128x128 PNG icons. As a placeholder, you can generate simple colored squares or use an icon generator.

---

## 4. Testing Instructions

### Load Extension in Chrome

1. Run `npm install && npm run build` in the extension directory
2. Open `chrome://extensions/`
3. Enable "Developer mode" (toggle in top right)
4. Click "Load unpacked"
5. Select the `extension/dist` directory

### Test Map Detection

1. Navigate to a page with a MapLibre map (e.g., https://parkingregulations.nyc)
2. Extension badge should show "1"
3. Click extension icon to open popup
4. Should show "1 map detected (maplibre)"

### Test Capture

1. Click "Capture Map" button
2. Progress indicator should show
3. File should download as `.webmap-capture.json`
4. Verify JSON contains style, viewport, and metadata

### Test with Python CLI

```bash
# Process the captured bundle
webmap-archive process downloaded-capture.webmap-capture.json -o test-archive.zip

# Should create working archive
```

---

## 5. Edge Cases to Handle

1. **Multiple maps on page** - Show count in badge, capture primary (largest) map
2. **Maps in iframes** - Detect and handle cross-origin restrictions
3. **React/Vue wrapped maps** - Handle cases where map instance isn't directly accessible
4. **Style not loaded** - Wait for `style.load` event with timeout
5. **Mapbox access tokens** - Don't store tokens in capture bundle
6. **Large styles** - Handle styles with embedded data (data URLs)
7. **Private/auth-required tiles** - Note in metadata that tiles may not load
8. **Extension permissions denied** - Graceful fallback messaging
9. **Chrome vs Firefox** - Use WebExtensions polyfill for compatibility

---

## 6. Success Criteria

Phase 3 is complete when:

1. ✅ Extension loads in Chrome without errors
2. ✅ Map detection works for MapLibre, Mapbox, Leaflet
3. ✅ Badge shows detected map count (idle) or "REC" (recording)
4. ✅ Popup shows map type and version
5. ✅ **Two-step capture flow works:**
   - "Start Capture" begins recording via `chrome.debugger` API
   - Live stats update in popup (tile count, zoom levels, data size)
   - "Stop & Archive" ends recording and processes
6. ✅ Tiles are captured via network interception (not just style/viewport)
7. ✅ Cloud processing via Modal returns `.zip` archive
8. ✅ Fallback to local service works when cloud unavailable
9. ✅ Fallback to bundle download works when no service available
10. ✅ Captured bundle works with `webmap-archive process` CLI command
11. ✅ No console errors during normal operation

---

## 7. Modal Cloud Deployment

The extension requires a Modal cloud backend for one-click archive processing. See the separate **Modal Deployment Guide** (`modal-deployment-guide.md`) for complete instructions.

### Quick Setup

```bash
# Install Modal CLI
pip install modal

# Authenticate
modal token new

# Deploy the backend
cd cli
modal deploy src/webmap_archiver/modal_app.py
```

### After Deployment

1. Note your Modal username from the deployment output
2. Update `extension/src/config.ts`:

```typescript
cloudEndpoint: "https://YOUR_USERNAME--webmap-archiver-process.modal.run",
```

3. Rebuild the extension: `npm run build`

### Testing Cloud Integration

1. Load extension in Chrome
2. Navigate to a page with a map
3. Click "Capture Map"
4. Progress should show "Uploading to cloud..." → "Processing..."
5. Archive should auto-download as `.zip` file

---

## 8. Future Enhancements (Not in Phase 3)

- Firefox support via WebExtensions polyfill
- Safari Web Extension support  
- Tile data extraction from HAR (currently style + viewport only; HAR passed to Modal)
- Zotero/Are.na quick-save buttons (Phase 4)
- Settings page for configuration (custom endpoints, etc.)
- Offline queue for captures when no network