# WebMap Archiver: Phase 2B Implementation Guide

## For Claude Code

This document provides complete instructions for implementing Phase 2B: Capture Bundle Format. Work through the sections in order.

---

## 1. Context

### Project Overview

WebMap Archiver is a tool for preserving web maps as self-contained archives. The system has three major components:

1. **Python CLI & Processing Library** (Phase 1-2A, complete) - Transforms captured data into archives
2. **Browser Extension** (Phase 3, planned) - Captures maps in the browser
3. **Capture Bundle Format** (Phase 2B, this phase) - Interchange format between capture and processing

### Current State

The existing `create` command accepts HAR files:
```bash
webmap-archive create map.har -o archive.zip
```

Phase 2B adds a `process` command that accepts **Capture Bundles** - a richer format that includes:
- Runtime map style (from `map.getStyle()`)
- Viewport state (center, zoom, bounds)
- Page metadata
- Optionally, pre-extracted tile data
- Optionally, the original HAR

### Source Code Location

The source code is in `/home/claude/src/webmap_archiver/` (or can be extracted from the provided zip file). Key files:
- `cli.py` - CLI entry points
- `har/parser.py` - HAR parsing
- `tiles/` - Tile detection, PMTiles building
- `styles/extractor.py` - Style extraction
- `viewer/generator.py` - HTML viewer generation
- `archive/packager.py` - ZIP assembly

---

## 2. Capture Bundle Specification

### File Format

**Extension:** `.webmap-capture.json` or `.webmap.json`
**Encoding:** UTF-8 JSON
**Compression:** Optional gzip (`.webmap-capture.json.gz`)

### Schema (v1.0)

```json
{
  "version": "1.0",
  
  "metadata": {
    "url": "https://example.com/map",
    "title": "Page Title",
    "capturedAt": "2024-01-15T10:30:00Z",
    "userAgent": "Mozilla/5.0...",
    "mapLibrary": {
      "type": "maplibre",
      "version": "4.0.0"
    }
  },
  
  "viewport": {
    "center": [-74.006, 40.7128],
    "zoom": 12,
    "bounds": [[-74.1, 40.6], [-73.9, 40.8]],
    "bearing": 0,
    "pitch": 0
  },
  
  "style": {
    "version": 8,
    "sources": { ... },
    "layers": [ ... ],
    "sprite": "...",
    "glyphs": "..."
  },
  
  "har": {
    "log": {
      "version": "1.2",
      "entries": [ ... ]
    }
  },
  
  "tiles": [
    {
      "sourceId": "basemap",
      "url": "https://api.maptiler.com/tiles/v3/12/1205/1539.pbf",
      "z": 12,
      "x": 1205,
      "y": 1539,
      "data": "base64-encoded-tile-content"
    }
  ],
  
  "resources": {
    "sprites": [
      {
        "url": "https://example.com/sprites/sprite@2x.png",
        "variant": "2x",
        "type": "image",
        "data": "base64-encoded-png"
      },
      {
        "url": "https://example.com/sprites/sprite@2x.json",
        "variant": "2x", 
        "type": "json",
        "data": { ... }
      }
    ],
    "glyphs": [
      {
        "url": "https://example.com/fonts/Open%20Sans%20Regular/0-255.pbf",
        "fontStack": "Open Sans Regular",
        "rangeStart": 0,
        "rangeEnd": 255,
        "data": "base64-encoded-pbf"
      }
    ]
  }
}
```

### Field Requirements

| Field | Required | Description |
|-------|----------|-------------|
| `version` | ✅ | Must be "1.0" |
| `metadata.url` | ✅ | Source page URL |
| `metadata.capturedAt` | ✅ | ISO 8601 timestamp |
| `metadata.title` | ❌ | Page title (can be derived) |
| `viewport` | ✅ | Map viewport state |
| `viewport.center` | ✅ | [lng, lat] |
| `viewport.zoom` | ✅ | Zoom level |
| `viewport.bounds` | ❌ | [[sw_lng, sw_lat], [ne_lng, ne_lat]] |
| `style` | ❌ | MapLibre style object (highly recommended) |
| `har` | ❌ | Full HAR log (if no pre-extracted tiles) |
| `tiles` | ❌ | Pre-extracted tile data |
| `resources` | ❌ | Pre-extracted sprites/glyphs |

**Note:** At least one of `har` or `tiles` must be present. If `tiles` is present, `har` becomes optional.

---

## 3. Streaming Format for Large Captures

For captures with 1000+ tiles, the single-JSON format becomes unwieldy. Support two streaming alternatives:

### Option A: Directory Bundle

```
capture-bundle/
├── manifest.json          # Metadata, viewport, style
├── tiles/
│   ├── basemap/
│   │   ├── 12-1205-1539.pbf
│   │   ├── 12-1205-1540.pbf
│   │   └── ...
│   └── overlay/
│       ├── 12-1205-1539.mvt
│       └── ...
├── resources/
│   ├── sprites/
│   │   ├── sprite@2x.png
│   │   └── sprite@2x.json
│   └── glyphs/
│       └── Open Sans Regular/
│           └── 0-255.pbf
└── har.json               # Optional: full HAR
```

**manifest.json** (same as top-level capture bundle, minus embedded data):
```json
{
  "version": "1.0",
  "format": "directory",
  "metadata": { ... },
  "viewport": { ... },
  "style": { ... },
  "tiles": {
    "basemap": {
      "urlTemplate": "https://api.maptiler.com/tiles/v3/{z}/{x}/{y}.pbf",
      "directory": "tiles/basemap",
      "count": 156
    }
  }
}
```

### Option B: NDJSON Stream

```
capture.webmap.ndjson
```

Each line is a separate JSON object:

```jsonl
{"type": "header", "version": "1.0", "metadata": {...}, "viewport": {...}}
{"type": "style", "style": {...}}
{"type": "tile", "sourceId": "basemap", "z": 12, "x": 1205, "y": 1539, "data": "..."}
{"type": "tile", "sourceId": "basemap", "z": 12, "x": 1205, "y": 1540, "data": "..."}
{"type": "resource", "resourceType": "sprite", "variant": "2x", "data": "..."}
{"type": "footer", "tileCount": 156, "checksum": "sha256:..."}
```

---

## 4. Implementation Tasks

### Task 1: Create Capture Bundle Parser Module

Create `webmap_archiver/capture/parser.py`:

```python
"""
Capture bundle parsing and validation.

Supports three input formats:
1. Single JSON file (.webmap.json, .webmap-capture.json)
2. Gzipped JSON (.webmap.json.gz)
3. Directory bundle (folder with manifest.json)
4. NDJSON stream (.webmap.ndjson)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
import json
import gzip

from ..tiles.coverage import TileCoord


@dataclass
class CaptureMetadata:
    """Metadata about the capture."""
    url: str
    captured_at: str  # ISO 8601
    title: str | None = None
    user_agent: str | None = None
    map_library_type: str | None = None  # maplibre, mapbox, leaflet
    map_library_version: str | None = None


@dataclass
class CaptureViewport:
    """Map viewport state at capture time."""
    center: tuple[float, float]  # (lng, lat)
    zoom: float
    bounds: tuple[tuple[float, float], tuple[float, float]] | None = None  # ((sw_lng, sw_lat), (ne_lng, ne_lat))
    bearing: float = 0.0
    pitch: float = 0.0


@dataclass  
class CaptureTile:
    """A single captured tile."""
    source_id: str
    coord: TileCoord
    url: str
    data: bytes


@dataclass
class CaptureResource:
    """A captured resource (sprite, glyph)."""
    resource_type: str  # "sprite" or "glyph"
    url: str
    data: bytes
    # For sprites
    variant: str | None = None  # "1x" or "2x"
    content_type: str | None = None  # "image" or "json"
    # For glyphs
    font_stack: str | None = None
    range_start: int | None = None
    range_end: int | None = None


@dataclass
class CaptureBundle:
    """Complete capture bundle."""
    version: str
    metadata: CaptureMetadata
    viewport: CaptureViewport
    style: dict | None = None
    har: dict | None = None
    tiles: list[CaptureTile] = field(default_factory=list)
    resources: list[CaptureResource] = field(default_factory=list)


class CaptureValidationError(Exception):
    """Raised when capture bundle validation fails."""
    pass


class CaptureParser:
    """Parse capture bundles from various formats."""
    
    def parse(self, path: Path) -> CaptureBundle:
        """
        Parse a capture bundle from file or directory.
        
        Automatically detects format based on path.
        """
        if path.is_dir():
            return self._parse_directory(path)
        elif path.suffix == '.ndjson':
            return self._parse_ndjson(path)
        elif path.suffix == '.gz':
            return self._parse_gzip(path)
        else:
            return self._parse_json(path)
    
    def _parse_json(self, path: Path) -> CaptureBundle:
        """Parse single JSON file."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return self._build_bundle(data)
    
    def _parse_gzip(self, path: Path) -> CaptureBundle:
        """Parse gzipped JSON file."""
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            data = json.load(f)
        return self._build_bundle(data)
    
    def _parse_directory(self, path: Path) -> CaptureBundle:
        """Parse directory bundle."""
        manifest_path = path / 'manifest.json'
        if not manifest_path.exists():
            raise CaptureValidationError(f"No manifest.json in {path}")
        
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        
        bundle = self._build_bundle(manifest, has_embedded_data=False)
        
        # Load tiles from directory
        tiles_dir = path / 'tiles'
        if tiles_dir.exists():
            bundle.tiles = list(self._load_tiles_from_directory(tiles_dir, manifest.get('tiles', {})))
        
        # Load resources from directory
        resources_dir = path / 'resources'
        if resources_dir.exists():
            bundle.resources = list(self._load_resources_from_directory(resources_dir))
        
        # Load HAR if present
        har_path = path / 'har.json'
        if har_path.exists():
            with open(har_path, 'r', encoding='utf-8') as f:
                bundle.har = json.load(f)
        
        return bundle
    
    def _parse_ndjson(self, path: Path) -> CaptureBundle:
        """Parse NDJSON stream file."""
        bundle = None
        tiles = []
        resources = []
        
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                    
                obj = json.loads(line)
                obj_type = obj.get('type')
                
                if obj_type == 'header':
                    bundle = CaptureBundle(
                        version=obj['version'],
                        metadata=self._parse_metadata(obj['metadata']),
                        viewport=self._parse_viewport(obj['viewport'])
                    )
                elif obj_type == 'style':
                    if bundle:
                        bundle.style = obj['style']
                elif obj_type == 'tile':
                    tiles.append(self._parse_tile(obj))
                elif obj_type == 'resource':
                    resources.append(self._parse_resource(obj))
        
        if bundle is None:
            raise CaptureValidationError("No header found in NDJSON")
        
        bundle.tiles = tiles
        bundle.resources = resources
        return bundle
    
    def _build_bundle(self, data: dict, has_embedded_data: bool = True) -> CaptureBundle:
        """Build CaptureBundle from parsed JSON."""
        # Validate version
        version = data.get('version')
        if version != '1.0':
            raise CaptureValidationError(f"Unsupported version: {version}")
        
        # Parse required fields
        if 'metadata' not in data:
            raise CaptureValidationError("Missing required field: metadata")
        if 'viewport' not in data:
            raise CaptureValidationError("Missing required field: viewport")
        
        metadata = self._parse_metadata(data['metadata'])
        viewport = self._parse_viewport(data['viewport'])
        
        bundle = CaptureBundle(
            version=version,
            metadata=metadata,
            viewport=viewport,
            style=data.get('style'),
            har=data.get('har')
        )
        
        # Parse tiles if embedded
        if has_embedded_data and 'tiles' in data:
            bundle.tiles = [self._parse_tile(t) for t in data['tiles']]
        
        # Parse resources if embedded
        if has_embedded_data and 'resources' in data:
            resources_data = data['resources']
            if 'sprites' in resources_data:
                for s in resources_data['sprites']:
                    bundle.resources.append(self._parse_sprite_resource(s))
            if 'glyphs' in resources_data:
                for g in resources_data['glyphs']:
                    bundle.resources.append(self._parse_glyph_resource(g))
        
        return bundle
    
    def _parse_metadata(self, data: dict) -> CaptureMetadata:
        """Parse metadata section."""
        if 'url' not in data:
            raise CaptureValidationError("Missing required field: metadata.url")
        if 'capturedAt' not in data:
            raise CaptureValidationError("Missing required field: metadata.capturedAt")
        
        map_lib = data.get('mapLibrary', {})
        
        return CaptureMetadata(
            url=data['url'],
            captured_at=data['capturedAt'],
            title=data.get('title'),
            user_agent=data.get('userAgent'),
            map_library_type=map_lib.get('type'),
            map_library_version=map_lib.get('version')
        )
    
    def _parse_viewport(self, data: dict) -> CaptureViewport:
        """Parse viewport section."""
        if 'center' not in data:
            raise CaptureValidationError("Missing required field: viewport.center")
        if 'zoom' not in data:
            raise CaptureValidationError("Missing required field: viewport.zoom")
        
        center = tuple(data['center'])
        bounds = None
        if 'bounds' in data:
            b = data['bounds']
            bounds = ((b[0][0], b[0][1]), (b[1][0], b[1][1]))
        
        return CaptureViewport(
            center=center,
            zoom=data['zoom'],
            bounds=bounds,
            bearing=data.get('bearing', 0.0),
            pitch=data.get('pitch', 0.0)
        )
    
    def _parse_tile(self, data: dict) -> CaptureTile:
        """Parse a tile entry."""
        import base64
        
        return CaptureTile(
            source_id=data.get('sourceId', 'unknown'),
            coord=TileCoord(data['z'], data['x'], data['y']),
            url=data.get('url', ''),
            data=base64.b64decode(data['data']) if isinstance(data['data'], str) else data['data']
        )
    
    def _parse_sprite_resource(self, data: dict) -> CaptureResource:
        """Parse a sprite resource entry."""
        import base64
        
        raw_data = data.get('data', '')
        if data.get('type') == 'json' and isinstance(raw_data, dict):
            decoded = json.dumps(raw_data).encode('utf-8')
        elif isinstance(raw_data, str):
            decoded = base64.b64decode(raw_data)
        else:
            decoded = raw_data
        
        return CaptureResource(
            resource_type='sprite',
            url=data.get('url', ''),
            data=decoded,
            variant=data.get('variant'),
            content_type=data.get('type')
        )
    
    def _parse_glyph_resource(self, data: dict) -> CaptureResource:
        """Parse a glyph resource entry."""
        import base64
        
        return CaptureResource(
            resource_type='glyph',
            url=data.get('url', ''),
            data=base64.b64decode(data['data']),
            font_stack=data.get('fontStack'),
            range_start=data.get('rangeStart'),
            range_end=data.get('rangeEnd')
        )
    
    def _load_tiles_from_directory(
        self, 
        tiles_dir: Path, 
        tiles_manifest: dict
    ) -> Iterator[CaptureTile]:
        """Load tiles from directory structure."""
        for source_id, source_info in tiles_manifest.items():
            source_dir = tiles_dir / source_info.get('directory', source_id)
            if not source_dir.exists():
                continue
            
            url_template = source_info.get('urlTemplate', '')
            
            for tile_file in source_dir.glob('*'):
                if tile_file.is_file():
                    # Parse filename: z-x-y.ext
                    parts = tile_file.stem.split('-')
                    if len(parts) == 3:
                        try:
                            z, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                            yield CaptureTile(
                                source_id=source_id,
                                coord=TileCoord(z, x, y),
                                url=url_template.format(z=z, x=x, y=y),
                                data=tile_file.read_bytes()
                            )
                        except ValueError:
                            continue
    
    def _load_resources_from_directory(self, resources_dir: Path) -> Iterator[CaptureResource]:
        """Load resources from directory structure."""
        # Load sprites
        sprites_dir = resources_dir / 'sprites'
        if sprites_dir.exists():
            for sprite_file in sprites_dir.glob('*'):
                if sprite_file.is_file():
                    variant = '2x' if '@2x' in sprite_file.name else '1x'
                    content_type = 'json' if sprite_file.suffix == '.json' else 'image'
                    yield CaptureResource(
                        resource_type='sprite',
                        url='',
                        data=sprite_file.read_bytes(),
                        variant=variant,
                        content_type=content_type
                    )
        
        # Load glyphs
        glyphs_dir = resources_dir / 'glyphs'
        if glyphs_dir.exists():
            for font_dir in glyphs_dir.iterdir():
                if font_dir.is_dir():
                    font_stack = font_dir.name
                    for glyph_file in font_dir.glob('*.pbf'):
                        # Parse range from filename: 0-255.pbf
                        parts = glyph_file.stem.split('-')
                        if len(parts) == 2:
                            try:
                                range_start, range_end = int(parts[0]), int(parts[1])
                                yield CaptureResource(
                                    resource_type='glyph',
                                    url='',
                                    data=glyph_file.read_bytes(),
                                    font_stack=font_stack,
                                    range_start=range_start,
                                    range_end=range_end
                                )
                            except ValueError:
                                continue


def validate_capture_bundle(bundle: CaptureBundle) -> list[str]:
    """
    Validate a capture bundle and return list of warnings.
    
    Raises CaptureValidationError for fatal issues.
    Returns list of warning strings for non-fatal issues.
    """
    warnings = []
    
    # Must have either HAR or tiles
    if not bundle.har and not bundle.tiles:
        raise CaptureValidationError(
            "Capture bundle must contain either 'har' or 'tiles' data"
        )
    
    # Warn if no style
    if not bundle.style:
        warnings.append("No style included - will attempt to extract from HAR")
    
    # Warn if no bounds in viewport
    if not bundle.viewport.bounds:
        warnings.append("No bounds in viewport - will calculate from tiles")
    
    # Validate tile coordinates
    for tile in bundle.tiles:
        if tile.coord.z < 0 or tile.coord.z > 22:
            warnings.append(f"Unusual zoom level: {tile.coord.z}")
            break
    
    return warnings
```

### Task 2: Create `__init__.py` for capture module

Create `webmap_archiver/capture/__init__.py`:

```python
"""Capture bundle parsing and validation."""

from .parser import (
    CaptureParser,
    CaptureBundle,
    CaptureMetadata,
    CaptureViewport,
    CaptureTile,
    CaptureResource,
    CaptureValidationError,
    validate_capture_bundle,
)

__all__ = [
    'CaptureParser',
    'CaptureBundle',
    'CaptureMetadata',
    'CaptureViewport', 
    'CaptureTile',
    'CaptureResource',
    'CaptureValidationError',
    'validate_capture_bundle',
]
```

### Task 3: Create Capture Bundle Processor

Create `webmap_archiver/capture/processor.py`:

```python
"""
Process capture bundles into archives.

Bridges the capture bundle format to the existing archive creation pipeline.
"""

from pathlib import Path
from dataclasses import dataclass
import tempfile

from .parser import CaptureBundle, CaptureTile, CaptureResource
from ..tiles.coverage import TileCoord, GeoBounds, CoverageCalculator
from ..tiles.detector import TileSource
from ..har.parser import HARParser


@dataclass
class ProcessedCapture:
    """Intermediate representation for archive building."""
    # Tile data grouped by source
    tile_sources: dict[str, TileSource]
    tiles_by_source: dict[str, list[tuple[TileCoord, bytes]]]
    
    # Style (from bundle or extracted from HAR)
    style: dict | None
    
    # Bounds (from viewport or calculated)
    bounds: GeoBounds
    
    # Resources
    sprites: list[CaptureResource]
    glyphs: list[CaptureResource]
    
    # Original HAR entries (if available)
    har_entries: list | None
    
    # Metadata
    source_url: str
    title: str
    captured_at: str


def process_capture_bundle(bundle: CaptureBundle) -> ProcessedCapture:
    """
    Process a capture bundle into a form suitable for archive creation.
    
    This bridges the capture bundle format to the existing tile/archive pipeline.
    """
    tiles_by_source: dict[str, list[tuple[TileCoord, bytes]]] = {}
    tile_sources: dict[str, TileSource] = {}
    
    # Process pre-extracted tiles
    if bundle.tiles:
        for tile in bundle.tiles:
            source_id = tile.source_id
            
            if source_id not in tiles_by_source:
                tiles_by_source[source_id] = []
                # Create TileSource from first tile
                tile_sources[source_id] = TileSource(
                    name=source_id,
                    url_template=_infer_url_template(tile.url),
                    tile_type=_infer_tile_type(tile.url, tile.data),
                    format=_infer_format(tile.url)
                )
            
            tiles_by_source[source_id].append((tile.coord, tile.data))
    
    # If no pre-extracted tiles, extract from HAR
    har_entries = None
    if not bundle.tiles and bundle.har:
        from ..har.parser import HARParser
        from ..tiles.detector import TileDetector
        
        parser = HARParser()
        har_entries = parser.parse_har_data(bundle.har)
        
        detector = TileDetector()
        detected_sources = detector.detect_tiles(har_entries)
        
        for source in detected_sources:
            tile_sources[source.name] = source
            tiles_by_source[source.name] = [
                (TileCoord(t['z'], t['x'], t['y']), t['content'])
                for t in source.tiles
            ]
    elif bundle.har:
        # HAR is present but we have tiles - still parse for other resources
        from ..har.parser import HARParser
        parser = HARParser()
        har_entries = parser.parse_har_data(bundle.har)
    
    # Determine bounds
    if bundle.viewport.bounds:
        sw, ne = bundle.viewport.bounds
        bounds = GeoBounds(
            west=sw[0],
            south=sw[1],
            east=ne[0],
            north=ne[1]
        )
    else:
        # Calculate from tiles
        all_coords = []
        for coords_list in tiles_by_source.values():
            all_coords.extend([c for c, _ in coords_list])
        
        if all_coords:
            calc = CoverageCalculator()
            bounds = calc.calculate_bounds(all_coords)
        else:
            # Fallback to viewport center
            lng, lat = bundle.viewport.center
            bounds = GeoBounds(
                west=lng - 0.1,
                south=lat - 0.1,
                east=lng + 0.1,
                north=lat + 0.1
            )
    
    # Separate resources by type
    sprites = [r for r in bundle.resources if r.resource_type == 'sprite']
    glyphs = [r for r in bundle.resources if r.resource_type == 'glyph']
    
    return ProcessedCapture(
        tile_sources=tile_sources,
        tiles_by_source=tiles_by_source,
        style=bundle.style,
        bounds=bounds,
        sprites=sprites,
        glyphs=glyphs,
        har_entries=har_entries,
        source_url=bundle.metadata.url,
        title=bundle.metadata.title or _title_from_url(bundle.metadata.url),
        captured_at=bundle.metadata.captured_at
    )


def _infer_url_template(url: str) -> str:
    """Infer URL template from a concrete tile URL."""
    import re
    # Replace coordinate patterns with placeholders
    # Match patterns like /12/1205/1539 or /12/1205/1539.pbf
    pattern = r'/(\d+)/(\d+)/(\d+)'
    match = re.search(pattern, url)
    if match:
        return url[:match.start()] + '/{z}/{x}/{y}' + url[match.end():]
    return url


def _infer_tile_type(url: str, data: bytes) -> str:
    """Infer tile type from URL and content."""
    url_lower = url.lower()
    
    # Check URL extensions
    if '.pbf' in url_lower or '.mvt' in url_lower:
        return 'vector'
    if '.png' in url_lower or '.jpg' in url_lower or '.jpeg' in url_lower or '.webp' in url_lower:
        return 'raster'
    
    # Check content magic bytes
    if data:
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return 'raster'
        if data[:2] == b'\xff\xd8':  # JPEG
            return 'raster'
        # Assume vector for gzipped/protobuf content
        if data[:2] == b'\x1f\x8b':  # gzip
            return 'vector'
    
    return 'vector'  # Default to vector


def _infer_format(url: str) -> str:
    """Infer tile format from URL."""
    url_lower = url.lower()
    if '.png' in url_lower:
        return 'png'
    if '.jpg' in url_lower or '.jpeg' in url_lower:
        return 'jpeg'
    if '.webp' in url_lower:
        return 'webp'
    if '.pbf' in url_lower:
        return 'pbf'
    if '.mvt' in url_lower:
        return 'mvt'
    return 'pbf'


def _title_from_url(url: str) -> str:
    """Extract a title from URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc or 'Untitled Map'
```

### Task 4: Add CLI `process` Command

Update `cli.py` to add the `process` command. Add this after the existing `create` command:

```python
@main.command()
@click.argument('capture_file', type=click.Path(exists=True, path_type=Path))
@click.option('-o', '--output', type=click.Path(path_type=Path), help='Output ZIP path')
@click.option('-n', '--name', help='Archive name (default: derived from capture)')
@click.option('-v', '--verbose', is_flag=True, help='Verbose output')
@click.option('--mode', type=click.Choice(['standalone', 'original', 'full']), default='full',
              help='Archive mode: standalone (viewer only), original (site + serve.py), full (both)')
def process(capture_file: Path, output: Path | None, name: str | None, 
            verbose: bool, mode: str):
    """Process a capture bundle into an archive.
    
    Accepts capture bundles in various formats:
    
    \b
    - Single JSON file (.webmap.json, .webmap-capture.json)
    - Gzipped JSON (.webmap.json.gz)
    - Directory bundle (folder with manifest.json)
    - NDJSON stream (.webmap.ndjson)
    
    The capture bundle format is the interchange format between the browser
    extension and this processing tool. It can contain pre-extracted tiles
    and style data, or a HAR log to extract from.
    """
    from .capture.parser import CaptureParser, validate_capture_bundle, CaptureValidationError
    from .capture.processor import process_capture_bundle
    
    archive_mode = ArchiveMode(mode)
    
    # Determine output path
    if output is None:
        if capture_file.is_dir():
            output = capture_file.with_suffix('.zip')
        else:
            # Remove .webmap.json, .webmap-capture.json, etc.
            stem = capture_file.stem
            for suffix in ['.webmap', '.webmap-capture']:
                if stem.endswith(suffix):
                    stem = stem[:-len(suffix)]
                    break
            output = capture_file.parent / f"{stem}.zip"
    
    console.print(f"Processing capture: {capture_file}")
    console.print(f"Output: {output}")
    console.print(f"Mode: {mode}")
    console.print()
    
    # Parse capture bundle
    with console.status("Parsing capture bundle..."):
        try:
            parser = CaptureParser()
            bundle = parser.parse(capture_file)
        except CaptureValidationError as e:
            console.print(f"[red]✗ Invalid capture bundle: {e}[/]")
            raise click.Abort()
    
    # Validate and show warnings
    try:
        warnings = validate_capture_bundle(bundle)
        for warning in warnings:
            console.print(f"  [yellow]⚠ {warning}[/]")
    except CaptureValidationError as e:
        console.print(f"[red]✗ Validation failed: {e}[/]")
        raise click.Abort()
    
    # Show capture info
    console.print(f"  Version: {bundle.version}")
    console.print(f"  Source: {bundle.metadata.url}")
    console.print(f"  Captured: {bundle.metadata.captured_at}")
    if bundle.metadata.map_library_type:
        console.print(f"  Map library: {bundle.metadata.map_library_type} {bundle.metadata.map_library_version or ''}")
    console.print(f"  Viewport: {bundle.viewport.center} @ z{bundle.viewport.zoom}")
    console.print(f"  Tiles: {len(bundle.tiles)}")
    console.print(f"  Resources: {len(bundle.resources)}")
    console.print(f"  Has style: {'✓' if bundle.style else '✗'}")
    console.print(f"  Has HAR: {'✓' if bundle.har else '✗'}")
    console.print()
    
    # Process into intermediate form
    with console.status("Processing capture data..."):
        processed = process_capture_bundle(bundle)
    
    console.print(f"  Tile sources: {len(processed.tile_sources)}")
    for source_name, tiles in processed.tiles_by_source.items():
        console.print(f"    • {source_name}: {len(tiles)} tiles")
    console.print()
    
    # From here, use the same pipeline as `create` command
    # This is where we'd call into the existing archive creation logic
    # ... (integrate with existing create command logic)
    
    # For now, show what would be created
    console.print("[green]✓ Capture bundle processed successfully[/]")
    console.print()
    console.print("Archive would contain:")
    console.print(f"  • {len(processed.tile_sources)} PMTiles archives")
    if archive_mode in (ArchiveMode.STANDALONE, ArchiveMode.FULL):
        console.print("  • viewer.html (standalone viewer)")
    if archive_mode in (ArchiveMode.ORIGINAL, ArchiveMode.FULL):
        console.print("  • serve.py (local server)")
        console.print("  • original/ (site assets)")
    console.print(f"  • {len(processed.sprites)} sprite files")
    console.print(f"  • {len(processed.glyphs)} glyph files")
```

### Task 5: Integrate with Existing Archive Pipeline

The `process` command should reuse the existing archive creation logic. Refactor `cli.py` to extract the common archive-building logic into a shared function that both `create` and `process` can use.

Create a helper function in `cli.py`:

```python
def build_archive(
    tile_sources: dict[str, any],  # TileSource objects
    tiles_by_source: dict[str, list[tuple[TileCoord, bytes]]],
    style: dict | None,
    bounds: GeoBounds,
    har_entries: list | None,
    source_url: str,
    title: str,
    output_path: Path,
    archive_mode: ArchiveMode,
    verbose: bool = False,
    expand_coverage: bool = False,
    expand_zoom: int = 0,
    rate_limit: float = 10.0
) -> None:
    """
    Build an archive from processed capture data.
    
    Shared between `create` (from HAR) and `process` (from capture bundle).
    """
    # ... implementation that extracts the common logic from `create`
```

---

## 5. Test Cases

### Test 1: Parse JSON Capture Bundle

Create a test file `test_capture.webmap.json`:

```json
{
  "version": "1.0",
  "metadata": {
    "url": "https://example.com/map",
    "title": "Test Map",
    "capturedAt": "2024-01-15T10:30:00Z"
  },
  "viewport": {
    "center": [-74.006, 40.7128],
    "zoom": 12
  },
  "tiles": [
    {
      "sourceId": "basemap",
      "url": "https://tiles.example.com/v1/12/1205/1539.pbf",
      "z": 12,
      "x": 1205,
      "y": 1539,
      "data": "H4sIAAAAAAAAA6tWKkktLlGyUlAqS8wpTtVRSs7PS0nNBQBSGrPcFgAAAA=="
    }
  ]
}
```

Test:
```bash
webmap-archive process test_capture.webmap.json -v
```

### Test 2: Parse Directory Bundle

Create directory structure:
```
test-bundle/
├── manifest.json
└── tiles/
    └── basemap/
        └── 12-1205-1539.pbf
```

Test:
```bash
webmap-archive process test-bundle/ -v
```

### Test 3: Validate Error Handling

Test with invalid bundle (missing required fields):
```json
{
  "version": "1.0",
  "metadata": {
    "url": "https://example.com"
  }
}
```

Should fail with: "Missing required field: metadata.capturedAt"

### Test 4: HAR Fallback

Test with bundle that has HAR but no pre-extracted tiles:
```json
{
  "version": "1.0",
  "metadata": {
    "url": "https://parkingregulations.nyc/",
    "capturedAt": "2024-01-15T10:30:00Z"
  },
  "viewport": {
    "center": [-74.006, 40.7128],
    "zoom": 12
  },
  "har": {
    "log": { ... }
  }
}
```

---

## 6. Edge Cases to Handle

1. **Empty tiles array** - Should fall back to HAR extraction
2. **Unknown tile source IDs** - Generate sensible defaults
3. **Missing viewport bounds** - Calculate from tile coverage
4. **Malformed base64 data** - Fail gracefully with clear error
5. **Very large bundles** - Stream processing, don't load all into memory
6. **Duplicate tiles** - Deduplicate by (source, z, x, y)
7. **Mixed tile formats** - Handle both .pbf and .mvt in same bundle
8. **Unicode in font stack names** - URL decode properly
9. **Gzipped JSON with BOM** - Handle UTF-8 BOM

---

## 7. File Structure After Implementation

```
webmap_archiver/
├── __init__.py
├── cli.py                    # Updated with `process` command
├── capture/                  # NEW
│   ├── __init__.py
│   ├── parser.py             # Capture bundle parsing
│   └── processor.py          # Bundle → archive processing
├── har/
│   ├── __init__.py
│   ├── parser.py
│   └── classifier.py
├── tiles/
│   ├── __init__.py
│   ├── detector.py
│   ├── coverage.py
│   ├── pmtiles.py
│   ├── layer_inspector.py
│   └── fetcher.py
├── styles/
│   ├── __init__.py
│   └── extractor.py
├── viewer/
│   ├── __init__.py
│   └── generator.py
├── archive/
│   ├── __init__.py
│   └── packager.py
├── site/
│   ├── __init__.py
│   └── extractor.py
├── resources/
│   ├── __init__.py
│   └── bundler.py
└── templates/
    ├── __init__.py
    └── serve.py
```

---

## 8. Success Criteria

Phase 2B is complete when:

1. ✅ `webmap-archive process <capture.json>` works with all format variants
2. ✅ Validation produces clear error messages for invalid bundles
3. ✅ Pre-extracted tiles are used when available
4. ✅ Falls back to HAR extraction when tiles not pre-extracted
5. ✅ Style from bundle is used when available
6. ✅ All existing `--mode` options work with `process` command
7. ✅ Directory bundles with 1000+ tiles don't cause memory issues
8. ✅ Tests pass for all edge cases listed above

---

## 9. Notes for Implementation

- **Reuse existing code**: The tile detection, PMTiles building, viewer generation, and archive packaging code already exists. The capture module bridges the new format to these existing components.

- **Don't break `create`**: The HAR-based workflow must continue to work. The `process` command is additive.

- **Type hints**: Use Python 3.10+ type hints throughout. The existing codebase uses them.

- **Error messages**: Make validation errors specific and actionable. Tell the user exactly what's wrong and how to fix it.

- **Progress reporting**: Use `rich` for progress bars and status messages, consistent with existing CLI.

---

## 10. Dependencies

No new dependencies required. The implementation uses:
- `click` (CLI)
- `rich` (progress/output)
- Standard library (`json`, `gzip`, `pathlib`, `dataclasses`)

All already installed in the project.