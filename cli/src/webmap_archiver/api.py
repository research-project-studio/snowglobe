"""
Public API for WebMap Archiver.

This is the primary interface for programmatic use. Modal, Puppeteer scripts,
CLI commands, and other tools should use these functions rather than
importing internal modules directly.

Example usage:
    from webmap_archiver.api import create_archive_from_bundle

    result = create_archive_from_bundle(
        bundle=capture_bundle_dict,
        output_path=Path("output.zip"),
    )
    print(f"Created archive with {result.tile_count} tiles")
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tempfile
import json

from .capture.parser import CaptureParser, CaptureValidationError
from .capture.processor import process_capture_bundle
from .tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
from .tiles.coverage import CoverageCalculator, GeoBounds
from .tiles.layer_inspector import discover_layers_from_tiles, extract_layer_names_protobuf
from .viewer.generator import ViewerGenerator, ViewerConfig
from .archive.packager import ArchivePackager, TileSourceInfo


# ============================================================================
# Public Data Classes
# ============================================================================


@dataclass
class TileSourceResult:
    """Information about a tile source in the archive."""

    name: str
    tile_count: int
    zoom_range: tuple[int, int]
    tile_type: str  # "vector" or "raster"
    format: str  # "pbf", "png", etc.
    discovered_layers: list[str]  # Source layers found in tiles


@dataclass
class ArchiveResult:
    """Result of archive creation."""

    output_path: Path
    size: int
    tile_count: int
    tile_sources: list[TileSourceResult]
    zoom_range: tuple[int, int]
    bounds: dict  # {west, south, east, north}
    viewer_included: bool = True
    manifest_included: bool = True


@dataclass
class InspectionResult:
    """Result of inspecting a capture bundle."""

    is_valid: bool
    version: str | None
    url: str | None
    title: str | None
    tile_count: int
    tile_sources: list[str]
    has_style: bool
    has_har: bool
    has_viewport: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# Bundle Normalization
# ============================================================================


def normalize_bundle(bundle: dict) -> dict:
    """
    Normalize a capture bundle to handle field name variations.

    The browser extension may send slightly different field names than
    what the parser expects. This function normalizes them.

    Args:
        bundle: Raw capture bundle dict

    Returns:
        Normalized bundle dict (modified in place and returned)
    """
    # Handle 'source' vs 'sourceId' in tiles
    if "tiles" in bundle:
        for tile in bundle["tiles"]:
            if "source" in tile and "sourceId" not in tile:
                tile["sourceId"] = tile.pop("source")

    # Ensure metadata.url exists (some bundles may have it missing)
    if "metadata" in bundle:
        if "url" not in bundle["metadata"] or not bundle["metadata"]["url"]:
            bundle["metadata"]["url"] = "https://unknown"

    return bundle


# ============================================================================
# Main Public API Functions
# ============================================================================


def create_archive_from_bundle(
    bundle: dict,
    output_path: Path,
    *,
    name: str | None = None,
    mode: str = "standalone",
    verbose: bool = False,
) -> ArchiveResult:
    """
    Create an archive from a capture bundle.

    This is the main entry point for the browser extension workflow.
    It handles all steps: parsing, processing, layer discovery, and packaging.

    Args:
        bundle: Capture bundle dict (from browser extension or file)
        output_path: Where to write the ZIP archive
        name: Optional archive name (defaults to page title or URL)
        mode: Archive mode - "standalone" (viewer only), "original" (site files),
              or "full" (both)
        verbose: If True, print progress information

    Returns:
        ArchiveResult with metadata about the created archive

    Raises:
        CaptureValidationError: If the bundle is invalid
        ValueError: If required data is missing
    """
    output_path = Path(output_path)

    # Step 1: Normalize bundle
    if verbose:
        print("Normalizing bundle...")
    bundle = normalize_bundle(bundle)

    # Step 2: Parse and validate
    if verbose:
        print("Parsing capture bundle...")
    parser = CaptureParser()
    capture = parser._build_bundle(bundle)

    # Step 3: Process into intermediate form
    if verbose:
        print("Processing capture...")
    processed = process_capture_bundle(capture)
    print(f"[DIAG] processed.url_patterns: {processed.url_patterns}", flush=True)

    # Step 4: Build archive with layer discovery
    if verbose:
        print("Building archive...")

    result = _build_archive(
        processed=processed,
        capture=capture,
        output_path=output_path,
        name=name,
        mode=mode,
        verbose=verbose,
    )

    return result


def create_archive_from_har(
    har_path: Path,
    output_path: Path,
    *,
    name: str | None = None,
    mode: str = "standalone",
    style_override: dict | None = None,
    verbose: bool = False,
) -> ArchiveResult:
    """
    Create an archive from a HAR file.

    This is the main entry point for the CLI workflow.

    Args:
        har_path: Path to HAR file
        output_path: Where to write the ZIP archive
        name: Optional archive name
        mode: Archive mode
        style_override: Optional style dict from map.getStyle()
        verbose: If True, print progress information

    Returns:
        ArchiveResult with metadata about the created archive
    """
    from .har.parser import HARParser

    har_path = Path(har_path)
    output_path = Path(output_path)

    if verbose:
        print(f"Parsing HAR file: {har_path}")

    # Parse HAR
    har_parser = HARParser()
    entries = har_parser.parse(har_path)

    # Build a capture bundle from HAR
    # (This reuses the same code path as the extension)
    bundle = {
        "version": "1.0",
        "metadata": {
            "url": _extract_url_from_har(entries),
            "capturedAt": _extract_timestamp_from_har(entries),
            "title": name,
        },
        "viewport": {
            "center": [0, 0],
            "zoom": 10,
        },
        "style": style_override,
        "har": {"log": {"version": "1.2", "entries": _entries_to_har_format(entries)}},
    }

    return create_archive_from_bundle(
        bundle=bundle,
        output_path=output_path,
        name=name,
        mode=mode,
        verbose=verbose,
    )


def inspect_bundle(bundle: dict) -> InspectionResult:
    """
    Inspect a capture bundle without creating an archive.

    Useful for validation and debugging.

    Args:
        bundle: Capture bundle dict

    Returns:
        InspectionResult with validation info
    """
    errors = []
    warnings = []

    # Check version
    version = bundle.get("version")
    if version != "1.0":
        errors.append(f"Unsupported or missing version: {version}")

    # Check metadata
    metadata = bundle.get("metadata", {})
    url = metadata.get("url")
    title = metadata.get("title")

    if not url:
        errors.append("Missing metadata.url")

    # Check viewport
    viewport = bundle.get("viewport", {})
    has_viewport = "center" in viewport and "zoom" in viewport
    if not has_viewport:
        errors.append("Missing viewport.center or viewport.zoom")

    # Check tiles
    tiles = bundle.get("tiles", [])
    tile_count = len(tiles)

    # Get unique sources
    tile_sources = list(set(t.get("sourceId") or t.get("source") or "unknown" for t in tiles))

    # Check for source field name issues
    if tiles and "source" in tiles[0] and "sourceId" not in tiles[0]:
        warnings.append("Tiles use 'source' field instead of 'sourceId' - will be normalized")

    # Check style and HAR
    has_style = bundle.get("style") is not None
    has_har = bundle.get("har") is not None

    if not has_style and not has_har and tile_count == 0:
        warnings.append("Bundle has no style, HAR, or tiles - archive will be empty")

    return InspectionResult(
        is_valid=len(errors) == 0,
        version=version,
        url=url,
        title=title,
        tile_count=tile_count,
        tile_sources=tile_sources,
        has_style=has_style,
        has_har=has_har,
        has_viewport=has_viewport,
        errors=errors,
        warnings=warnings,
    )


# ============================================================================
# Internal Implementation
# ============================================================================


def _rewrite_style_sources(style: dict, tile_source_infos: list) -> dict:
    """
    Rewrite source URLs in captured style to point to local PMTiles files.

    Takes a MapLibre/Mapbox style object and rewrites the 'url' field in each
    source to use the pmtiles:// protocol pointing to local files.

    Uses URL pattern matching to connect style sources to PMTiles files.
    This is more reliable than string partial matching since we track the
    original tile URL patterns through the pipeline.

    Args:
        style: MapLibre style object from map.getStyle()
        tile_source_infos: List of TileSourceInfo objects with PMTiles paths

    Returns:
        Modified style dict with rewritten source URLs
    """
    import copy

    # Deep copy to avoid modifying the original
    rewritten_style = copy.deepcopy(style)

    if "sources" not in rewritten_style:
        print("[StyleRewrite] No sources in style", flush=True)
        return rewritten_style

    print(f"[StyleRewrite] Processing {len(rewritten_style['sources'])} sources", flush=True)

    # Debug: show what URL patterns we have
    print(f"[StyleRewrite] Available PMTiles URL patterns:", flush=True)
    for info in tile_source_infos:
        print(f"[StyleRewrite]   {info.name}: {info.url_pattern}", flush=True)

    rewrite_count = 0

    # Rewrite each source
    for source_name, source_def in rewritten_style["sources"].items():
        if source_def.get("type") not in ["vector", "raster"]:
            continue

        # STRATEGY 1: Match by tiles array (direct tile URLs)
        tile_urls = source_def.get("tiles", [])
        if tile_urls:
            print(f"[StyleRewrite] Matching source '{source_name}' (tiles array)", flush=True)
            print(f"[StyleRewrite]   Style tile URLs: {tile_urls[:2]}...", flush=True)

            matched_pmtiles = None
            for tile_url in tile_urls:
                style_pattern = _normalize_tile_url(tile_url)
                print(f"[StyleRewrite]   Normalized style pattern: {style_pattern}", flush=True)

                for info in tile_source_infos:
                    if not info.url_pattern:
                        continue

                    pmtiles_pattern = _normalize_tile_url(info.url_pattern)
                    print(f"[StyleRewrite]   Comparing to '{info.name}': {pmtiles_pattern}", flush=True)

                    if _patterns_match(style_pattern, pmtiles_pattern):
                        print(f"[StyleRewrite]   MATCH FOUND!", flush=True)
                        matched_pmtiles = info.path
                        break

                if matched_pmtiles:
                    break

            if matched_pmtiles:
                print(f"[StyleRewrite] Rewriting '{source_name}' -> {matched_pmtiles}", flush=True)
                source_def["url"] = f"pmtiles://{matched_pmtiles}"
                source_def.pop("tiles", None)
                rewrite_count += 1
            else:
                print(f"[StyleRewrite] WARNING: No match found for '{source_name}'", flush=True)
            continue

        # STRATEGY 2: Match by TileJSON URL (domain-based matching)
        tilejson_url = source_def.get("url")
        if tilejson_url:
            # Skip if already rewritten to pmtiles://
            if tilejson_url.startswith("pmtiles://"):
                continue

            print(f"[StyleRewrite] Matching source '{source_name}' (TileJSON URL)", flush=True)
            print(f"[StyleRewrite]   TileJSON URL: {tilejson_url}", flush=True)

            from urllib.parse import urlparse
            tilejson_parsed = urlparse(tilejson_url)
            tilejson_domain = tilejson_parsed.netloc

            matched_pmtiles = None
            for info in tile_source_infos:
                if not info.url_pattern:
                    continue

                pmtiles_parsed = urlparse(info.url_pattern)
                pmtiles_domain = pmtiles_parsed.netloc

                print(f"[StyleRewrite]   Comparing domain '{tilejson_domain}' to '{pmtiles_domain}' ({info.name})", flush=True)

                if tilejson_domain == pmtiles_domain:
                    print(f"[StyleRewrite]   DOMAIN MATCH FOUND!", flush=True)
                    matched_pmtiles = info.path
                    break

            if matched_pmtiles:
                print(f"[StyleRewrite] Rewriting '{source_name}' -> {matched_pmtiles}", flush=True)
                source_def["url"] = f"pmtiles://{matched_pmtiles}"
                rewrite_count += 1
            else:
                print(f"[StyleRewrite] WARNING: No domain match found for '{source_name}'", flush=True)
            continue

        # No tiles array and no URL - nothing to match
        print(f"[StyleRewrite] Source '{source_name}' has no tile URLs or TileJSON URL, skipping", flush=True)

    print(
        f"[StyleRewrite] Successfully rewrote {rewrite_count} of {len(rewritten_style['sources'])} sources",
        flush=True,
    )

    return rewritten_style


def _normalize_tile_url(url: str) -> str:
    """
    Normalize a tile URL to a pattern for comparison.

    Converts concrete tile URLs like:
      https://tiles.wxy-labs.org/parking_regs_v2/12/1205/1539.mvt
    to normalized patterns like:
      https://tiles.wxy-labs.org/parking_regs_v2/{z}/{x}/{y}.mvt

    This handles various formats including with/without file extensions.
    """
    import re

    # If already a template, normalize the placeholders
    if "{z}" in url or "{x}" in url or "{y}" in url:
        return url.replace("{z}", "{z}").replace("{x}", "{x}").replace("{y}", "{y}")

    # Replace coordinate patterns with placeholders
    # Match patterns like /12/1205/1539 or /12/1205/1539.pbf
    pattern = r"/(\d+)/(\d+)/(\d+)"
    match = re.search(pattern, url)
    if match:
        return url[: match.start()] + "/{z}/{x}/{y}" + url[match.end() :]

    return url


def _patterns_match(pattern1: str, pattern2: str) -> bool:
    """
    Check if two URL patterns match, accounting for placeholder variations.

    Handles cases where one uses {z}/{x}/{y} and another uses {z}/{x}/{y}.
    Also strips query parameters and anchors for comparison.
    """
    from urllib.parse import urlparse

    # Parse and compare just the path and netloc, ignoring query params
    parsed1 = urlparse(pattern1)
    parsed2 = urlparse(pattern2)

    # Compare scheme + netloc + path (ignore query/fragment)
    base1 = f"{parsed1.scheme}://{parsed1.netloc}{parsed1.path}"
    base2 = f"{parsed2.scheme}://{parsed2.netloc}{parsed2.path}"

    return base1 == base2


def _rewrite_sprite_url(style: dict) -> dict:
    """Rewrite sprite URL to point to local files."""
    if 'sprite' in style:
        # Local sprite path (without extension - MapLibre adds .png/.json)
        # Use relative path without leading ./ as MapLibre's URL parser may not handle it
        style['sprite'] = 'sprites/sprite'
        print(f"[StyleRewrite] Rewrote sprite URL to local path", flush=True)
    return style


def _rewrite_glyphs_url(style: dict) -> dict:
    """Rewrite glyphs URL to point to local files."""
    if 'glyphs' in style:
        # Local glyphs path template
        # Use relative path without leading ./ as MapLibre's URL parser may not handle it
        style['glyphs'] = 'glyphs/{fontstack}/{range}.pbf'
        print(f"[StyleRewrite] Rewrote glyphs URL to local path", flush=True)
    return style


def _build_archive(
    processed,
    capture,
    output_path: Path,
    name: str | None,
    mode: str,
    verbose: bool,
) -> ArchiveResult:
    """
    Internal function to build the archive.

    This contains the core logic shared by all entry points.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        tile_source_infos = []
        tile_source_results = []
        viewer_tile_sources = []
        all_coords = []
        total_tiles = 0

        # Process each tile source
        for source_name, tiles in processed.tiles_by_source.items():
            if not tiles:
                continue

            # Sanitize source name for filename
            safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in source_name)
            if not safe_name:
                safe_name = "tiles"

            if verbose:
                print(f"  Processing source '{source_name}' ({len(tiles)} tiles)")

            # Discover source layers from tile content
            discovered_layers = _discover_source_layers(tiles)
            if verbose and discovered_layers:
                print(
                    f"    Discovered layers: {discovered_layers[:5]}{'...' if len(discovered_layers) > 5 else ''}"
                )

            # Build PMTiles
            pmtiles_path = temp_path / f"{safe_name}.pmtiles"
            builder = PMTilesBuilder(pmtiles_path)

            for coord, content in tiles:
                builder.add_tile(coord, content)
                all_coords.append(coord)

            total_tiles += len(tiles)

            # Calculate bounds and zoom
            calc = CoverageCalculator()
            coords = [c for c, _ in tiles]
            bounds = calc.calculate_bounds(coords)
            zoom_range = calc.get_zoom_range(coords)

            # Get source metadata
            source = processed.tile_sources.get(source_name)
            tile_type = source.tile_type if source else "vector"
            tile_format = source.format if source else "pbf"

            # Format vector layers as TileJSON spec
            vector_layers_metadata = None
            if tile_type == "vector" and discovered_layers:
                vector_layers_metadata = [
                    {
                        "id": layer_name,
                        "fields": {},  # Could be enhanced to discover fields
                        "minzoom": zoom_range[0],
                        "maxzoom": zoom_range[1],
                    }
                    for layer_name in discovered_layers
                ]

            # Set PMTiles metadata
            builder.set_metadata(
                PMTilesMetadata(
                    name=safe_name,
                    description=f"Tiles from {capture.metadata.url}",
                    bounds=bounds,
                    min_zoom=zoom_range[0],
                    max_zoom=zoom_range[1],
                    tile_type=tile_type,
                    format=tile_format,
                    vector_layers=vector_layers_metadata,
                )
            )
            builder.build()

            # Track for packager
            url_pattern = (
                processed.url_patterns.get(source_name) if processed.url_patterns else None
            )
            tile_source_infos.append(
                TileSourceInfo(
                    name=safe_name,
                    path=f"tiles/{safe_name}.pmtiles",
                    tile_type=tile_type,
                    format=tile_format,
                    tile_count=len(tiles),
                    zoom_range=zoom_range,
                    url_pattern=url_pattern,
                )
            )

            # Track for result
            tile_source_results.append(
                TileSourceResult(
                    name=safe_name,
                    tile_count=len(tiles),
                    zoom_range=zoom_range,
                    tile_type=tile_type,
                    format=tile_format,
                    discovered_layers=discovered_layers,
                )
            )

            # Build viewer config for this source
            is_orphan = True
            if processed.style and "sources" in processed.style:
                if source_name in processed.style["sources"]:
                    is_orphan = False

            viewer_tile_sources.append(
                {
                    "name": safe_name,
                    "path": f"tiles/{safe_name}.pmtiles",
                    "type": tile_type,
                    "isOrphan": is_orphan,
                    "extractedStyle": {
                        "allLayers": discovered_layers,
                        "sourceLayer": discovered_layers[0] if discovered_layers else None,
                        "confidence": 0.8 if discovered_layers else 0.0,
                    },
                }
            )

        # Calculate overall bounds
        if all_coords:
            calc = CoverageCalculator()
            overall_bounds = calc.calculate_bounds(all_coords)
            overall_zoom_range = calc.get_zoom_range(all_coords)
        else:
            overall_bounds = GeoBounds(west=-180, south=-90, east=180, north=90)
            overall_zoom_range = (0, 14)

        # Handle captured style
        captured_style = None
        if capture.style:
            if verbose:
                print("  Found captured style from map.getStyle()")
            # Rewrite style sources to point to local PMTiles
            # DIAG: Check tile_source_infos
            print(f"[DIAG] tile_source_infos count: {len(tile_source_infos)}", flush=True)
            for info in tile_source_infos:
                print(
                    f"[DIAG] TileSourceInfo: name={info.name}, url_pattern={info.url_pattern}",
                    flush=True,
                )

            captured_style = _rewrite_style_sources(capture.style, tile_source_infos)

            # Rewrite sprite URL if we have captured sprites
            if processed.sprites:
                captured_style = _rewrite_sprite_url(captured_style)
                if verbose:
                    print(f"    Rewrote sprite URL to local path")

            # Rewrite glyphs URL if we have captured glyphs
            if processed.glyphs:
                captured_style = _rewrite_glyphs_url(captured_style)
                if verbose:
                    print(f"    Rewrote glyphs URL to local path")

            if verbose:
                print("    Style source rewriting complete (see [StyleRewrite] logs for details)")

        # Generate viewer
        if verbose:
            print("  Generating viewer...")

        archive_name = name or capture.metadata.title or "WebMap Archive"

        viewer_config = ViewerConfig(
            name=archive_name,
            bounds=overall_bounds,
            min_zoom=overall_zoom_range[0],
            max_zoom=overall_zoom_range[1],
            tile_sources=viewer_tile_sources,
            created_at=capture.metadata.captured_at,
            captured_style=captured_style,  # Pass captured style to viewer
        )

        generator = ViewerGenerator()
        viewer_html = generator.generate(viewer_config)

        # Package archive
        if verbose:
            print("  Packaging...")

        packager = ArchivePackager(output_path)

        for info in tile_source_infos:
            pmtiles_path = temp_path / f"{info.name}.pmtiles"
            packager.add_pmtiles(info.name, pmtiles_path)

        packager.add_viewer(viewer_html)

        # Add captured style if available
        if captured_style:
            style_json = json.dumps(captured_style, indent=2)
            packager.temp_files.append(("style/captured_style.json", style_json.encode("utf-8")))
            if verbose:
                print("  Added captured style to archive")

        # Add sprites to archive
        if processed.sprites:
            # Track which base files (1x) we have
            has_base_png = False
            has_base_json = False
            fallback_png = None
            fallback_json = None

            for sprite in processed.sprites:
                # Determine filename from URL or use default with variant
                if sprite.content_type == "image":
                    ext = ".png"
                else:
                    ext = ".json"

                # Use sprite@2x naming for 2x sprites
                if sprite.variant and sprite.variant != "1x":
                    filename = f"sprite@{sprite.variant}{ext}"
                    # Track @2x files as fallback for missing 1x
                    if ext == ".png":
                        fallback_png = sprite.data
                    else:
                        fallback_json = sprite.data
                else:
                    filename = f"sprite{ext}"
                    # Track that we have base files
                    if ext == ".png":
                        has_base_png = True
                    else:
                        has_base_json = True

                sprite_path = f"sprites/{filename}"
                packager.temp_files.append((sprite_path, sprite.data))

            # If we're missing base sprites but have @2x, duplicate them as base
            # This handles cases where only high-DPI sprites were captured
            if not has_base_png and fallback_png:
                print(f"[Archive] No 1x sprite.png found, using @2x as fallback", flush=True)
                packager.temp_files.append(("sprites/sprite.png", fallback_png))

            if not has_base_json and fallback_json:
                print(f"[Archive] No 1x sprite.json found, using @2x as fallback", flush=True)
                packager.temp_files.append(("sprites/sprite.json", fallback_json))

            if verbose:
                print(f"  Added {len(processed.sprites)} sprite files to archive")

        # Add glyphs to archive
        if processed.glyphs:
            for glyph in processed.glyphs:
                # Organize by font stack: glyphs/{fontstack}/{range}.pbf
                safe_fontstack = "".join(
                    c if c.isalnum() or c in " -_" else "_" for c in glyph.font_stack
                )
                glyph_range = f"{glyph.range_start}-{glyph.range_end}"
                glyph_path = f"glyphs/{safe_fontstack}/{glyph_range}.pbf"
                packager.temp_files.append((glyph_path, glyph.data))

            if verbose:
                print(f"  Added {len(processed.glyphs)} glyph files to archive")

        packager.set_manifest(
            name=archive_name,
            description=f"Archived from {capture.metadata.url}",
            bounds=overall_bounds,
            zoom_range=overall_zoom_range,
            tile_sources=tile_source_infos,
        )

        packager.build()

        if verbose:
            print(f"  Archive created: {output_path}")

        # Return result
        return ArchiveResult(
            output_path=output_path,
            size=output_path.stat().st_size,
            tile_count=total_tiles,
            tile_sources=tile_source_results,
            zoom_range=overall_zoom_range,
            bounds={
                "west": overall_bounds.west,
                "south": overall_bounds.south,
                "east": overall_bounds.east,
                "north": overall_bounds.north,
            },
        )


def _discover_source_layers(tiles: list[tuple]) -> list[str]:
    """
    Discover source layers from tile content.

    Samples tiles and extracts layer names from MVT protobuf structure.
    """
    # Try to use the CLI's layer_inspector if available
    try:
        return discover_layers_from_tiles(tiles)
    except Exception:
        pass

    # Fallback: inline implementation
    import gzip

    all_layers = []
    sample_size = min(5, len(tiles))

    for i in range(sample_size):
        coord, content = tiles[i]

        # Decompress if gzipped
        try:
            if content[:2] == b"\x1f\x8b":
                content = gzip.decompress(content)
        except Exception:
            pass

        # Extract layer names
        layers = _extract_mvt_layer_names(content)
        for layer in layers:
            if layer not in all_layers:
                all_layers.append(layer)

    return all_layers


def _extract_mvt_layer_names(data: bytes) -> list[str]:
    """Extract layer names from MVT protobuf data."""
    layer_names = []
    pos = 0

    def read_varint(data: bytes, pos: int) -> tuple[int, int]:
        result = 0
        shift = 0
        while pos < len(data):
            byte = data[pos]
            pos += 1
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result, pos

    try:
        while pos < len(data):
            tag, pos = read_varint(data, pos)
            field_number = tag >> 3
            wire_type = tag & 0x07

            if wire_type == 0:  # Varint
                _, pos = read_varint(data, pos)
            elif wire_type == 1:  # 64-bit
                pos += 8
            elif wire_type == 2:  # Length-delimited
                length, pos = read_varint(data, pos)

                if field_number == 3:  # Layer
                    layer_data = data[pos : pos + length]
                    # Extract name (field 1) from layer
                    layer_pos = 0
                    while layer_pos < len(layer_data):
                        ltag, layer_pos = read_varint(layer_data, layer_pos)
                        lfield = ltag >> 3
                        lwire = ltag & 0x07

                        if lwire == 2:
                            llength, layer_pos = read_varint(layer_data, layer_pos)
                            if lfield == 1:
                                name = layer_data[layer_pos : layer_pos + llength].decode("utf-8")
                                if name not in layer_names:
                                    layer_names.append(name)
                            layer_pos += llength
                        elif lwire == 0:
                            _, layer_pos = read_varint(layer_data, layer_pos)
                        elif lwire == 5:
                            layer_pos += 4
                        else:
                            break

                pos += length
            elif wire_type == 5:  # 32-bit
                pos += 4
            else:
                break
    except Exception:
        pass

    return layer_names


def _extract_url_from_har(entries) -> str:
    """Extract the main page URL from HAR entries."""
    for entry in entries:
        if hasattr(entry, "mime_type") and "html" in entry.mime_type:
            return entry.url
    if entries:
        return entries[0].url
    return "https://unknown"


def _extract_timestamp_from_har(entries) -> str:
    """Extract timestamp from HAR entries."""
    from datetime import datetime

    # HAR entries should have timestamps, but fall back to now
    return datetime.now().isoformat() + "Z"


def _entries_to_har_format(entries) -> list[dict]:
    """Convert parsed HAR entries back to HAR format for bundle."""
    # This is needed when creating a bundle from HAR for unified processing
    result = []
    for entry in entries:
        result.append(
            {
                "request": {"url": entry.url, "method": "GET"},
                "response": {
                    "status": entry.status_code,
                    "content": {
                        "mimeType": entry.mime_type,
                        "text": entry.content.decode("utf-8") if entry.content else "",
                    },
                },
            }
        )
    return result


# ============================================================================
# Diagnostic Functions
# ============================================================================


def validate_pmtiles(path: Path) -> dict:
    """
    Validate a PMTiles file and return diagnostic information.

    This function reads a PMTiles file and extracts key information useful
    for debugging loading issues in viewers like pmtiles.io.

    Args:
        path: Path to the PMTiles file

    Returns:
        Dictionary with diagnostic information including:
        - valid: Whether the file could be read
        - tile_type: The type of tiles (MVT, PNG, etc.)
        - tile_compression: Compression method used
        - min_zoom, max_zoom: Zoom range
        - bounds: Geographic bounds
        - tile_count: Number of tiles in the archive
        - sample_tile_info: Information about the first tile (for debugging)
    """
    from pmtiles.reader import Reader
    from pmtiles.tile import TileType, Compression

    try:
        # Open the file and create a get_bytes function
        with open(path, "rb") as f:
            file_data = f.read()

        def get_bytes(offset, length):
            return file_data[offset : offset + length]

        reader = Reader(get_bytes)

        # Read header
        header = reader.header()

        # Read metadata
        try:
            metadata = json.loads(reader.metadata())
        except:
            metadata = {}

        # Get a sample tile
        sample_tile_info = None
        try:
            # Try to get the first tile
            for entry in reader.entries():
                tile_id = entry[0]
                tile_offset = entry[1]
                tile_length = entry[2]

                # Read tile data
                tile_data = reader.get_tile(tile_id)
                if tile_data:
                    sample_tile_info = {
                        "tile_id": tile_id,
                        "size": len(tile_data),
                        "first_10_bytes": tile_data[:10].hex(),
                        "is_gzipped": len(tile_data) >= 2 and tile_data[:2] == b"\x1f\x8b",
                    }
                    break
        except Exception as e:
            sample_tile_info = {"error": str(e)}

        # Tile type names
        tile_type_names = {
            TileType.UNKNOWN: "Unknown",
            TileType.MVT: "MVT (Vector)",
            TileType.PNG: "PNG",
            TileType.JPEG: "JPEG",
            TileType.WEBP: "WebP",
        }

        compression_names = {
            Compression.UNKNOWN: "Unknown",
            Compression.NONE: "None",
            Compression.GZIP: "Gzip",
            Compression.BROTLI: "Brotli",
            Compression.ZSTD: "Zstandard",
        }

        return {
            "valid": True,
            "tile_type": tile_type_names.get(header["tile_type"], "Unknown"),
            "tile_compression": compression_names.get(header["tile_compression"], "Unknown"),
            "min_zoom": header["min_zoom"],
            "max_zoom": header["max_zoom"],
            "bounds": {
                "west": header["min_lon_e7"] / 1e7,
                "south": header["min_lat_e7"] / 1e7,
                "east": header["max_lon_e7"] / 1e7,
                "north": header["max_lat_e7"] / 1e7,
            },
            "center": {
                "lon": header["center_lon_e7"] / 1e7,
                "lat": header["center_lat_e7"] / 1e7,
                "zoom": header["center_zoom"],
            },
            "tile_count": header.get("addressed_tiles_count", 0),
            "metadata": metadata,
            "sample_tile": sample_tile_info,
        }
    except Exception as e:
        return {
            "valid": False,
            "error": str(e),
        }
