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

        # TEMPORARILY REVERT: Test without swap to debug coordinate issue
        coord = TileCoord(z=int(z), x=int(x), y=int(y))

        # Debug logging for ESRI tiles to understand coordinate system
        url_lower = url.lower()
        if 'arcgisonline.com' in url_lower or 'arcgis.com' in url_lower:
            print(f"[TileDetector] ESRI tile captured:")
            print(f"  URL: {url}")
            print(f"  Parsed: z={z}, x={x}, y={y}")
            print(f"  Stored as: {coord}", flush=True)

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
