"""
Process capture bundles into archives.

Bridges the capture bundle format to the existing archive creation pipeline.
"""

from pathlib import Path
from dataclasses import dataclass

from .parser import CaptureBundle, CaptureTile, CaptureResource
from ..tiles.coverage import TileCoord, GeoBounds, CoverageCalculator
from ..tiles.detector import TileSource


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

    # URL patterns for source matching (maps source_id to original URL pattern)
    url_patterns: dict[str, str] = None


def process_capture_bundle(bundle: CaptureBundle) -> ProcessedCapture:
    """
    Process a capture bundle into a form suitable for archive creation.

    This bridges the capture bundle format to the existing tile/archive pipeline.
    """
    tiles_by_source: dict[str, list[tuple[TileCoord, bytes]]] = {}
    tile_sources: dict[str, TileSource] = {}
    url_patterns: dict[str, str] = {}

    # Process pre-extracted tiles
    if bundle.tiles:
        esri_tile_count = 0
        for tile in bundle.tiles:
            source_id = tile.source_id

            # ESRI coordinate swap: ESRI uses {z}/{y}/{x} format, need to swap before storing
            coord = tile.coord
            if tile.url and ('arcgisonline.com' in tile.url.lower() or 'arcgis.com' in tile.url.lower()):
                # Swap x and y for ESRI tiles
                coord = TileCoord(z=tile.coord.z, x=tile.coord.y, y=tile.coord.x)
                if esri_tile_count < 3:
                    print(f"[Processor] ESRI tile {esri_tile_count + 1} - swapping coordinates:")
                    print(f"  URL: {tile.url}")
                    print(f"  Original: z={tile.coord.z}, x={tile.coord.x}, y={tile.coord.y}")
                    print(f"  Corrected: z={coord.z}, x={coord.x}, y={coord.y}", flush=True)
                    esri_tile_count += 1

            if source_id not in tiles_by_source:
                tiles_by_source[source_id] = []

                # Get URL template from tile URL if available
                url_template = None
                if tile.url:
                    url_template = _infer_url_template(tile.url)
                    print(f"[Processor] Stored URL pattern for '{source_id}': {url_template}", flush=True)
                else:
                    print(f"[Processor] WARNING: No URL for source '{source_id}', pattern matching will fail", flush=True)

                # Infer format from tile.format, URL, or content
                if hasattr(tile, 'format') and tile.format:
                    format_val = tile.format
                else:
                    format_val = _infer_format(tile.url or "", tile.data)

                tile_sources[source_id] = TileSource(
                    name=source_id,
                    url_template=url_template or f"tiles/{source_id}",
                    tile_type=_infer_tile_type(tile.url or "", tile.data),
                    format=format_val
                )

                # Store URL pattern for source matching
                if url_template:
                    url_patterns[source_id] = url_template

            tiles_by_source[source_id].append((coord, tile.data))

    # If no pre-extracted tiles, extract from HAR
    har_entries = None
    if not bundle.tiles and bundle.har:
        from ..har.parser import HARParser
        from ..har.classifier import RequestClassifier, RequestType
        from ..tiles.detector import TileDetector

        parser = HARParser(None)  # We'll use parse_har_data method
        har_entries = parser.parse_har_data(bundle.har)

        # Classify and detect tiles
        classifier = RequestClassifier()
        grouped = classifier.classify_all(har_entries)

        detector = TileDetector()
        detected = []
        for entry in grouped[RequestType.VECTOR_TILE] + grouped[RequestType.RASTER_TILE]:
            tile = detector.detect(entry.url, entry.content)
            if tile:
                detected.append(tile)

        sources = detector.group_by_source(detected)

        for template, (source, tiles) in sources.items():
            tile_sources[source.name] = source
            tiles_by_source[source.name] = tiles
            # Store URL pattern for source matching
            url_patterns[source.name] = source.url_template

    elif bundle.har:
        # HAR is present but we have tiles - still parse for metadata
        from ..har.parser import HARParser
        parser = HARParser(None)
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
        captured_at=bundle.metadata.captured_at,
        url_patterns=url_patterns
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


def _infer_format(url: str, data: bytes | None = None) -> str:
    """Infer tile format from URL and optionally content."""
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

    # If no extension in URL, check content magic bytes
    if data and len(data) >= 8:
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        if data[:2] == b'\xff\xd8':  # JPEG
            return 'jpeg'
        # WebP starts with 'RIFF' then 'WEBP'
        if data[:4] == b'RIFF' and len(data) >= 12 and data[8:12] == b'WEBP':
            return 'webp'

    return 'pbf'  # Default


def _title_from_url(url: str) -> str:
    """Extract a title from URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc or 'Untitled Map'
