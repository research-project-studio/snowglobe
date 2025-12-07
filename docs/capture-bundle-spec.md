# Capture Bundle Specification v1.0

## Overview

The **Capture Bundle** is a JSON interchange format used to transfer captured web map data from the browser extension to the processing backend (Modal cloud or local CLI). It contains everything needed to reconstruct an offline map archive.

**File Extension:** `.webmap-capture.json`

**MIME Type:** `application/json`

---

## Schema

```json
{
  "version": "1.0",
  "metadata": { ... },
  "viewport": { ... },
  "style": { ... },
  "har": { ... },
  "tiles": [ ... ],
  "resources": { ... }
}
```

---

## Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | string | ✅ | Schema version. Currently `"1.0"` |
| `metadata` | object | ✅ | Capture metadata (URL, timestamp, etc.) |
| `viewport` | object | ✅ | Map viewport state at capture time |
| `style` | object | ❌ | MapLibre/Mapbox GL style JSON |
| `har` | object | ❌ | HAR 1.2 log of network requests |
| `tiles` | array | ❌ | Pre-extracted tile data |
| `resources` | object | ❌ | Additional resources (sprites, glyphs) |

---

## Field Definitions

### `metadata` (required)

Information about the capture source and context.

```json
{
  "metadata": {
    "url": "https://example.com/map",
    "title": "Example Map",
    "capturedAt": "2024-01-15T10:30:00.000Z",
    "userAgent": "Mozilla/5.0 ...",
    "mapLibrary": {
      "type": "maplibre",
      "version": "4.0.0"
    },
    "captureStats": {
      "totalRequests": 312,
      "tileCount": 247,
      "zoomLevels": [10, 11, 12, 13, 14],
      "estimatedSize": 4523000,
      "recordingDuration": 45000
    }
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | ✅ | Source page URL |
| `title` | string | ✅ | Page title (from `document.title`) |
| `capturedAt` | string | ✅ | ISO 8601 timestamp |
| `userAgent` | string | ❌ | Browser user agent |
| `mapLibrary` | object | ❌ | Detected map library info |
| `mapLibrary.type` | string | ❌ | One of: `maplibre`, `mapbox`, `leaflet`, `openlayers`, `unknown` |
| `mapLibrary.version` | string | ❌ | Library version if detected |
| `captureStats` | object | ❌ | Recording statistics |
| `captureStats.totalRequests` | number | ❌ | Total network requests captured |
| `captureStats.tileCount` | number | ❌ | Number of tile requests |
| `captureStats.zoomLevels` | number[] | ❌ | Zoom levels captured |
| `captureStats.estimatedSize` | number | ❌ | Estimated data size in bytes |
| `captureStats.recordingDuration` | number | ❌ | Recording duration in milliseconds |

---

### `viewport` (required)

Map viewport state at the time of capture (or final state when recording stopped).

```json
{
  "viewport": {
    "center": [-74.006, 40.7128],
    "zoom": 12,
    "bounds": [
      [-74.1, 40.6],
      [-73.9, 40.8]
    ],
    "bearing": 0,
    "pitch": 0
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `center` | [number, number] | ✅ | [longitude, latitude] |
| `zoom` | number | ✅ | Zoom level (0-22) |
| `bounds` | [[number, number], [number, number]] | ❌ | [[sw_lng, sw_lat], [ne_lng, ne_lat]] |
| `bearing` | number | ❌ | Map rotation in degrees (default: 0) |
| `pitch` | number | ❌ | Map tilt in degrees (default: 0) |

**Note:** Coordinates use WGS84 (EPSG:4326) with longitude first.

---

### `style` (optional)

MapLibre/Mapbox GL Style Specification JSON. Captured via `map.getStyle()`.

```json
{
  "style": {
    "version": 8,
    "name": "My Map Style",
    "sources": {
      "openmaptiles": {
        "type": "vector",
        "url": "https://tiles.example.com/tiles.json"
      }
    },
    "layers": [
      {
        "id": "background",
        "type": "background",
        "paint": {
          "background-color": "#f8f4f0"
        }
      }
    ],
    "sprite": "https://example.com/sprites/sprite",
    "glyphs": "https://example.com/fonts/{fontstack}/{range}.pbf"
  }
}
```

The style object follows the [MapLibre Style Specification](https://maplibre.org/maplibre-style-spec/).

**Important:** The processor will rewrite source URLs to point to local PMTiles files in the archive.

---

### `har` (optional)

HTTP Archive (HAR) 1.2 format log containing captured network requests. Used when tiles are not pre-extracted.

```json
{
  "har": {
    "log": {
      "version": "1.2",
      "creator": {
        "name": "WebMap Archiver",
        "version": "0.1.0"
      },
      "entries": [
        {
          "startedDateTime": "2024-01-15T10:30:01.000Z",
          "request": {
            "method": "GET",
            "url": "https://tiles.example.com/12/1205/1539.pbf"
          },
          "response": {
            "status": 200,
            "content": {
              "size": 45678,
              "mimeType": "application/x-protobuf",
              "text": "base64-encoded-content...",
              "encoding": "base64"
            }
          }
        }
      ]
    }
  }
}
```

The HAR format follows the [HAR 1.2 Specification](http://www.softwareishard.com/blog/har-12-spec/).

**Key fields used by the processor:**

| Path | Description |
|------|-------------|
| `entries[].request.url` | Used to identify tile requests |
| `entries[].response.status` | Must be 200 for valid tiles |
| `entries[].response.content.text` | Base64-encoded response body |
| `entries[].response.content.mimeType` | Used to determine tile format |

---

### `tiles` (optional)

Pre-extracted tile data. When present, the processor uses this instead of parsing the HAR.

```json
{
  "tiles": [
    {
      "z": 12,
      "x": 1205,
      "y": 1539,
      "source": "openmaptiles",
      "data": "base64-encoded-tile-content...",
      "format": "pbf"
    },
    {
      "z": 12,
      "x": 1205,
      "y": 1539,
      "source": "satellite",
      "data": "base64-encoded-png-content...",
      "format": "png"
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `z` | number | ✅ | Zoom level |
| `x` | number | ✅ | Tile X coordinate |
| `y` | number | ✅ | Tile Y coordinate |
| `source` | string | ✅ | Source identifier (matches style sources) |
| `data` | string | ✅ | Base64-encoded tile content |
| `format` | string | ✅ | One of: `pbf`, `mvt`, `png`, `jpg`, `webp` |

**Tile Coordinate System:** Uses the standard Web Mercator / Slippy Map tile scheme (XYZ).

---

### `resources` (optional)

Additional map resources that should be bundled.

```json
{
  "resources": {
    "sprites": {
      "sprite": "base64-encoded-png...",
      "sprite@2x": "base64-encoded-png...",
      "sprite.json": "{ \"icon-1\": { ... } }",
      "sprite@2x.json": "{ \"icon-1\": { ... } }"
    },
    "glyphs": {
      "Roboto Regular/0-255.pbf": "base64-encoded-pbf...",
      "Roboto Bold/0-255.pbf": "base64-encoded-pbf..."
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sprites` | object | Map of sprite filename → base64 content |
| `glyphs` | object | Map of glyph path → base64 content |

**Note:** Sprite and glyph capture is optional. The viewer can fall back to CDN-hosted resources if not bundled.

---

## Processing Behavior

### Priority Order

The processor handles tile data in this priority:

1. **`tiles` array** - If present and non-empty, use pre-extracted tiles
2. **`har` log** - If present, extract tiles from HAR entries
3. **Style sources only** - If neither present, archive will only contain style (no tiles)

### Tile Source Matching

When processing HAR entries, tiles are matched to style sources by:

1. URL pattern matching against source `tiles` templates
2. Hostname matching against source URLs
3. Fallback to generic source name derived from hostname

### Output

The processor produces a ZIP archive containing:

```
archive.zip
├── viewer.html           # Self-contained map viewer
├── manifest.json         # Archive metadata
└── tiles/
    ├── source1.pmtiles   # Tiles grouped by source
    └── source2.pmtiles
```

---

## Validation

### Required Fields Check

```python
def validate_bundle(bundle: dict) -> bool:
    # Version check
    if bundle.get("version") != "1.0":
        raise ValidationError("Unsupported version")
    
    # Required metadata
    metadata = bundle.get("metadata", {})
    if not metadata.get("url"):
        raise ValidationError("metadata.url is required")
    if not metadata.get("capturedAt"):
        raise ValidationError("metadata.capturedAt is required")
    
    # Required viewport
    viewport = bundle.get("viewport", {})
    if "center" not in viewport:
        raise ValidationError("viewport.center is required")
    if "zoom" not in viewport:
        raise ValidationError("viewport.zoom is required")
    
    return True
```

### Tile Data Validation

```python
def validate_tile(tile: dict) -> bool:
    required = ["z", "x", "y", "source", "data", "format"]
    for field in required:
        if field not in tile:
            raise ValidationError(f"tile.{field} is required")
    
    if tile["format"] not in ["pbf", "mvt", "png", "jpg", "webp"]:
        raise ValidationError(f"Invalid tile format: {tile['format']}")
    
    # Validate base64
    try:
        base64.b64decode(tile["data"])
    except:
        raise ValidationError("tile.data is not valid base64")
    
    return True
```

---

## Examples

### Minimal Bundle (Style Only)

```json
{
  "version": "1.0",
  "metadata": {
    "url": "https://example.com/map",
    "title": "Example Map",
    "capturedAt": "2024-01-15T10:30:00.000Z"
  },
  "viewport": {
    "center": [-74.006, 40.7128],
    "zoom": 12
  },
  "style": {
    "version": 8,
    "sources": {},
    "layers": []
  }
}
```

### Full Bundle (With Tiles)

```json
{
  "version": "1.0",
  "metadata": {
    "url": "https://parkingregulations.nyc/",
    "title": "NYC Parking Regulations",
    "capturedAt": "2024-01-15T10:30:00.000Z",
    "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...",
    "mapLibrary": {
      "type": "maplibre",
      "version": "4.0.0"
    },
    "captureStats": {
      "totalRequests": 312,
      "tileCount": 247,
      "zoomLevels": [10, 11, 12, 13, 14],
      "estimatedSize": 4523000,
      "recordingDuration": 45000
    }
  },
  "viewport": {
    "center": [-73.9857, 40.7484],
    "zoom": 14,
    "bounds": [
      [-74.0479, 40.6829],
      [-73.9065, 40.8820]
    ],
    "bearing": 0,
    "pitch": 0
  },
  "style": {
    "version": 8,
    "name": "Parking Regulations",
    "sources": {
      "maptiler": {
        "type": "vector",
        "url": "https://api.maptiler.com/tiles/v3/tiles.json"
      },
      "parking": {
        "type": "vector",
        "tiles": ["https://parkingregulations.nyc/tiles/{z}/{x}/{y}.pbf"]
      }
    },
    "layers": [
      {
        "id": "background",
        "type": "background",
        "paint": { "background-color": "#f8f4f0" }
      },
      {
        "id": "parking-signs",
        "type": "symbol",
        "source": "parking",
        "source-layer": "signs",
        "layout": { "icon-image": "parking-sign" }
      }
    ]
  },
  "tiles": [
    {
      "z": 14,
      "x": 4825,
      "y": 6156,
      "source": "maptiler",
      "data": "H4sIAAAAAAAAA...",
      "format": "pbf"
    },
    {
      "z": 14,
      "x": 4825,
      "y": 6156,
      "source": "parking",
      "data": "H4sIAAAAAAAAA...",
      "format": "pbf"
    }
  ]
}
```

---

## TypeScript Type Definition

```typescript
interface CaptureBundle {
  version: "1.0";
  metadata: CaptureMetadata;
  viewport: CaptureViewport;
  style?: MapLibreStyle;
  har?: HARLog;
  tiles?: CapturedTile[];
  resources?: CapturedResources;
}

interface CaptureMetadata {
  url: string;
  title: string;
  capturedAt: string;
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

interface CaptureViewport {
  center: [number, number];
  zoom: number;
  bounds?: [[number, number], [number, number]];
  bearing?: number;
  pitch?: number;
}

interface CapturedTile {
  z: number;
  x: number;
  y: number;
  source: string;
  data: string;
  format: "pbf" | "mvt" | "png" | "jpg" | "webp";
}

interface CapturedResources {
  sprites?: Record<string, string>;
  glyphs?: Record<string, string>;
}

// HAR types (simplified)
interface HARLog {
  log: {
    version: string;
    creator: { name: string; version: string };
    entries: HAREntry[];
  };
}

interface HAREntry {
  startedDateTime: string;
  request: {
    method: string;
    url: string;
  };
  response: {
    status: number;
    content: {
      size: number;
      mimeType: string;
      text?: string;
      encoding?: string;
    };
  };
}
```

---

## Python Type Definition

```python
from dataclasses import dataclass
from typing import Optional, Literal
from datetime import datetime

@dataclass
class MapLibraryInfo:
    type: Literal["maplibre", "mapbox", "leaflet", "openlayers", "unknown"]
    version: Optional[str] = None

@dataclass
class CaptureStats:
    total_requests: int
    tile_count: int
    zoom_levels: list[int]
    estimated_size: int
    recording_duration: int

@dataclass
class CaptureMetadata:
    url: str
    title: str
    captured_at: datetime
    user_agent: Optional[str] = None
    map_library: Optional[MapLibraryInfo] = None
    capture_stats: Optional[CaptureStats] = None

@dataclass
class CaptureViewport:
    center: tuple[float, float]
    zoom: float
    bounds: Optional[tuple[tuple[float, float], tuple[float, float]]] = None
    bearing: float = 0.0
    pitch: float = 0.0

@dataclass
class CapturedTile:
    z: int
    x: int
    y: int
    source: str
    data: str  # base64
    format: Literal["pbf", "mvt", "png", "jpg", "webp"]

@dataclass
class CaptureBundle:
    version: str
    metadata: CaptureMetadata
    viewport: CaptureViewport
    style: Optional[dict] = None
    har: Optional[dict] = None
    tiles: Optional[list[CapturedTile]] = None
    resources: Optional[dict] = None
```

---

## Changelog

### v1.0 (2024-01-15)

- Initial specification
- Support for MapLibre/Mapbox GL styles
- HAR-based tile extraction
- Pre-extracted tile array
- Sprite and glyph resources

---

## References

- [MapLibre Style Specification](https://maplibre.org/maplibre-style-spec/)
- [HAR 1.2 Specification](http://www.softwareishard.com/blog/har-12-spec/)
- [PMTiles Specification](https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md)
- [Slippy Map Tilenames](https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames)