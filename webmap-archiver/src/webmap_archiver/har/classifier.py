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
