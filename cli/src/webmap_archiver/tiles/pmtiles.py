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

from pmtiles.tile import TileType, Compression, zxy_to_tileid
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
    vector_layers: list[dict] | None = None  # TileJSON vector_layers spec


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

        # VALIDATION: Check sample tile content
        sample_coord, sample_data = self.tiles[0]
        print(f"  [PMTiles] Sample tile z{sample_coord.z}/{sample_coord.x}/{sample_coord.y}")
        print(f"    Size: {len(sample_data)} bytes")
        print(f"    First 10 bytes: {sample_data[:10].hex()}")
        print(f"    Is gzipped: {len(sample_data) >= 2 and sample_data[:2] == b'\\x1f\\x8b'}")
        print(f"    Tile type: {tile_type.name}")

        # Open writer
        with open(self.output_path, 'wb') as f:
            writer = Writer(f)

            # Write tiles
            for coord, data in self.tiles:
                # Ensure vector tiles are gzipped
                if tile_type == TileType.MVT:
                    data = self._ensure_gzipped(data)

                tile_id = zxy_to_tileid(coord.z, coord.x, coord.y)
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

            # Add vector_layers for MVT tiles (required by TileJSON spec)
            if self.metadata.vector_layers is not None:
                json_metadata["vector_layers"] = self.metadata.vector_layers

            writer.finalize(header, json_metadata)

    def _ensure_gzipped(self, data: bytes) -> bytes:
        """
        Ensure data is gzipped exactly once.

        CRITICAL: Network-captured tiles may already be gzipped.
        Double-gzipping corrupts the data and prevents tiles from loading.
        """
        # Check if already gzipped (magic bytes: 0x1f 0x8b)
        if len(data) >= 2 and data[:2] == b'\x1f\x8b':
            # Validate it's actually valid gzip by attempting decompression
            try:
                gzip.decompress(data)
                # Valid gzip - return as-is to avoid double compression
                return data
            except Exception:
                # Not valid gzip despite magic bytes - fall through to compress it
                pass

        # Not gzipped - compress it
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6) as gz:
            gz.write(data)
        return buf.getvalue()
