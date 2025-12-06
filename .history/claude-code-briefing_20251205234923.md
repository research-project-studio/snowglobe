# WebMap Archiver: Claude Code Implementation Briefing

## Project Overview

**Goal:** Build a Python CLI tool that transforms HAR files into self-contained web map archives, preserving vector and raster tiles, styles, and all dependencies in a portable format.

**Primary Use Case:** Enabling researchers, educators, and archivists to preserve web maps for long-term access and offline viewing.

**This Briefing Covers:** Phase 1 implementation (MVP) with analysis of a real-world test HAR file.

---

## Test Data Analysis

### HAR File: `parkingregulations_nyc.har`

This HAR file captures a session from https://parkingregulations.nyc, a MapLibre-based map showing NYC parking regulations.

#### Key Statistics
- **Total entries:** 93 HTTP requests
- **HAR version:** 1.2
- **Creator:** WebInspector (Chrome DevTools)

#### Tile Sources Detected

| Source | Domain | Format | Tiles | Zoom Levels | Role |
|--------|--------|--------|-------|-------------|------|
| MapTiler Basemap | api.maptiler.com/tiles/v3 | .pbf | 33 | z10-z13 | Basemap |
| Parking Regulations | tiles.wxy-labs.org/parking_regs_v2 | .mvt | 33 | z10-z13 | Data Overlay |

#### URL Patterns

```
# MapTiler Basemap (vector tiles)
https://api.maptiler.com/tiles/v3/{z}/{x}/{y}.pbf?key=bDxRFX9VXGshWw7IhDCz

# Parking Regulations Overlay (vector tiles)  
https://tiles.wxy-labs.org/parking_regs_v2/{z}/{x}/{y}.mvt
```

**Important Observations:**
1. Both sources use standard XYZ tile scheme
2. MapTiler URLs contain API key parameter (`?key=...`)
3. Different file extensions: `.pbf` vs `.mvt` (both are vector tiles)
4. The parking layer is NOT in the style.json - it's added programmatically by the web app

#### Geographic Coverage

```
Bounds:
  West:  -74.5312°
  East:  -73.4766°
  South: 40.4469°
  North: 40.9799°
  Center: (-74.0039, 40.7134)  # New York City area

Per-Zoom Breakdown:
  z10: 12 tiles (6 unique positions × 2 sources)
  z11: 18 tiles
  z12: 12 tiles
  z13: 24 tiles
  Total: 66 tiles (33 per source)
```

#### Resources Found

| Resource Type | Count | Has Content | Details |
|---------------|-------|-------------|---------|
| Style JSON | 1 | ✓ | MapTiler Dataviz Dark style |
| TileJSON | 1 | ✓ | MapTiler v3 metadata |
| Sprite PNG | 1 | ✓ | @2x variant only |
| Sprite JSON | 1 | ✓ | @2x variant only |
| Glyphs | 2 | ✓ | Two font stacks, 0-255 range |
| GeoJSON | 0 | - | None captured |

#### Content Encoding in HAR

```python
# JSON content (style.json, sprite.json, tilejson)
encoding: None  # Plain text in 'text' field

# Binary content (tiles, sprite.png, glyphs)
encoding: "base64"  # Base64-encoded in 'text' field
```

#### Style.json Structure

```json
{
  "version": 8,
  "name": "Dataviz Dark",
  "sources": {
    "maptiler_planet": {
      "type": "vector",
      "url": "https://api.maptiler.com/tiles/v3/tiles.json?key=..."
    }
  },
  "sprite": "https://api.maptiler.com/maps/dataviz-dark/sprite",
  "glyphs": "https://api.maptiler.com/fonts/{fontstack}/{range}.pbf?key=...",
  "layers": [/* 42 layers, all using maptiler_planet source */]
}
```

#### Critical Pattern: Data Layers Separate from Base Style

**This is the common and expected pattern, not an edge case.** The parking regulations layer is NOT in the style.json—it's added programmatically by the web application after loading the base style. This separation is fundamental to how most custom web maps work:

1. **Base style** (style.json) = commodity infrastructure from a provider (MapTiler, Mapbox, ESRI, etc.)
2. **Data layers** = the intellectual contribution, added via JavaScript at runtime

This is exactly why we designed the DATA_ONLY archive mode—the base style is replaceable commodity infrastructure, while the data layers and their tile sources are what actually need preserving.

**Implications for the viewer:**
- The viewer MUST be able to render tile sources that don't have layer definitions in the captured style.json
- For Phase 1: Generate basic/default layer styling for "orphan" tile sources (sources detected in HAR but not defined in style.json)
- For Phase 2+: Allow user-provided layer configuration, or attempt to capture the runtime-modified style

**Detection strategy:**
- Parse style.json to find which sources it references
- Compare against tile sources detected in HAR
- Any HAR tile source NOT in style.json is a "data layer" that needs generated styling

---

## Phase 1 Scope

### Goals
1. Parse HAR files and extract all entries with content
2. Detect and classify tile requests (vector/raster, basemap/overlay)
3. Extract tile coordinates from URLs
4. Build PMTiles archive from captured tiles
5. **Handle "orphan" data layers** - tile sources in HAR but not in style.json (this is the common case!)
6. Generate a MapLibre HTML viewer with basic styling for all sources
7. Output a ZIP archive with manifest

### Critical Design Requirement

**Data layers are commonly separate from the base style.** The viewer must:
- Detect tile sources from HAR that are NOT defined in the captured style.json
- Generate reasonable default layer styling for these "orphan" sources
- Ensure ALL captured tile data is visible in the viewer, not just basemap

This is not a workaround—this is the primary use case. Most maps worth archiving follow this pattern.

### Not in Phase 1
- Fetching missing tiles (±N zoom expansion)
- Style.json processing/rewriting
- Sprite/glyph extraction and bundling
- Multiple archive modes (FULL only)
- Provider-specific pattern matching (generic XYZ only)

---

## Implementation Specification

### Project Structure

```
webmap-archiver/
├── src/
│   └── webmap_archiver/
│       ├── __init__.py
│       ├── cli.py              # Click CLI
│       ├── har/
│       │   ├── __init__.py
│       │   ├── parser.py       # HAR file parsing
│       │   └── classifier.py   # Request classification
│       ├── tiles/
│       │   ├── __init__.py
│       │   ├── detector.py     # Tile URL detection
│       │   ├── coverage.py     # Geographic bounds calculation
│       │   └── pmtiles.py      # PMTiles building
│       ├── viewer/
│       │   ├── __init__.py
│       │   └── generator.py    # HTML viewer generation
│       └── archive/
│           ├── __init__.py
│           ├── packager.py     # ZIP assembly
│           └── manifest.py     # Manifest generation
├── tests/
│   ├── __init__.py
│   ├── test_har_parser.py
│   ├── test_tile_detector.py
│   └── fixtures/
│       └── parkingregulations_nyc.har  # Test file
├── pyproject.toml
└── README.md
```

### Dependencies

```toml
[project]
name = "webmap-archiver"
version = "0.1.0"
requires-python = ">=3.10"

dependencies = [
    "click>=8.0",
    "pmtiles>=3.0", 
    "pydantic>=2.0",
    "rich>=13.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "ruff>=0.1",
]

[project.scripts]
webmap-archive = "webmap_archiver.cli:main"
```

---

## Module Implementations

### 1. HAR Parser (`har/parser.py`)

```python
"""
HAR file parsing with content extraction.

Key requirements:
- Handle both plain text and base64-encoded content
- Extract response body as bytes
- Parse timestamps
- Filter to successful responses (2xx status)
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import base64


@dataclass
class HAREntry:
    """A single request/response from a HAR file."""
    url: str
    method: str
    status: int
    mime_type: str
    content: bytes | None
    timestamp: datetime
    
    @property
    def is_successful(self) -> bool:
        return 200 <= self.status < 300
    
    @property
    def has_content(self) -> bool:
        return self.content is not None and len(self.content) > 0


class HARParser:
    """Parse HAR files and extract entries with content."""
    
    def __init__(self, har_path: Path):
        self.har_path = Path(har_path)
    
    def parse(self) -> list[HAREntry]:
        """Parse HAR file and return all entries."""
        with open(self.har_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        entries = []
        for entry in data['log']['entries']:
            parsed = self._parse_entry(entry)
            if parsed:
                entries.append(parsed)
        
        return entries
    
    def _parse_entry(self, entry: dict) -> HAREntry | None:
        """Parse a single HAR entry."""
        request = entry.get('request', {})
        response = entry.get('response', {})
        content_info = response.get('content', {})
        
        # Extract and decode content
        content = self._decode_content(content_info)
        
        return HAREntry(
            url=request.get('url', ''),
            method=request.get('method', 'GET'),
            status=response.get('status', 0),
            mime_type=content_info.get('mimeType', ''),
            content=content,
            timestamp=self._parse_timestamp(entry.get('startedDateTime'))
        )
    
    def _decode_content(self, content_info: dict) -> bytes | None:
        """Decode content from HAR format to bytes."""
        text = content_info.get('text')
        if text is None:
            return None
        
        encoding = content_info.get('encoding', '')
        
        if encoding == 'base64':
            try:
                return base64.b64decode(text)
            except Exception:
                return None
        else:
            # Plain text - encode to bytes
            return text.encode('utf-8')
    
    def _parse_timestamp(self, ts: str | None) -> datetime:
        """Parse ISO 8601 timestamp from HAR."""
        if not ts:
            return datetime.now()
        # Handle 'Z' suffix and various formats
        ts = ts.replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return datetime.now()
```

### 2. Request Classifier (`har/classifier.py`)

```python
"""
Classify HAR entries by their role in web mapping.

Key requirements:
- Distinguish vector tiles, raster tiles, styles, sprites, glyphs
- Use both MIME type and URL patterns
- Return classification with confidence score
"""

from enum import Enum, auto
from dataclasses import dataclass
import re

from .parser import HAREntry


class RequestType(Enum):
    VECTOR_TILE = auto()
    RASTER_TILE = auto()
    STYLE_JSON = auto()
    SPRITE_IMAGE = auto()
    SPRITE_JSON = auto()
    GLYPH = auto()
    TILEJSON = auto()
    GEOJSON = auto()
    OTHER = auto()


@dataclass
class Classification:
    """Result of classifying a HAR entry."""
    request_type: RequestType
    confidence: float  # 0.0 to 1.0


class RequestClassifier:
    """Classify HAR entries by their map-related function."""
    
    # URL patterns with associated types and confidence
    PATTERNS = [
        # Vector tiles - high confidence patterns
        (r'/\d+/\d+/\d+\.pbf', RequestType.VECTOR_TILE, 0.95),
        (r'/\d+/\d+/\d+\.mvt', RequestType.VECTOR_TILE, 0.95),
        (r'\.vector\.pbf', RequestType.VECTOR_TILE, 0.9),
        
        # Raster tiles
        (r'/\d+/\d+/\d+\.png(?:\?|$)', RequestType.RASTER_TILE, 0.9),
        (r'/\d+/\d+/\d+\.jpg(?:\?|$)', RequestType.RASTER_TILE, 0.9),
        (r'/\d+/\d+/\d+\.webp(?:\?|$)', RequestType.RASTER_TILE, 0.9),
        
        # Style
        (r'style\.json', RequestType.STYLE_JSON, 0.95),
        (r'/styles/.*\.json', RequestType.STYLE_JSON, 0.85),
        
        # Sprites
        (r'sprite.*\.png', RequestType.SPRITE_IMAGE, 0.95),
        (r'sprite.*\.json', RequestType.SPRITE_JSON, 0.95),
        
        # Glyphs
        (r'/fonts/.*\.pbf', RequestType.GLYPH, 0.95),
        (r'/\d+-\d+\.pbf', RequestType.GLYPH, 0.8),
        
        # TileJSON
        (r'tiles\.json', RequestType.TILEJSON, 0.95),
        
        # GeoJSON
        (r'\.geojson', RequestType.GEOJSON, 0.95),
    ]
    
    # MIME type mappings
    MIME_HINTS = {
        'application/x-protobuf': RequestType.VECTOR_TILE,
        'application/vnd.mapbox-vector-tile': RequestType.VECTOR_TILE,
        'application/geo+json': RequestType.GEOJSON,
    }
    
    def classify(self, entry: HAREntry) -> Classification:
        """Classify a single HAR entry."""
        # First, try URL pattern matching
        for pattern, req_type, confidence in self.PATTERNS:
            if re.search(pattern, entry.url, re.IGNORECASE):
                return Classification(req_type, confidence)
        
        # Fall back to MIME type
        mime = entry.mime_type.split(';')[0].strip().lower()
        if mime in self.MIME_HINTS:
            return Classification(self.MIME_HINTS[mime], 0.7)
        
        return Classification(RequestType.OTHER, 0.0)
    
    def classify_all(self, entries: list[HAREntry]) -> dict[RequestType, list[HAREntry]]:
        """Classify all entries and group by type."""
        grouped: dict[RequestType, list[HAREntry]] = {t: [] for t in RequestType}
        
        for entry in entries:
            if entry.is_successful and entry.has_content:
                result = self.classify(entry)
                grouped[result.request_type].append(entry)
        
        return grouped
```

### 3. Tile Detector (`tiles/detector.py`)

```python
"""
Detect tile URLs and extract coordinates.

Key requirements:
- Support XYZ pattern: /{z}/{x}/{y}.(pbf|mvt|png|...)
- Handle both vector (.pbf, .mvt) and raster (.png, .jpg) tiles
- Group tiles by source (based on URL template)
- Detect tile type (vector vs raster)
"""

from dataclasses import dataclass
from typing import Literal
import re
from urllib.parse import urlparse


@dataclass(frozen=True)
class TileCoord:
    """A single tile's coordinates."""
    z: int
    x: int
    y: int
    
    def __hash__(self):
        return hash((self.z, self.x, self.y))


@dataclass
class TileSource:
    """A detected tile source."""
    name: str
    url_template: str
    tile_type: Literal["vector", "raster"]
    format: str  # pbf, mvt, png, jpg, etc.


@dataclass
class DetectedTile:
    """A tile detected from a URL."""
    coord: TileCoord
    source: TileSource
    content: bytes


class TileDetector:
    """Detect tiles from URLs and extract coordinates."""
    
    # Pattern to match z/x/y in URL path
    COORD_PATTERN = re.compile(r'/(\d{1,2})/(\d+)/(\d+)\.(\w+)')
    
    # File extensions by type
    VECTOR_EXTENSIONS = {'pbf', 'mvt'}
    RASTER_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    
    def detect(self, url: str, content: bytes) -> DetectedTile | None:
        """
        Detect if URL is a tile request and extract info.
        
        Returns DetectedTile if URL matches tile pattern, None otherwise.
        """
        match = self.COORD_PATTERN.search(url)
        if not match:
            return None
        
        z, x, y, ext = match.groups()
        ext = ext.lower()
        
        # Determine tile type
        if ext in self.VECTOR_EXTENSIONS:
            tile_type = "vector"
        elif ext in self.RASTER_EXTENSIONS:
            tile_type = "raster"
        else:
            return None
        
        coord = TileCoord(z=int(z), x=int(x), y=int(y))
        source = self._create_source(url, ext, tile_type)
        
        return DetectedTile(coord=coord, source=source, content=content)
    
    def _create_source(
        self, 
        url: str, 
        ext: str, 
        tile_type: Literal["vector", "raster"]
    ) -> TileSource:
        """Create a TileSource from a URL."""
        # Create URL template by replacing coordinates with placeholders
        template = self.COORD_PATTERN.sub(r'/{z}/{x}/{y}.' + ext, url)
        
        # Remove query parameters for cleaner template (but keep for name)
        parsed = urlparse(url)
        
        # Generate source name from domain and path
        name = self._generate_source_name(parsed)
        
        return TileSource(
            name=name,
            url_template=template,
            tile_type=tile_type,
            format=ext
        )
    
    def _generate_source_name(self, parsed) -> str:
        """Generate a human-readable source name."""
        # Use domain + first meaningful path segment
        domain = parsed.netloc.replace('api.', '').replace('tiles.', '')
        domain = domain.split('.')[0]
        
        # Get path segments before z/x/y
        path_parts = [p for p in parsed.path.split('/') if p and not p.isdigit()]
        if path_parts:
            # Skip common prefixes like 'tiles', 'v3', etc.
            meaningful = [p for p in path_parts if p not in ('tiles', 'v3', 'v4', 'v1')]
            if meaningful:
                return f"{domain}-{meaningful[0]}"
        
        return domain
    
    def group_by_source(
        self, 
        tiles: list[DetectedTile]
    ) -> dict[str, tuple[TileSource, list[tuple[TileCoord, bytes]]]]:
        """Group detected tiles by their source."""
        groups: dict[str, tuple[TileSource, list]] = {}
        
        for tile in tiles:
            key = tile.source.url_template
            if key not in groups:
                groups[key] = (tile.source, [])
            groups[key][1].append((tile.coord, tile.content))
        
        return groups
```

### 4. Coverage Calculator (`tiles/coverage.py`)

```python
"""
Calculate geographic coverage from tile coordinates.

Key requirements:
- Convert tile coords to geographic bounds
- Calculate overall bounding box
- Support zoom level analysis
"""

from dataclasses import dataclass
import math

from .detector import TileCoord


@dataclass
class GeoBounds:
    """Geographic bounding box in WGS84."""
    west: float   # min longitude
    south: float  # min latitude
    east: float   # max longitude
    north: float  # max latitude
    
    @property
    def center(self) -> tuple[float, float]:
        """Return (longitude, latitude) of center."""
        return (
            (self.west + self.east) / 2,
            (self.south + self.north) / 2
        )


class CoverageCalculator:
    """Calculate geographic coverage from tiles."""
    
    def tile_to_bounds(self, coord: TileCoord) -> GeoBounds:
        """Convert a tile coordinate to geographic bounds."""
        n = 2 ** coord.z
        
        # Northwest corner (top-left)
        west = coord.x / n * 360.0 - 180.0
        north = math.degrees(
            math.atan(math.sinh(math.pi * (1 - 2 * coord.y / n)))
        )
        
        # Southeast corner (bottom-right)
        east = (coord.x + 1) / n * 360.0 - 180.0
        south = math.degrees(
            math.atan(math.sinh(math.pi * (1 - 2 * (coord.y + 1) / n)))
        )
        
        return GeoBounds(west=west, south=south, east=east, north=north)
    
    def calculate_bounds(self, tiles: list[TileCoord]) -> GeoBounds:
        """Calculate overall bounds from a list of tiles."""
        if not tiles:
            raise ValueError("No tiles provided")
        
        min_west = float('inf')
        min_south = float('inf')
        max_east = float('-inf')
        max_north = float('-inf')
        
        for tile in tiles:
            bounds = self.tile_to_bounds(tile)
            min_west = min(min_west, bounds.west)
            min_south = min(min_south, bounds.south)
            max_east = max(max_east, bounds.east)
            max_north = max(max_north, bounds.north)
        
        return GeoBounds(
            west=min_west,
            south=min_south,
            east=max_east,
            north=max_north
        )
    
    def get_zoom_range(self, tiles: list[TileCoord]) -> tuple[int, int]:
        """Get min and max zoom levels from tiles."""
        if not tiles:
            raise ValueError("No tiles provided")
        
        zooms = [t.z for t in tiles]
        return (min(zooms), max(zooms))
    
    def count_by_zoom(self, tiles: list[TileCoord]) -> dict[int, int]:
        """Count tiles per zoom level."""
        counts: dict[int, int] = {}
        for tile in tiles:
            counts[tile.z] = counts.get(tile.z, 0) + 1
        return dict(sorted(counts.items()))
```

### 5. PMTiles Builder (`tiles/pmtiles.py`)

```python
"""
Build PMTiles archives from tiles.

Key requirements:
- Support vector tiles (pbf format)
- Set proper metadata (bounds, zoom range, etc.)
- Handle tile compression (gzip)

Note: Uses the pmtiles Python library.
"""

from pathlib import Path
from dataclasses import dataclass
import gzip
import io

from pmtiles.tile import TileType, Compression
from pmtiles.writer import Writer

from .detector import TileCoord, TileSource
from .coverage import GeoBounds


@dataclass
class PMTilesMetadata:
    """Metadata for a PMTiles archive."""
    name: str
    description: str
    bounds: GeoBounds
    min_zoom: int
    max_zoom: int
    tile_type: str  # "vector" or "raster"
    format: str     # "pbf", "png", etc.


class PMTilesBuilder:
    """Build a PMTiles archive from tiles."""
    
    def __init__(self, output_path: Path):
        self.output_path = Path(output_path)
        self.tiles: list[tuple[TileCoord, bytes]] = []
        self.metadata: PMTilesMetadata | None = None
    
    def add_tile(self, coord: TileCoord, data: bytes) -> None:
        """Add a tile to the archive."""
        self.tiles.append((coord, data))
    
    def set_metadata(self, metadata: PMTilesMetadata) -> None:
        """Set archive metadata."""
        self.metadata = metadata
    
    def build(self) -> None:
        """Build and write the PMTiles archive."""
        if not self.tiles:
            raise ValueError("No tiles to write")
        
        if not self.metadata:
            raise ValueError("Metadata not set")
        
        # Determine tile type for PMTiles
        if self.metadata.tile_type == "vector":
            tile_type = TileType.MVT
        else:
            # Map format to tile type
            format_map = {
                'png': TileType.PNG,
                'jpg': TileType.JPEG,
                'jpeg': TileType.JPEG,
                'webp': TileType.WEBP,
            }
            tile_type = format_map.get(self.metadata.format, TileType.PNG)
        
        # Open writer
        with open(self.output_path, 'wb') as f:
            writer = Writer(f)
            
            # Write tiles
            for coord, data in self.tiles:
                # Ensure vector tiles are gzipped
                if tile_type == TileType.MVT:
                    data = self._ensure_gzipped(data)
                
                tile_id = self._coord_to_tileid(coord)
                writer.write_tile(tile_id, data)
            
            # Write header with metadata
            header = {
                "tile_type": tile_type,
                "tile_compression": Compression.GZIP if tile_type == TileType.MVT else Compression.NONE,
                "min_zoom": self.metadata.min_zoom,
                "max_zoom": self.metadata.max_zoom,
                "min_lon_e7": int(self.metadata.bounds.west * 1e7),
                "min_lat_e7": int(self.metadata.bounds.south * 1e7),
                "max_lon_e7": int(self.metadata.bounds.east * 1e7),
                "max_lat_e7": int(self.metadata.bounds.north * 1e7),
                "center_lon_e7": int(self.metadata.bounds.center[0] * 1e7),
                "center_lat_e7": int(self.metadata.bounds.center[1] * 1e7),
                "center_zoom": (self.metadata.min_zoom + self.metadata.max_zoom) // 2,
            }
            
            json_metadata = {
                "name": self.metadata.name,
                "description": self.metadata.description,
            }
            
            writer.finalize(header, json_metadata)
    
    def _coord_to_tileid(self, coord: TileCoord) -> int:
        """Convert tile coordinate to PMTiles tile ID."""
        # PMTiles uses Hilbert curve tile IDs
        # The pmtiles library handles this internally with zxy_to_tileid
        from pmtiles.tile import zxy_to_tileid
        return zxy_to_tileid(coord.z, coord.x, coord.y)
    
    def _ensure_gzipped(self, data: bytes) -> bytes:
        """Ensure data is gzipped."""
        # Check if already gzipped (magic bytes)
        if data[:2] == b'\x1f\x8b':
            return data
        
        # Gzip the data
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
            gz.write(data)
        return buf.getvalue()
```

### 6. Viewer Generator (`viewer/generator.py`)

```python
"""
Generate self-contained HTML viewer for archived maps.

Key requirements:
- MapLibre GL JS viewer
- PMTiles protocol support
- CRITICAL: Generate styling for ALL tile sources, including those not in original style.json
- Display archive bounds and zoom info
- Work when served locally (not from file://)

Design note: Data layers are commonly added programmatically and won't be in the 
captured style.json. The viewer MUST render these "orphan" sources with sensible
default styling. This is the primary use case, not an edge case.
"""

from dataclasses import dataclass
from pathlib import Path
import json

from ..tiles.coverage import GeoBounds


@dataclass 
class ViewerConfig:
    """Configuration for the viewer."""
    name: str
    bounds: GeoBounds
    min_zoom: int
    max_zoom: int
    tile_sources: list[dict]  # [{name, path, type, is_orphan}]
    created_at: str


VIEWER_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} - WebMap Archive</title>
    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
    <script src="https://unpkg.com/pmtiles@2.11.0/dist/pmtiles.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
        .info-panel {{
            position: absolute;
            top: 10px;
            left: 10px;
            background: white;
            padding: 12px 16px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.15);
            max-width: 300px;
            font-size: 13px;
            z-index: 100;
        }}
        .info-panel h1 {{
            font-size: 16px;
            margin-bottom: 8px;
        }}
        .info-panel .meta {{
            color: #666;
            line-height: 1.6;
        }}
        .layer-toggle {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #eee;
        }}
        .layer-toggle label {{
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            padding: 4px 0;
        }}
        .layer-toggle input {{
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info-panel">
        <h1>{name}</h1>
        <div class="meta">
            <div>Archived: {created_at}</div>
            <div>Zoom: {min_zoom}-{max_zoom}</div>
            <div>Sources: {source_count}</div>
        </div>
        <div class="layer-toggle" id="layer-controls"></div>
    </div>
    <script>
        // Register PMTiles protocol
        const protocol = new pmtiles.Protocol();
        maplibregl.addProtocol("pmtiles", protocol.tile);
        
        // Archive configuration
        const config = {config_json};
        
        // Color palette for data layers WITHOUT extracted styling
        const DEFAULT_COLORS = [
            "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", 
            "#ff7f00", "#ffff33", "#a65628", "#f781bf"
        ];
        let colorIndex = 0;
        
        // Build sources object
        const sources = {{}};
        config.tileSources.forEach(src => {{
            sources[src.name] = {{
                type: "vector",
                url: "pmtiles://" + src.path
            }};
        }});
        
        // Create style with layers for ALL sources
        const style = {{
            version: 8,
            sources: sources,
            layers: [
                {{
                    id: "background",
                    type: "background",
                    paint: {{ "background-color": "#1a1a2e" }}
                }}
            ]
        }};
        
        // Track layers for toggle controls
        const layerGroups = {{}};
        
        // Helper to build color expression from extracted colors
        function buildColorExpression(colors, sourceLayer) {{
            if (!colors || Object.keys(colors).length === 0) {{
                return null;
            }}
            
            // Build a case expression: ["case", condition1, color1, condition2, color2, ..., default]
            const expr = ["case"];
            for (const [category, color] of Object.entries(colors)) {{
                if (category !== 'unknown' && category !== 'other' && color) {{
                    // Assume properties are boolean flags (==1 means true)
                    expr.push(["==", ["get", category], 1]);
                    expr.push(color);
                }}
            }}
            // Default color
            expr.push(colors.unknown || colors.other || "#888888");
            
            return expr;
        }}
        
        // Add layers for each source
        config.tileSources.forEach((src, i) => {{
            const isDataLayer = src.isOrphan !== false;
            const extracted = src.extractedStyle;
            
            // Determine colors to use
            let color;
            let colorExpr = null;
            
            if (extracted && extracted.colors && Object.keys(extracted.colors).length > 0) {{
                // Use extracted colors - build expression
                colorExpr = buildColorExpression(extracted.colors, extracted.sourceLayer);
                color = Object.values(extracted.colors)[0];  // Fallback single color
                console.log("Using extracted colors for", src.name, "confidence:", extracted.confidence);
            }} else {{
                // Fall back to default palette
                color = isDataLayer ? DEFAULT_COLORS[colorIndex++ % DEFAULT_COLORS.length] : "#4a4a6a";
            }}
            
            const layerType = extracted?.layerType || "line";
            const sourceLayer = extracted?.sourceLayer || "";
            const layerIds = [];
            
            // Create layer based on extracted or inferred type
            if (layerType === "line" || !isDataLayer) {{
                const lineId = src.name + "-line";
                style.layers.push({{
                    id: lineId,
                    type: "line",
                    source: src.name,
                    "source-layer": sourceLayer,
                    paint: {{
                        "line-color": colorExpr || color,
                        "line-width": isDataLayer ? 2 : 1,
                        "line-opacity": isDataLayer ? 0.9 : 0.5
                    }}
                }});
                layerIds.push(lineId);
            }}
            
            if (layerType === "fill" || !extracted) {{
                const fillId = src.name + "-fill";
                style.layers.push({{
                    id: fillId,
                    type: "fill",
                    source: src.name,
                    "source-layer": sourceLayer,
                    filter: ["==", ["geometry-type"], "Polygon"],
                    paint: {{
                        "fill-color": colorExpr || color,
                        "fill-opacity": isDataLayer ? 0.4 : 0.2
                    }}
                }});
                layerIds.push(fillId);
            }}
            
            if (layerType === "circle" || !extracted) {{
                const circleId = src.name + "-circle";
                style.layers.push({{
                    id: circleId,
                    type: "circle", 
                    source: src.name,
                    "source-layer": sourceLayer,
                    filter: ["==", ["geometry-type"], "Point"],
                    paint: {{
                        "circle-color": colorExpr || color,
                        "circle-radius": isDataLayer ? 6 : 3,
                        "circle-stroke-color": "#ffffff",
                        "circle-stroke-width": isDataLayer ? 1 : 0
                    }}
                }});
                layerIds.push(circleId);
            }}
            
            layerGroups[src.name] = {{
                label: src.name + (extracted?.confidence ? ` (${{Math.round(extracted.confidence * 100)}}% styled)` : ""),
                layers: layerIds,
                isData: isDataLayer,
                hasExtractedStyle: !!(extracted && extracted.colors && Object.keys(extracted.colors).length > 0)
            }};
        }});
        
        const map = new maplibregl.Map({{
            container: "map",
            style: style,
            center: [{center_lon}, {center_lat}],
            zoom: {initial_zoom},
            maxBounds: [[{west}, {south}], [{east}, {north}]]
        }});
        
        map.addControl(new maplibregl.NavigationControl(), "top-right");
        map.addControl(new maplibregl.ScaleControl(), "bottom-right");
        
        // Add layer toggle controls
        map.on("load", () => {{
            const controlsDiv = document.getElementById("layer-controls");
            
            Object.entries(layerGroups).forEach(([name, group]) => {{
                const label = document.createElement("label");
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.checked = true;
                checkbox.addEventListener("change", (e) => {{
                    const visibility = e.target.checked ? "visible" : "none";
                    group.layers.forEach(layerId => {{
                        if (map.getLayer(layerId)) {{
                            map.setLayoutProperty(layerId, "visibility", visibility);
                        }}
                    }});
                }});
                
                const span = document.createElement("span");
                let labelText = group.label;
                if (group.isData) {{
                    labelText += group.hasExtractedStyle ? " ✓" : " (default style)";
                }}
                span.textContent = labelText;
                span.title = group.hasExtractedStyle 
                    ? "Styling extracted from original JavaScript"
                    : group.isData 
                        ? "Using default styling - original could not be extracted"
                        : "Basemap layer";
                
                label.appendChild(checkbox);
                label.appendChild(span);
                controlsDiv.appendChild(label);
            }});
        }});
        
        // Log errors for debugging
        map.on("error", (e) => {{
            console.error("Map error:", e);
        }});
    </script>
</body>
</html>
'''


### 6b. Style Extractor (`styles/extractor.py`)

```python
"""
Extract styling information from JavaScript files in HAR.

BACKGROUND:
Data layer styling is typically NOT in style.json—it's added programmatically
by the web application JavaScript at runtime. However, the JS files ARE captured
in the HAR, and we can extract useful styling information via regex patterns.

PHASE 1 APPROACH:
- Regex-based extraction of common patterns
- Extract: colors, source-layer names, basic paint properties
- Works for ~80% of common cases

PHASE 2 IMPROVEMENTS (documented for future):
- Proper JS AST parsing (e.g., using esprima, babel-parser via subprocess)
- Handle complex MapLibre expressions
- Extract interactive states (hover, click)
- Handle minified variable references

LIMITATIONS:
- Minified code uses single-letter variable names (D, V, w, G, etc.)
- Complex expressions may span multiple variables
- Some patterns may be missed by regex
- Results should be validated against actual tile data
"""

from dataclasses import dataclass, field
from typing import Any
import re
import json


@dataclass
class ExtractedLayerStyle:
    """Styling information extracted from JavaScript."""
    source_id: str | None = None
    source_layer: str | None = None
    tile_url: str | None = None
    layer_type: str | None = None  # "line", "fill", "circle", etc.
    colors: dict[str, str] = field(default_factory=dict)  # category -> hex color
    paint_properties: dict[str, Any] = field(default_factory=dict)
    
    # Metadata about extraction quality
    extraction_confidence: float = 0.0  # 0.0 - 1.0
    extraction_notes: list[str] = field(default_factory=list)
    raw_matches: dict[str, str] = field(default_factory=dict)  # For debugging


@dataclass
class StyleExtractionReport:
    """Report on what styling was/wasn't extracted."""
    extracted_layers: list[ExtractedLayerStyle]
    unmatched_sources: list[str]  # Tile sources with no extracted styling
    js_files_analyzed: int
    extraction_method: str = "regex_v1"
    notes: list[str] = field(default_factory=list)
    
    def to_manifest_section(self) -> dict:
        """Generate manifest section documenting extraction results."""
        return {
            "style_extraction": {
                "method": self.extraction_method,
                "method_description": "Regex-based extraction from minified JavaScript",
                "limitations": [
                    "Complex MapLibre expressions may be simplified or incomplete",
                    "Interactive states (hover, click) not fully captured", 
                    "Minified variable names require pattern matching heuristics",
                    "Some layer properties may be missing"
                ],
                "future_improvements": [
                    "JavaScript AST parsing for complete expression extraction",
                    "Runtime style capture via browser extension",
                    "User-provided layer configuration override"
                ],
                "layers_extracted": len(self.extracted_layers),
                "sources_without_styling": self.unmatched_sources,
                "js_files_analyzed": self.js_files_analyzed,
                "notes": self.notes,
                "layers": [
                    {
                        "source_id": layer.source_id,
                        "source_layer": layer.source_layer,
                        "layer_type": layer.layer_type,
                        "colors_extracted": len(layer.colors),
                        "confidence": layer.extraction_confidence,
                        "notes": layer.extraction_notes
                    }
                    for layer in self.extracted_layers
                ]
            }
        }


class StyleExtractor:
    """Extract layer styling from JavaScript files."""
    
    # Patterns for common MapLibre/Mapbox styling constructs
    PATTERNS = {
        # Hex color mappings: {category:"#hexcolor",...}
        'color_object': re.compile(
            r'\{[a-z_]+:"#[0-9a-fA-F]{6}"(?:,[a-z_]+:"#[0-9a-fA-F]{6}")*\}'
        ),
        
        # Individual color assignments: category:"#hexcolor"
        'color_pair': re.compile(
            r'([a-z_]+):"(#[0-9a-fA-F]{6})"'
        ),
        
        # Tile URL patterns
        'tile_url': re.compile(
            r'(https?://[^"\']+/\{z\}/\{x\}/\{y\}[^"\'\s]*)'
        ),
        
        # Source-layer string (often a specific identifier)
        'source_layer': re.compile(
            r'"source-layer"\s*:\s*([A-Za-z_][A-Za-z0-9_]*)|'
            r'"source-layer"\s*:\s*"([^"]+)"'
        ),
        
        # Layer type
        'layer_type': re.compile(
            r'type\s*:\s*"(line|fill|circle|symbol)"'
        ),
        
        # Paint properties
        'line_width': re.compile(
            r'"line-width"\s*:\s*(\d+(?:\.\d+)?|\[[^\]]+\])'
        ),
        'line_opacity': re.compile(
            r'"line-opacity"\s*:\s*(\d+(?:\.\d+)?|\[[^\]]+\])'
        ),
        'fill_opacity': re.compile(
            r'"fill-opacity"\s*:\s*(\d+(?:\.\d+)?|\[[^\]]+\])'
        ),
        
        # Layer definition object pattern
        'layer_def': re.compile(
            r'\{id:[^,]+,source:[^,]+,"source-layer":[^,]+,type:"(line|fill|circle)"[^}]+\}'
        ),
        
        # Source definition with tiles array
        'source_def': re.compile(
            r'\{type:"vector",tiles:\["([^"]+)"\]\}'
        ),
    }
    
    def extract_from_js(self, js_content: str, source_url: str = "") -> list[ExtractedLayerStyle]:
        """
        Extract styling information from JavaScript content.
        
        Args:
            js_content: The JavaScript file content
            source_url: URL of the JS file (for reporting)
            
        Returns:
            List of extracted layer styles
        """
        extracted = []
        
        # Find all tile URLs in the JS
        tile_urls = self.PATTERNS['tile_url'].findall(js_content)
        
        for tile_url in tile_urls:
            # Skip common basemap URLs
            if any(provider in tile_url.lower() for provider in 
                   ['maptiler', 'mapbox', 'arcgis', 'openstreetmap', 'carto']):
                continue
            
            style = ExtractedLayerStyle(tile_url=tile_url)
            style.extraction_notes.append(f"Found tile URL: {tile_url}")
            
            # Try to find associated styling near this URL in the code
            url_pos = js_content.find(tile_url)
            if url_pos >= 0:
                # Search in a window around the URL
                window_start = max(0, url_pos - 2000)
                window_end = min(len(js_content), url_pos + 2000)
                context = js_content[window_start:window_end]
                
                # Extract colors
                self._extract_colors(context, style)
                
                # Extract source-layer
                self._extract_source_layer(context, style)
                
                # Extract layer type
                self._extract_layer_type(context, style)
                
                # Extract paint properties
                self._extract_paint_properties(context, style)
            
            # Also do a global search for color objects
            if not style.colors:
                self._extract_colors(js_content, style)
            
            # Calculate confidence
            style.extraction_confidence = self._calculate_confidence(style)
            
            if style.colors or style.source_layer:
                extracted.append(style)
        
        return extracted
    
    def _extract_colors(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract color mappings from content."""
        # Find color object patterns
        color_objects = self.PATTERNS['color_object'].findall(content)
        
        for obj_str in color_objects:
            pairs = self.PATTERNS['color_pair'].findall(obj_str)
            for category, color in pairs:
                style.colors[category] = color
        
        if style.colors:
            style.extraction_notes.append(f"Extracted {len(style.colors)} color mappings")
            style.raw_matches['colors'] = str(style.colors)
    
    def _extract_source_layer(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract source-layer name."""
        matches = self.PATTERNS['source_layer'].findall(content)
        for match in matches:
            # match is a tuple from the alternation groups
            source_layer = match[0] or match[1]
            if source_layer and source_layer not in ('null', 'undefined'):
                style.source_layer = source_layer
                style.extraction_notes.append(f"Found source-layer: {source_layer}")
                break
    
    def _extract_layer_type(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract layer type (line, fill, circle, etc.)."""
        matches = self.PATTERNS['layer_type'].findall(content)
        if matches:
            style.layer_type = matches[0]
            style.extraction_notes.append(f"Found layer type: {style.layer_type}")
    
    def _extract_paint_properties(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract paint properties."""
        for prop_name in ['line_width', 'line_opacity', 'fill_opacity']:
            matches = self.PATTERNS[prop_name].findall(content)
            if matches:
                # Convert prop_name to CSS property name
                css_name = prop_name.replace('_', '-')
                try:
                    # Try to parse as number or JSON
                    value = matches[0]
                    if value.startswith('['):
                        style.paint_properties[css_name] = json.loads(value)
                    else:
                        style.paint_properties[css_name] = float(value)
                except (json.JSONDecodeError, ValueError):
                    style.paint_properties[css_name] = matches[0]
    
    def _calculate_confidence(self, style: ExtractedLayerStyle) -> float:
        """Calculate confidence score for extraction."""
        score = 0.0
        
        if style.tile_url:
            score += 0.2
        if style.source_layer:
            score += 0.2
        if style.layer_type:
            score += 0.1
        if style.colors:
            # More colors = higher confidence
            score += min(0.3, len(style.colors) * 0.05)
        if style.paint_properties:
            score += min(0.2, len(style.paint_properties) * 0.05)
        
        return min(1.0, score)
    
    def generate_maplibre_layer(self, style: ExtractedLayerStyle, source_name: str) -> dict | None:
        """
        Generate a MapLibre layer definition from extracted styling.
        
        Returns None if insufficient information was extracted.
        """
        if not style.colors and not style.layer_type:
            return None
        
        layer_type = style.layer_type or "line"
        
        layer = {
            "id": f"{source_name}-extracted",
            "source": source_name,
            "type": layer_type,
        }
        
        if style.source_layer:
            layer["source-layer"] = style.source_layer
        
        # Build paint properties
        paint = {}
        
        if style.colors and len(style.colors) > 1:
            # Generate a case expression for colors
            # This is a simplified version - full expressions would need AST parsing
            color_expr = ["case"]
            for category, color in style.colors.items():
                if category not in ('unknown', 'other', ''):
                    color_expr.extend([
                        ["==", ["get", category], 1],
                        color
                    ])
            # Default color
            color_expr.append(style.colors.get('unknown', '#888888'))
            
            if layer_type == "line":
                paint["line-color"] = color_expr
                paint["line-width"] = style.paint_properties.get("line-width", 2)
                paint["line-opacity"] = style.paint_properties.get("line-opacity", 0.8)
            elif layer_type == "fill":
                paint["fill-color"] = color_expr
                paint["fill-opacity"] = style.paint_properties.get("fill-opacity", 0.6)
            elif layer_type == "circle":
                paint["circle-color"] = color_expr
                paint["circle-radius"] = 5
        else:
            # Single color or no colors - use defaults
            default_color = list(style.colors.values())[0] if style.colors else "#e41a1c"
            if layer_type == "line":
                paint["line-color"] = default_color
                paint["line-width"] = 2
            elif layer_type == "fill":
                paint["fill-color"] = default_color
                paint["fill-opacity"] = 0.6
            elif layer_type == "circle":
                paint["circle-color"] = default_color
                paint["circle-radius"] = 5
        
        layer["paint"] = paint
        
        return layer


def extract_styles_from_har(
    entries: list['HAREntry'],
    detected_tile_sources: list[str]
) -> StyleExtractionReport:
    """
    Extract styling from all JavaScript files in HAR.
    
    Args:
        entries: Parsed HAR entries
        detected_tile_sources: List of tile source URLs found in HAR
        
    Returns:
        StyleExtractionReport with extraction results
    """
    extractor = StyleExtractor()
    all_extracted = []
    js_count = 0
    
    for entry in entries:
        # Check if this is a JavaScript file
        mime = entry.mime_type.lower()
        url = entry.url.lower()
        
        if 'javascript' in mime or url.endswith('.js'):
            if entry.content:
                js_count += 1
                try:
                    js_text = entry.content.decode('utf-8')
                    extracted = extractor.extract_from_js(js_text, entry.url)
                    all_extracted.extend(extracted)
                except UnicodeDecodeError:
                    pass
    
    # Determine which sources still have no styling
    extracted_urls = {s.tile_url for s in all_extracted if s.tile_url}
    unmatched = [url for url in detected_tile_sources if url not in extracted_urls]
    
    report = StyleExtractionReport(
        extracted_layers=all_extracted,
        unmatched_sources=unmatched,
        js_files_analyzed=js_count,
        notes=[
            f"Analyzed {js_count} JavaScript files",
            f"Extracted styling for {len(all_extracted)} layers",
            f"{len(unmatched)} tile sources have no extracted styling"
        ]
    )
    
    return report
```

---

class ViewerGenerator:
    """Generate HTML viewer for archived maps."""
    
    def generate(self, config: ViewerConfig) -> str:
        """Generate viewer HTML from configuration."""
        center = config.bounds.center
        
        # Build config JSON for JavaScript
        config_dict = {
            "name": config.name,
            "bounds": {
                "west": config.bounds.west,
                "south": config.bounds.south,
                "east": config.bounds.east,
                "north": config.bounds.north,
            },
            "minZoom": config.min_zoom,
            "maxZoom": config.max_zoom,
            "tileSources": config.tile_sources,
            "createdAt": config.created_at,
        }
        
        return VIEWER_TEMPLATE.format(
            name=config.name,
            created_at=config.created_at,
            min_zoom=config.min_zoom,
            max_zoom=config.max_zoom,
            source_count=len(config.tile_sources),
            config_json=json.dumps(config_dict, indent=2),
            center_lon=center[0],
            center_lat=center[1],
            initial_zoom=(config.min_zoom + config.max_zoom) // 2,
            west=config.bounds.west,
            south=config.bounds.south,
            east=config.bounds.east,
            north=config.bounds.north,
        )
    
    def write(self, config: ViewerConfig, output_path: Path) -> None:
        """Generate and write viewer to file."""
        html = self.generate(config)
        output_path.write_text(html, encoding='utf-8')
```

### 7. Archive Packager (`archive/packager.py`)

```python
"""
Package all components into a ZIP archive.

Key requirements:
- Create proper directory structure
- Include PMTiles, viewer, manifest
- Generate manifest.json with metadata
"""

from pathlib import Path
from datetime import datetime
import zipfile
import json
from dataclasses import dataclass, asdict

from ..tiles.coverage import GeoBounds


@dataclass
class TileSourceInfo:
    """Information about a tile source in the archive."""
    name: str
    path: str
    tile_type: str
    format: str
    tile_count: int
    zoom_range: tuple[int, int]


@dataclass
class ArchiveManifest:
    """Manifest describing the archive contents."""
    name: str
    description: str
    created_at: str
    version: str
    bounds: dict
    zoom_range: tuple[int, int]
    tile_sources: list[dict]
    viewer_path: str
    archive_mode: str = "full"
    style_extraction: dict = None  # Added: documents what styling was/wasn't extracted
    known_limitations: list[dict] = None  # Added: documents limitations for future work
    
    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "version": self.version,
            "bounds": self.bounds,
            "zoom_range": list(self.zoom_range),
            "tile_sources": self.tile_sources,
            "viewer_path": self.viewer_path,
            "archive_mode": self.archive_mode,
        }
        
        # Include style extraction report if available
        if self.style_extraction:
            result["style_extraction"] = self.style_extraction
        
        # Include known limitations for future refinement
        if self.known_limitations:
            result["known_limitations"] = self.known_limitations
        else:
            # Default limitations documentation
            result["known_limitations"] = [
                {
                    "id": "style_extraction_incomplete",
                    "area": "Data Layer Styling",
                    "description": "Styling for data layers added via JavaScript may be incomplete or simplified",
                    "impact": "Visual appearance may not match original map exactly",
                    "current_approach": "Regex-based extraction from minified JavaScript",
                    "future_improvements": [
                        "JavaScript AST parsing for complete expression extraction",
                        "Runtime style capture via browser extension calling map.getStyle()",
                        "User-provided layer configuration override file"
                    ],
                    "workaround": "Manually edit style/extracted_layers.json to refine styling"
                },
                {
                    "id": "interactive_states_missing", 
                    "area": "Interactivity",
                    "description": "Hover, click, and other interactive states not captured",
                    "impact": "Map is static view only",
                    "current_approach": "Not implemented in Phase 1",
                    "future_improvements": [
                        "Extract feature-state expressions from JavaScript",
                        "Capture event handlers and popup content"
                    ]
                },
                {
                    "id": "basemap_style_simplified",
                    "area": "Basemap Styling", 
                    "description": "Basemap uses captured style.json but sprites/glyphs may be missing",
                    "impact": "Labels and icons may not render",
                    "current_approach": "Style.json captured, sprites/glyphs not bundled in Phase 1",
                    "future_improvements": [
                        "Bundle sprite atlas and JSON",
                        "Bundle required glyph ranges",
                        "Rewrite URLs in style.json to local paths"
                    ]
                }
            ]
        
        return result


class ArchivePackager:
    """Package map archive into a ZIP file."""
    
    VERSION = "1.0.0"
    
    def __init__(self, output_path: Path):
        self.output_path = Path(output_path)
        self.temp_files: list[tuple[str, Path | bytes]] = []
        self.manifest: ArchiveManifest | None = None
    
    def add_pmtiles(self, name: str, pmtiles_path: Path) -> None:
        """Add a PMTiles file to the archive."""
        archive_path = f"tiles/{name}.pmtiles"
        self.temp_files.append((archive_path, pmtiles_path))
    
    def add_viewer(self, html_content: str) -> None:
        """Add the viewer HTML to the archive."""
        self.temp_files.append(("viewer.html", html_content.encode('utf-8')))
    
    def set_manifest(
        self,
        name: str,
        description: str,
        bounds: GeoBounds,
        zoom_range: tuple[int, int],
        tile_sources: list[TileSourceInfo],
        style_extraction: dict = None
    ) -> None:
        """Set the archive manifest."""
        self.manifest = ArchiveManifest(
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
            version=self.VERSION,
            bounds={
                "west": bounds.west,
                "south": bounds.south,
                "east": bounds.east,
                "north": bounds.north,
            },
            zoom_range=zoom_range,
            tile_sources=[
                {
                    "name": ts.name,
                    "path": ts.path,
                    "tile_type": ts.tile_type,
                    "format": ts.format,
                    "tile_count": ts.tile_count,
                    "zoom_range": list(ts.zoom_range),
                }
                for ts in tile_sources
            ],
            viewer_path="viewer.html",
            style_extraction=style_extraction,
        )
    
    def build(self) -> None:
        """Build the ZIP archive."""
        if not self.manifest:
            raise ValueError("Manifest not set")
        
        with zipfile.ZipFile(self.output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add manifest
            manifest_json = json.dumps(self.manifest.to_dict(), indent=2)
            zf.writestr("manifest.json", manifest_json)
            
            # Add all files
            for archive_path, content in self.temp_files:
                if isinstance(content, Path):
                    zf.write(content, archive_path)
                else:
                    zf.writestr(archive_path, content)
```

### 8. CLI (`cli.py`)

```python
"""
Command-line interface for webmap-archiver.

Commands:
- create: Create archive from HAR file
- inspect: Analyze HAR file without creating archive
"""

import click
from pathlib import Path
from datetime import datetime
import json
from rich.console import Console
from rich.table import Table

from .har.parser import HARParser
from .har.classifier import RequestClassifier, RequestType
from .tiles.detector import TileDetector
from .tiles.coverage import CoverageCalculator
from .tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
from .styles.extractor import StyleExtractor, extract_styles_from_har
from .viewer.generator import ViewerGenerator, ViewerConfig
from .archive.packager import ArchivePackager, TileSourceInfo

console = Console()


@click.group()
def main():
    """WebMap Archiver - Preserve web maps for offline access."""
    pass


@main.command()
@click.argument('har_file', type=click.Path(exists=True, path_type=Path))
@click.option('-o', '--output', type=click.Path(path_type=Path), help='Output ZIP path')
@click.option('-n', '--name', help='Archive name (default: derived from HAR filename)')
@click.option('-v', '--verbose', is_flag=True, help='Verbose output')
def create(har_file: Path, output: Path | None, name: str | None, verbose: bool):
    """Create an archive from a HAR file."""
    
    # Set defaults
    if output is None:
        output = har_file.with_suffix('.zip')
    if name is None:
        name = har_file.stem.replace('_', ' ').replace('-', ' ').title()
    
    console.print(f"[bold]Creating archive from:[/] {har_file}")
    console.print(f"[bold]Output:[/] {output}")
    console.print()
    
    # Step 1: Parse HAR
    with console.status("Parsing HAR file..."):
        parser = HARParser(har_file)
        entries = parser.parse()
    console.print(f"  Parsed [cyan]{len(entries)}[/] entries")
    
    # Step 2: Classify requests
    with console.status("Classifying requests..."):
        classifier = RequestClassifier()
        grouped = classifier.classify_all(entries)
    
    vector_tiles = grouped[RequestType.VECTOR_TILE]
    raster_tiles = grouped[RequestType.RASTER_TILE]
    console.print(f"  Found [cyan]{len(vector_tiles)}[/] vector tiles, [cyan]{len(raster_tiles)}[/] raster tiles")
    
    if not vector_tiles and not raster_tiles:
        console.print("[red]No tiles found in HAR file![/]")
        raise click.Abort()
    
    # Step 3: Detect tile sources
    with console.status("Detecting tile sources..."):
        detector = TileDetector()
        detected = []
        
        for entry in vector_tiles + raster_tiles:
            tile = detector.detect(entry.url, entry.content)
            if tile:
                detected.append(tile)
        
        sources = detector.group_by_source(detected)
    
    console.print(f"  Detected [cyan]{len(sources)}[/] tile sources:")
    for template, (source, tiles) in sources.items():
        console.print(f"    • {source.name}: {len(tiles)} tiles ({source.tile_type})")
    console.print()
    
    # Step 4: Calculate coverage
    coverage_calc = CoverageCalculator()
    all_coords = [t[0] for tiles in sources.values() for t in tiles[1]]
    bounds = coverage_calc.calculate_bounds(all_coords)
    zoom_range = coverage_calc.get_zoom_range(all_coords)
    
    console.print(f"[bold]Coverage:[/]")
    console.print(f"  Bounds: {bounds.west:.4f}, {bounds.south:.4f} to {bounds.east:.4f}, {bounds.north:.4f}")
    console.print(f"  Zoom: {zoom_range[0]}-{zoom_range[1]}")
    console.print()
    
    # Step 5: Build PMTiles for each source
    import tempfile
    temp_dir = Path(tempfile.mkdtemp())
    pmtiles_files: list[tuple[str, Path, TileSourceInfo]] = []
    
    for template, (source, tiles) in sources.items():
        console.print(f"Building PMTiles for [cyan]{source.name}[/]...")
        
        pmtiles_path = temp_dir / f"{source.name}.pmtiles"
        builder = PMTilesBuilder(pmtiles_path)
        
        for coord, content in tiles:
            builder.add_tile(coord, content)
        
        source_coords = [t[0] for t in tiles]
        source_bounds = coverage_calc.calculate_bounds(source_coords)
        source_zoom = coverage_calc.get_zoom_range(source_coords)
        
        builder.set_metadata(PMTilesMetadata(
            name=source.name,
            description=f"Tiles from {source.url_template}",
            bounds=source_bounds,
            min_zoom=source_zoom[0],
            max_zoom=source_zoom[1],
            tile_type=source.tile_type,
            format=source.format,
        ))
        
        builder.build()
        
        info = TileSourceInfo(
            name=source.name,
            path=f"tiles/{source.name}.pmtiles",
            tile_type=source.tile_type,
            format=source.format,
            tile_count=len(tiles),
            zoom_range=source_zoom,
        )
        pmtiles_files.append((source.name, pmtiles_path, info))
        console.print(f"  ✓ Created {pmtiles_path.name}")
    
    # Step 6: Extract styling from JavaScript files
    console.print("Extracting styling from JavaScript...")
    
    # Get all tile URLs for matching
    tile_urls = [info.path for _, _, info in pmtiles_files]  # Will refine this
    detected_urls = []
    for template, (source, tiles) in sources.items():
        detected_urls.append(source.url_template)
    
    style_report = extract_styles_from_har(entries, detected_urls)
    
    if style_report.extracted_layers:
        console.print(f"  ✓ Extracted styling for [cyan]{len(style_report.extracted_layers)}[/] layers")
        for layer in style_report.extracted_layers:
            console.print(f"    • {layer.source_layer or 'unknown'}: {len(layer.colors)} colors, confidence: {layer.extraction_confidence:.0%}")
    else:
        console.print("  [yellow]⚠ No data layer styling could be extracted from JavaScript[/]")
    
    if style_report.unmatched_sources:
        console.print(f"  [yellow]⚠ {len(style_report.unmatched_sources)} sources have no extracted styling[/]")
    
    # Step 7: Generate viewer
    # First, detect which sources are "orphan" (not in style.json)
    # This is the common case - data layers added programmatically
    style_sources = set()  # TODO: Extract from style.json in Phase 2
    # For now, use heuristics: sources from known basemap providers are not orphan
    BASEMAP_DOMAINS = ['maptiler.com', 'mapbox.com', 'arcgis.com', 'openstreetmap.org']
    
    console.print("Generating viewer...")
    viewer_gen = ViewerGenerator()
    
    tile_source_configs = []
    for _, _, info in pmtiles_files:
        # Detect if this is likely a basemap vs data layer
        is_basemap = any(domain in info.name.lower() for domain in ['maptiler', 'mapbox', 'esri', 'osm'])
        
        # Find extracted styling for this source if available
        extracted_style = None
        for layer in style_report.extracted_layers:
            if layer.tile_url and info.name.lower() in layer.tile_url.lower():
                extracted_style = layer
                break
        
        tile_source_configs.append({
            "name": info.name, 
            "path": info.path, 
            "type": info.tile_type,
            "isOrphan": not is_basemap,  # Data layers are "orphan" = not in base style
            "extractedStyle": {
                "sourceLayer": extracted_style.source_layer if extracted_style else None,
                "colors": extracted_style.colors if extracted_style else {},
                "layerType": extracted_style.layer_type if extracted_style else "line",
                "confidence": extracted_style.extraction_confidence if extracted_style else 0.0
            } if extracted_style or not is_basemap else None
        })
    
    viewer_config = ViewerConfig(
        name=name,
        bounds=bounds,
        min_zoom=zoom_range[0],
        max_zoom=zoom_range[1],
        tile_sources=tile_source_configs,
        created_at=datetime.now().strftime("%Y-%m-%d"),
    )
    viewer_html = viewer_gen.generate(viewer_config)
    
    # Step 8: Package archive
    
    # Step 8: Package archive
    console.print("Packaging archive...")
    packager = ArchivePackager(output)
    
    for name_, pmtiles_path, info in pmtiles_files:
        packager.add_pmtiles(name_, pmtiles_path)
    
    packager.add_viewer(viewer_html)
    
    # Write extracted styles to a separate file for manual refinement
    extracted_styles_json = json.dumps({
        "extraction_report": style_report.to_manifest_section(),
        "layers": [
            {
                "source_id": layer.source_id,
                "source_layer": layer.source_layer,
                "tile_url": layer.tile_url,
                "layer_type": layer.layer_type,
                "colors": layer.colors,
                "paint_properties": layer.paint_properties,
                "extraction_notes": layer.extraction_notes,
                "confidence": layer.extraction_confidence
            }
            for layer in style_report.extracted_layers
        ],
        "_comment": "This file documents extracted styling. Edit to refine layer appearance."
    }, indent=2)
    packager.temp_files.append(("style/extracted_layers.json", extracted_styles_json.encode('utf-8')))
    
    packager.set_manifest(
        name=name,
        description=f"WebMap archive created from {har_file.name}",
        bounds=bounds,
        zoom_range=zoom_range,
        tile_sources=[info for _, _, info in pmtiles_files],
        style_extraction=style_report.to_manifest_section()
    )
    
    packager.build()
    
    # Cleanup
    import shutil
    shutil.rmtree(temp_dir)
    
    console.print()
    console.print(f"[bold green]✓ Archive created:[/] {output}")
    console.print(f"  Size: {output.stat().st_size / 1024 / 1024:.2f} MB")


@main.command()
@click.argument('har_file', type=click.Path(exists=True, path_type=Path))
def inspect(har_file: Path):
    """Analyze a HAR file without creating an archive."""
    
    console.print(f"[bold]Analyzing:[/] {har_file}")
    console.print()
    
    # Parse
    parser = HARParser(har_file)
    entries = parser.parse()
    
    # Classify
    classifier = RequestClassifier()
    grouped = classifier.classify_all(entries)
    
    # Summary table
    table = Table(title="Request Classification")
    table.add_column("Type", style="cyan")
    table.add_column("Count", justify="right")
    
    for req_type in RequestType:
        count = len(grouped[req_type])
        if count > 0:
            table.add_row(req_type.name, str(count))
    
    console.print(table)
    console.print()
    
    # Detect tiles
    detector = TileDetector()
    detected = []
    
    for entry in grouped[RequestType.VECTOR_TILE] + grouped[RequestType.RASTER_TILE]:
        tile = detector.detect(entry.url, entry.content)
        if tile:
            detected.append(tile)
    
    if detected:
        sources = detector.group_by_source(detected)
        
        # Sources table
        table = Table(title="Tile Sources")
        table.add_column("Source")
        table.add_column("Type")
        table.add_column("Tiles", justify="right")
        table.add_column("Zoom Range")
        
        coverage_calc = CoverageCalculator()
        
        for template, (source, tiles) in sources.items():
            coords = [t[0] for t in tiles]
            zoom_range = coverage_calc.get_zoom_range(coords)
            table.add_row(
                source.name,
                source.tile_type,
                str(len(tiles)),
                f"z{zoom_range[0]}-z{zoom_range[1]}"
            )
        
        console.print(table)
        console.print()
        
        # Geographic coverage
        all_coords = [t[0] for tiles in sources.values() for t in tiles[1]]
        bounds = coverage_calc.calculate_bounds(all_coords)
        
        console.print("[bold]Geographic Coverage:[/]")
        console.print(f"  West:  {bounds.west:.4f}°")
        console.print(f"  East:  {bounds.east:.4f}°")
        console.print(f"  South: {bounds.south:.4f}°")
        console.print(f"  North: {bounds.north:.4f}°")
        console.print(f"  Center: ({bounds.center[0]:.4f}, {bounds.center[1]:.4f})")


if __name__ == '__main__':
    main()
```

---

## Testing Strategy

### Test with Real Data

Place `parkingregulations_nyc.har` in `tests/fixtures/` and create:

```python
# tests/test_integration.py

import pytest
from pathlib import Path

from webmap_archiver.har.parser import HARParser
from webmap_archiver.har.classifier import RequestClassifier, RequestType
from webmap_archiver.tiles.detector import TileDetector

FIXTURES = Path(__file__).parent / "fixtures"
HAR_FILE = FIXTURES / "parkingregulations_nyc.har"


@pytest.fixture
def har_entries():
    parser = HARParser(HAR_FILE)
    return parser.parse()


def test_har_parsing(har_entries):
    """Test that HAR file parses correctly."""
    assert len(har_entries) == 93
    
    # Check that entries have content
    with_content = [e for e in har_entries if e.has_content]
    assert len(with_content) > 50


def test_tile_classification(har_entries):
    """Test that tiles are classified correctly."""
    classifier = RequestClassifier()
    grouped = classifier.classify_all(har_entries)
    
    # Should find vector tiles
    vector_tiles = grouped[RequestType.VECTOR_TILE]
    assert len(vector_tiles) == 66  # 33 + 33 from two sources
    
    # Should find style, sprites, etc.
    assert len(grouped[RequestType.STYLE_JSON]) == 1
    assert len(grouped[RequestType.SPRITE_IMAGE]) >= 1
    assert len(grouped[RequestType.GLYPH]) >= 1


def test_tile_detection(har_entries):
    """Test that tile coordinates are extracted correctly."""
    classifier = RequestClassifier()
    grouped = classifier.classify_all(har_entries)
    
    detector = TileDetector()
    detected = []
    
    for entry in grouped[RequestType.VECTOR_TILE]:
        tile = detector.detect(entry.url, entry.content)
        if tile:
            detected.append(tile)
    
    # Should detect all 66 tiles
    assert len(detected) == 66
    
    # Check coordinate ranges
    zooms = set(t.coord.z for t in detected)
    assert zooms == {10, 11, 12, 13}
    
    # Should group into 2 sources
    sources = detector.group_by_source(detected)
    assert len(sources) == 2
```

---

## Expected Output

Running on `parkingregulations_nyc.har` should produce:

```
$ webmap-archive create parkingregulations_nyc.har -o nyc-parking.zip

Creating archive from: parkingregulations_nyc.har
Output: nyc-parking.zip

  Parsed 93 entries
  Found 66 vector tiles, 0 raster tiles
  Detected 2 tile sources:
    • maptiler-v3: 33 tiles (vector)
    • wxy-labs-parking_regs_v2: 33 tiles (vector)

Coverage:
  Bounds: -74.5312, 40.4469 to -73.4766, 40.9799
  Zoom: 10-13

Building PMTiles for maptiler-v3...
  ✓ Created maptiler-v3.pmtiles
Building PMTiles for wxy-labs-parking_regs_v2...
  ✓ Created wxy-labs-parking_regs_v2.pmtiles
Generating viewer...
Packaging archive...

✓ Archive created: nyc-parking.zip
  Size: 1.85 MB
```

Archive structure:
```
nyc-parking.zip
├── manifest.json
├── viewer.html
├── style/
│   └── extracted_layers.json   # Documented extracted styling for manual refinement
└── tiles/
    ├── maptiler-v3.pmtiles
    └── wxy-labs-parking_regs_v2.pmtiles
```

The `extracted_layers.json` file serves two purposes:
1. **Documentation**: Records what styling was/wasn't extracted, with confidence scores
2. **Manual refinement**: Users can edit this file to fix or improve layer styling

Example `extracted_layers.json`:
```json
{
  "extraction_report": {
    "method": "regex_v1",
    "limitations": ["Complex expressions may be simplified", "..."],
    "future_improvements": ["JavaScript AST parsing", "..."]
  },
  "layers": [
    {
      "source_layer": "parking_reg_sections_3fgb",
      "tile_url": "https://tiles.wxy-labs.org/parking_regs_v2/{z}/{x}/{y}.mvt",
      "layer_type": "line",
      "colors": {
        "vehicle": "#a432a8",
        "open": "#32a852",
        "bus": "#329aa8",
        "limited": "#a89832",
        "stop_stand": "#a86b32",
        "none": "#a83232",
        "gov": "#7532a8",
        "no_regs": "#517369"
      },
      "confidence": 0.75,
      "extraction_notes": ["Found 9 color mappings", "..."]
    }
  ],
  "_comment": "Edit this file to refine layer appearance."
}
```

---

## Known Limitations (Phase 1)

1. **Basic styling only** - The viewer generates default styling for all sources; it won't replicate the original map's visual appearance (proper style processing comes in Phase 2)
2. **No sprite/glyph bundling** - These resources aren't included yet, so text labels and icons won't render
3. **No tile fetching** - Only tiles present in HAR are archived (zoom expansion comes in Phase 2)
4. **FULL mode only** - No DATA_ONLY, STYLE_ONLY, or HYBRID modes yet
5. **Vector tiles focus** - Raster tile PMTiles building is supported but less tested
6. **Generic source-layer matching** - Uses empty string to match all source-layers; may need refinement for complex sources

**Note:** The pattern of data layers being separate from style.json is handled correctly—this is expected behavior, not a limitation.

---

## Manual Style Refinement Workflow

The archive includes `style/extracted_layers.json` which documents what styling was extracted and allows manual refinement:

### Step 1: Inspect Extraction Results
```bash
# Unzip and examine
unzip -p my-map.zip style/extracted_layers.json | jq .
```

### Step 2: Check Confidence Scores
Low confidence (<50%) suggests extraction was incomplete. Review `extraction_notes` for details.

### Step 3: Edit if Needed
```json
{
  "layers": [{
    "source_layer": "parking_reg_sections_3fgb",
    "colors": {
      "none": "#a83232",  // Correct this if colors are wrong
      "open": "#32a852"
    }
  }]
}
```

### Step 4: Regenerate Viewer (Future)
Phase 2 will include a command to regenerate viewer from edited config:
```bash
webmap-archive rebuild my-map.zip --style-config edited_layers.json
```

### Providing Feedback
If extraction fails for a particular map pattern, the extraction_notes and raw_matches in the JSON provide debugging information. This helps improve the extraction patterns for future versions.

---

## Next Steps (Phase 2)

After Phase 1 is working:

### Priority 1: Style Extraction Improvements
1. **JavaScript AST parsing** - Use a proper JS parser (via subprocess to Node.js or bundled parser) to extract complete MapLibre expressions
2. **Runtime style capture** - Browser extension that calls `map.getStyle()` after page fully loads
3. **User-provided overrides** - Allow users to supply a layer config file that takes precedence over extraction
4. **Validation against tiles** - Parse vector tile schema to validate extracted source-layer names and properties

### Priority 2: Resource Bundling  
1. **Style.json processing** - Rewrite source URLs to local PMTiles paths
2. **Sprite bundling** - Include sprite atlas PNG and JSON
3. **Glyph bundling** - Include required font glyph ranges

### Priority 3: Archive Modes
1. **DATA_ONLY mode** - Skip basemap tiles, reference external
2. **STYLE_ONLY mode** - No tiles, just style recipe
3. **HYBRID mode** - Full data, limited basemap zoom range
4. **Basemap classification** - Detect and handle basemap sources differently

### Priority 4: Coverage Expansion
1. **Tile fetching** - Fetch missing tiles within bounds at ±N zoom levels
2. **Progress reporting** - Show download progress
3. **Resume capability** - Handle interrupted fetches

---

## Questions for Implementation

1. Should the CLI use async for tile building (performance)?
2. How to handle HAR files without tile content (truncated responses)?
3. Should we validate PMTiles output before packaging?
4. Error handling strategy - fail fast or collect warnings?

---

*End of Briefing*