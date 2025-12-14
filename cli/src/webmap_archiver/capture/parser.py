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

from ..tiles.detector import TileCoord


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
            # Handle both flat array (new format) and nested object (legacy format)
            if isinstance(resources_data, list):
                for r in resources_data:
                    bundle.resources.append(self._parse_resource(r))
            elif isinstance(resources_data, dict):
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

    def _parse_resource(self, data: dict) -> CaptureResource:
        """Parse a resource from NDJSON."""
        resource_type = data.get('resourceType')
        if resource_type == 'sprite':
            return self._parse_sprite_resource(data)
        elif resource_type == 'glyph':
            return self._parse_glyph_resource(data)
        else:
            raise CaptureValidationError(f"Unknown resource type: {resource_type}")

    def _parse_sprite_resource(self, data: dict) -> CaptureResource:
        """Parse a sprite resource entry."""
        import base64

        raw_data = data.get('data', '')
        content_type = data.get('contentType') or data.get('type')  # Support both field names

        if content_type == 'json' and isinstance(raw_data, dict):
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
            content_type=content_type
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
