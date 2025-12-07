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
