"""
Command-line interface for webmap-archiver.

Commands:
- create: Create archive from HAR file
- inspect: Analyze HAR file without creating archive
- capture-style-help: Show instructions for capturing map style
"""

import click
from pathlib import Path
from datetime import datetime
import json
from enum import Enum
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
import tempfile
import shutil

from .har.parser import HARParser
from .har.classifier import RequestClassifier, RequestType
from .tiles.detector import TileDetector
from .tiles.coverage import CoverageCalculator
from .tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
from .tiles.layer_inspector import discover_layers_from_tiles, get_primary_layer_name
from .styles.extractor import extract_styles_from_har
from .viewer.generator import ViewerGenerator, ViewerConfig
from .archive.packager import ArchivePackager, TileSourceInfo
from .site.extractor import SiteExtractor
from .resources.bundler import SpriteBundler, GlyphBundler, extract_all_resources
from .capture.parser import CaptureParser, validate_capture_bundle
from .capture.processor import process_capture_bundle

console = Console()


class ArchiveMode(str, Enum):
    """Archive output modes."""
    STANDALONE = "standalone"  # viewer.html + tiles only
    ORIGINAL = "original"      # original site + tiles + serve.py
    FULL = "full"              # both standalone and original


def build_archive_from_tiles(
    tile_sources: dict[str, any],  # source_name -> TileSource
    tiles_by_source: dict[str, list[tuple[any, bytes]]],  # source_name -> [(coord, content)]
    bounds: any,  # GeoBounds
    zoom_range: tuple[int, int],
    output_path: Path,
    archive_name: str,
    archive_mode: ArchiveMode,
    override_style: dict | None = None,
    har_entries: list | None = None,
    capture_metadata: dict | None = None,
    verbose: bool = False,
) -> None:
    """
    Build an archive from processed tile data.

    Shared between `create` (from HAR) and `process` (from capture bundle).
    """
    temp_dir = Path(tempfile.mkdtemp())
    pmtiles_files: list[tuple[str, Path, TileSourceInfo]] = []
    discovered_layers: dict[str, list[str]] = {}

    coverage_calc = CoverageCalculator()

    # Step 1: Build PMTiles for each source
    for source_name, source in tile_sources.items():
        tiles = tiles_by_source.get(source_name, [])
        if not tiles:
            continue

        console.print(f"Building PMTiles for [cyan]{source_name}[/]...")

        pmtiles_path = temp_dir / f"{source_name}.pmtiles"
        builder = PMTilesBuilder(pmtiles_path)

        for coord, content in tiles:
            builder.add_tile(coord, content)

        coords = [t[0] for t in tiles]
        source_bounds = coverage_calc.calculate_bounds(coords)
        source_zoom = coverage_calc.get_zoom_range(coords)

        builder.set_metadata(PMTilesMetadata(
            name=source_name,
            description=f"Tiles from {source.url_template}",
            bounds=source_bounds,
            min_zoom=source_zoom[0],
            max_zoom=source_zoom[1],
            tile_type=source.tile_type,
            format=source.format,
        ))

        builder.build()

        # Discover layer names from tile content (only for vector tiles)
        if source.tile_type == "vector":
            layers = discover_layers_from_tiles(tiles)
            layer_names = list(layers.keys())
            discovered_layers[source_name] = layer_names
            if layer_names:
                console.print(f"  ✓ Discovered layers: [cyan]{', '.join(layer_names)}[/]")

        info = TileSourceInfo(
            name=source_name,
            path=f"tiles/{source_name}.pmtiles",
            tile_type=source.tile_type,
            format=source.format,
            tile_count=len(tiles),
            zoom_range=source_zoom,
        )
        pmtiles_files.append((source_name, pmtiles_path, info))
        console.print(f"  ✓ Created {pmtiles_path.name} ({len(tiles)} tiles)")

    console.print()

    # Step 2: Extract original site assets (if mode requires it)
    extracted_assets = []
    site_dir = None

    if archive_mode in (ArchiveMode.ORIGINAL, ArchiveMode.FULL) and har_entries:
        console.print("Extracting original site assets...")

        # Detect base URL from HAR
        site_extractor = SiteExtractor()
        base_url = site_extractor.get_base_url_from_entries(har_entries)
        if base_url:
            site_extractor = SiteExtractor(base_url=base_url)
            console.print(f"  Base URL: [cyan]{base_url}[/]")

        # Create directory for extracted site
        site_dir = temp_dir / "original"
        site_dir.mkdir(exist_ok=True)

        # Extract assets
        extracted_assets = site_extractor.extract_to_directory(har_entries, site_dir)

        if extracted_assets:
            console.print(f"  ✓ Extracted [cyan]{len(extracted_assets)}[/] site assets")

            if verbose:
                html_count = sum(1 for a in extracted_assets if a.mime_type == 'text/html')
                css_count = sum(1 for a in extracted_assets if a.mime_type == 'text/css')
                js_count = sum(1 for a in extracted_assets if 'javascript' in a.mime_type)
                other_count = len(extracted_assets) - html_count - css_count - js_count
                console.print(f"    HTML: {html_count}, CSS: {css_count}, JS: {js_count}, Other: {other_count}")
        else:
            console.print("  [yellow]⚠ No site assets found to extract[/]")

        console.print()

    # Step 3: Extract map resources (sprites, glyphs) from HAR if available
    resources_dir = temp_dir / "resources"
    resources_dir.mkdir(exist_ok=True)

    sprite_path = None
    glyphs_path = None

    if har_entries:
        console.print("Extracting map resources...")

        sprite_bundle, glyph_bundle = extract_all_resources(har_entries)

        if sprite_bundle.has_sprites:
            sprites_dir = resources_dir / "sprites"
            sprite_bundle.write_to_directory(sprites_dir)
            sprite_path = "resources/sprites/sprite"
            console.print(f"  ✓ Extracted sprites")
            if verbose:
                console.print(f"    1x: {'PNG+JSON' if sprite_bundle.png_1x and sprite_bundle.json_1x else 'partial'}")
                console.print(f"    2x: {'PNG+JSON' if sprite_bundle.png_2x and sprite_bundle.json_2x else 'partial'}")
        else:
            console.print("  [dim]No sprites found in HAR[/]")

        if glyph_bundle.has_glyphs:
            glyphs_dir = resources_dir / "glyphs"
            written = glyph_bundle.write_to_directory(glyphs_dir)
            glyphs_path = "resources/glyphs/{fontstack}/{range}.pbf"
            console.print(f"  ✓ Extracted glyphs for [cyan]{len(written)}[/] font stacks")
            if verbose:
                for font_stack, count in written.items():
                    console.print(f"    {font_stack}: {count} ranges")
        else:
            console.print("  [dim]No glyphs found in HAR[/]")

        console.print()

    # Step 4: Handle style (override or extracted)
    override_layers_by_source = {}
    extracted_style_report = None

    if override_style:
        console.print("Using provided style...")
        # Extract layers grouped by source
        if 'layers' in override_style:
            for layer in override_style['layers']:
                source_id = layer.get('source')
                if source_id:
                    if source_id not in override_layers_by_source:
                        override_layers_by_source[source_id] = []
                    override_layers_by_source[source_id].append(layer)

        console.print(f"  ✓ Loaded style with {len(override_style.get('layers', []))} layers")
        console.print(f"  ✓ Sources in style: {list(override_style.get('sources', {}).keys())}")
        console.print()
    elif har_entries:
        console.print("Extracting styling from JavaScript...")

        # Get all tile URLs for matching
        detected_urls = [source.url_template for source in tile_sources.values()]

        extracted_style_report = extract_styles_from_har(har_entries, detected_urls)

        if extracted_style_report.extracted_layers:
            console.print(f"  ✓ Extracted styling for [cyan]{len(extracted_style_report.extracted_layers)}[/] layers")
            if verbose:
                for layer in extracted_style_report.extracted_layers:
                    console.print(f"    • {layer.source_layer or 'unknown'}: {len(layer.colors)} colors, confidence: {layer.extraction_confidence:.0%}")
        else:
            console.print("  [yellow]⚠ No data layer styling could be extracted from JavaScript[/]")

        console.print()

    # Step 5: Generate viewer
    console.print("Generating viewer...")
    viewer_gen = ViewerGenerator()

    BASEMAP_DOMAINS = ['maptiler', 'mapbox', 'esri', 'osm']

    tile_source_configs = []
    for _, _, info in pmtiles_files:
        # Detect if this is likely a basemap vs data layer
        is_basemap = any(domain in info.name.lower() for domain in BASEMAP_DOMAINS)

        # Get discovered layer names for this source
        source_layers = discovered_layers.get(info.name, [])

        # Build extracted style config
        extracted_style_config = None

        # Check if we have override layers for this source
        override_layers = None
        if override_layers_by_source:
            for override_source_id in override_layers_by_source.keys():
                if (override_source_id.lower() in info.name.lower() or
                    info.name.lower() in override_source_id.lower() or
                    any(part in override_source_id.lower() for part in info.name.lower().split('-') if len(part) > 2)):
                    override_layers = override_layers_by_source[override_source_id]
                    if verbose:
                        console.print(f"  ✓ Found {len(override_layers)} override layers for {info.name}")
                    break

        if override_layers:
            extracted_style_config = {
                "sourceLayer": source_layers[0] if source_layers else None,
                "allLayers": source_layers,
                "colors": {},
                "layerType": override_layers[0].get('type', 'line') if override_layers else 'line',
                "confidence": 1.0,
                "overrideLayers": override_layers
            }
        elif extracted_style_report or source_layers or not is_basemap:
            # Find extracted styling for this source
            extracted_style = None
            if extracted_style_report:
                for layer in extracted_style_report.extracted_layers:
                    if layer.tile_url:
                        source_parts = info.name.lower().replace('-', ' ').replace('_', ' ').replace('.', ' ').split()
                        url_lower = layer.tile_url.lower()
                        skip_parts = {'pbf', 'mvt', 'tiles', 'api', 'v1', 'v2', 'v3', 'v4'}
                        identifying_parts = [p for p in source_parts if p not in skip_parts and len(p) > 2]
                        matches = sum(1 for part in identifying_parts if part in url_lower)
                        match_ratio = matches / len(identifying_parts) if identifying_parts else 0
                        if match_ratio >= 0.5:
                            extracted_style = layer
                            break

            primary_source_layer = None
            if source_layers:
                primary_source_layer = source_layers[0]
            elif extracted_style and extracted_style.source_layer:
                primary_source_layer = extracted_style.source_layer

            extracted_style_config = {
                "sourceLayer": primary_source_layer,
                "allLayers": source_layers,
                "colors": extracted_style.colors if extracted_style else {},
                "layerType": extracted_style.layer_type if extracted_style else "line",
                "confidence": extracted_style.extraction_confidence if extracted_style else 0.0
            }

        tile_source_configs.append({
            "name": info.name,
            "path": info.path,
            "type": info.tile_type,
            "isOrphan": not is_basemap,
            "extractedStyle": extracted_style_config
        })

    viewer_config = ViewerConfig(
        name=archive_name,
        bounds=bounds,
        min_zoom=zoom_range[0],
        max_zoom=zoom_range[1],
        tile_sources=tile_source_configs,
        created_at=datetime.now().strftime("%Y-%m-%d"),
    )
    viewer_html = viewer_gen.generate(viewer_config)

    # Step 6: Package archive
    console.print("Packaging archive...")
    packager = ArchivePackager(output_path)

    for name_, pmtiles_path, info in pmtiles_files:
        packager.add_pmtiles(name_, pmtiles_path)

    # Write extracted styles if available
    if extracted_style_report:
        extracted_styles_json = json.dumps({
            "extraction_report": extracted_style_report.to_manifest_section(),
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
                for layer in extracted_style_report.extracted_layers
            ],
            "_comment": "This file documents extracted styling. Edit to refine layer appearance."
        }, indent=2)
        packager.temp_files.append(("style/extracted_layers.json", extracted_styles_json.encode('utf-8')))

    # Add viewer HTML (for standalone and full modes)
    if archive_mode in (ArchiveMode.STANDALONE, ArchiveMode.FULL):
        packager.add_viewer(viewer_html)
        console.print("  ✓ Added standalone viewer")

    # Add original site files (for original and full modes)
    if archive_mode in (ArchiveMode.ORIGINAL, ArchiveMode.FULL) and site_dir and site_dir.exists():
        for file_path in site_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(temp_dir)
                packager.temp_files.append((str(rel_path), file_path))

        console.print(f"  ✓ Added original site ({len(extracted_assets)} files)")

        # Add serve.py script
        serve_py_template = Path(__file__).parent / "templates" / "serve.py"
        if serve_py_template.exists():
            packager.temp_files.append(("serve.py", serve_py_template))
            console.print("  ✓ Added serve.py")
        else:
            console.print("  [yellow]⚠ serve.py template not found[/]")

    # Add resources (sprites, glyphs)
    if resources_dir.exists():
        for file_path in resources_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(temp_dir)
                packager.temp_files.append((str(rel_path), file_path))

        resource_count = sum(1 for _ in resources_dir.rglob('*') if _.is_file())
        if resource_count > 0:
            console.print(f"  ✓ Added map resources ({resource_count} files)")

    # Add captured style if provided
    if override_style:
        style_json = json.dumps(override_style, indent=2)
        packager.temp_files.append(("style/captured_style.json", style_json.encode('utf-8')))
        console.print("  ✓ Added captured style")

    # Prepare manifest
    original_site_info = None
    if archive_mode in (ArchiveMode.ORIGINAL, ArchiveMode.FULL) and extracted_assets:
        original_site_info = {
            "available": True,
            "entry_point": "original/index.html",
            "file_count": len(extracted_assets),
            "total_size_bytes": sum(len(a.content) for a in extracted_assets)
        }

    # Get original tile URLs for manifest
    tile_source_manifest = []
    for _, _, info in pmtiles_files:
        original_url = tile_sources.get(info.name).url_template if info.name in tile_sources else None

        tile_source_manifest.append({
            "name": info.name,
            "path": info.path,
            "tile_type": info.tile_type,
            "format": info.format,
            "tile_count": info.tile_count,
            "zoom_range": list(info.zoom_range),
            "original_url": original_url
        })

    packager.set_manifest(
        name=archive_name,
        description=f"WebMap archive",
        bounds=bounds,
        zoom_range=zoom_range,
        tile_sources=[info for _, _, info in pmtiles_files],
        style_extraction=extracted_style_report.to_manifest_section() if extracted_style_report else None
    )

    packager.manifest.archive_mode = archive_mode.value
    packager.manifest.tile_sources = tile_source_manifest

    if capture_metadata:
        packager.manifest.capture_metadata = capture_metadata

    packager.build()

    # Cleanup
    shutil.rmtree(temp_dir)

    console.print()
    console.print(f"[bold green]✓ Archive created:[/] {output_path}")
    console.print(f"  Size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")
    console.print(f"  Mode: {archive_mode.value}")
    console.print()


@click.group()
def main():
    """WebMap Archiver - Preserve web maps for offline access."""
    pass


@main.command()
@click.argument('har_file', type=click.Path(exists=True, path_type=Path))
@click.option('-o', '--output', type=click.Path(path_type=Path), help='Output ZIP path')
@click.option('-n', '--name', help='Archive name (default: derived from HAR filename)')
@click.option('-v', '--verbose', is_flag=True, help='Verbose output')
@click.option('--style-override', type=click.Path(exists=True, path_type=Path),
              help='JSON file with complete MapLibre style (from map.getStyle())')
@click.option('--mode', type=click.Choice(['standalone', 'original', 'full']), default='full',
              help='Archive mode: standalone (viewer only), original (site + serve.py), full (both)')
@click.option('--expand-coverage', is_flag=True, 
              help='Fetch missing tiles to fill gaps in coverage for all captured zoom levels')
@click.option('--expand-zoom', type=int, default=0,
              help='Expand coverage by N additional zoom levels (implies --expand-coverage)')
@click.option('--rate-limit', type=float, default=10.0,
              help='Rate limit for tile fetching (requests per second, default: 10)')
def create(har_file: Path, output: Path | None, name: str | None, verbose: bool, 
           style_override: Path | None, mode: str, expand_coverage: bool, 
           expand_zoom: int, rate_limit: float):
    """Create an archive from a HAR file.
    
    Archive modes:
    
    \b
    standalone - Minimal archive with viewer.html and PMTiles only.
                 Self-contained, works by opening viewer.html directly.
    
    \b
    original   - Full site preservation with original HTML/CSS/JS.
                 Requires running serve.py to intercept tile requests.
    
    \b  
    full       - Both standalone viewer and original site (default).
                 Maximum flexibility for viewing the archive.
    
    Coverage expansion:
    
    \b
    --expand-coverage   Fill gaps in the captured tile set for all zoom levels
                        that were reached during the capture session. Ensures
                        complete coverage of the session bounding box.
    
    \b
    --expand-zoom N     Additionally expand coverage by N zoom levels beyond
                        what was captured. Implies --expand-coverage.
    """
    archive_mode = ArchiveMode(mode)
    
    # --expand-zoom implies --expand-coverage
    if expand_zoom > 0:
        expand_coverage = True

    # Set defaults
    if output is None:
        output = har_file.with_suffix('.zip')
    if name is None:
        name = har_file.stem.replace('_', ' ').replace('-', ' ').replace('.', ' ').title()

    console.print(f"[bold]Creating archive from:[/] {har_file}")
    console.print(f"[bold]Output:[/] {output}")
    console.print(f"[bold]Mode:[/] {archive_mode.value}")
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

    # Step 5: Build PMTiles for each source (with optional coverage expansion)
    temp_dir = Path(tempfile.mkdtemp())
    pmtiles_files: list[tuple[str, Path, TileSourceInfo]] = []
    
    # Also store discovered layer names for each source
    discovered_layers: dict[str, list[str]] = {}
    
    # Track expansion results for manifest
    expansion_results = {}

    for template, (source, tiles) in sources.items():
        console.print(f"Building PMTiles for [cyan]{source.name}[/]...")
        
        # Start with captured tiles
        all_tiles = list(tiles)  # list of (coord, content)
        
        # Coverage expansion if requested
        if expand_coverage:
            try:
                from .tiles.fetcher import analyze_coverage, expand_coverage as do_expand, AIOHTTP_AVAILABLE
                
                if not AIOHTTP_AVAILABLE:
                    console.print("  [yellow]⚠ Coverage expansion requires aiohttp: pip install aiohttp[/]")
                else:
                    # Analyze current coverage
                    report = analyze_coverage(tiles, bounds, expand_zoom)
                    
                    if report.total_missing > 0:
                        console.print(f"  Coverage: {report.coverage_percent:.1f}% ({report.total_captured}/{report.total_required} tiles)")
                        console.print(f"  Missing {report.total_missing} tiles across {len(report.zoom_levels)} zoom levels")
                        
                        if verbose:
                            for z in report.zoom_levels:
                                captured = report.tiles_by_zoom.get(z, 0)
                                required = report.required_by_zoom.get(z, 0)
                                missing = report.missing_by_zoom.get(z, 0)
                                if missing > 0:
                                    console.print(f"    z{z}: {captured}/{required} tiles ({missing} missing)")
                        
                        # Fetch missing tiles with progress
                        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
                        
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            BarColumn(),
                            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                            TextColumn("({task.completed}/{task.total})"),
                            TimeElapsedColumn(),
                            console=console,
                        ) as progress:
                            task = progress.add_task(
                                f"Fetching tiles for {source.name}...", 
                                total=report.total_missing
                            )
                            
                            def update_progress(src_name, completed, total):
                                progress.update(task, completed=completed)
                            
                            result = do_expand(
                                url_template=source.url_template,
                                source_name=source.name,
                                captured_tiles=tiles,
                                bounds=bounds,
                                expand_zoom=expand_zoom,
                                rate_limit=rate_limit,
                                progress_callback=update_progress
                            )
                        
                        # Add fetched tiles
                        if result.new_tiles:
                            all_tiles.extend(result.new_tiles)
                            console.print(f"  ✓ Fetched {result.fetched_count} additional tiles")
                        
                        if result.failed_count > 0:
                            console.print(f"  [yellow]⚠ Failed to fetch {result.failed_count} tiles[/]")
                        
                        if result.auth_failures > 0:
                            console.print(f"  [yellow]⚠ {result.auth_failures} tiles require authentication[/]")
                        
                        # Store for manifest
                        expansion_results[source.name] = {
                            "original_tiles": result.original_count,
                            "fetched_tiles": result.fetched_count,
                            "failed_tiles": result.failed_count,
                            "auth_failures": result.auth_failures,
                            "success_rate": result.success_rate
                        }
                    else:
                        console.print(f"  ✓ Full coverage: {report.total_captured} tiles")
                        
            except ImportError as e:
                console.print(f"  [yellow]⚠ Coverage expansion unavailable: {e}[/]")
        
        # Build PMTiles with all tiles (captured + expanded)
        pmtiles_path = temp_dir / f"{source.name}.pmtiles"
        builder = PMTilesBuilder(pmtiles_path)

        for coord, content in all_tiles:
            builder.add_tile(coord, content)

        source_coords = [t[0] for t in all_tiles]
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
        
        # Discover layer names from tile content (only for vector tiles)
        if source.tile_type == "vector":
            layers = discover_layers_from_tiles(all_tiles)
            layer_names = list(layers.keys())
            discovered_layers[source.name] = layer_names
            if layer_names:
                console.print(f"  ✓ Discovered layers: [cyan]{', '.join(layer_names)}[/]")
            else:
                console.print(f"  [yellow]⚠ Could not discover layer names from tile content[/]")

        info = TileSourceInfo(
            name=source.name,
            path=f"tiles/{source.name}.pmtiles",
            tile_type=source.tile_type,
            format=source.format,
            tile_count=len(all_tiles),
            zoom_range=source_zoom,
        )
        pmtiles_files.append((source.name, pmtiles_path, info))
        console.print(f"  ✓ Created {pmtiles_path.name} ({len(all_tiles)} tiles)")

    console.print()

    # Step 5b: Extract original site assets (if mode requires it)
    extracted_assets = []
    site_dir = None
    
    if archive_mode in (ArchiveMode.ORIGINAL, ArchiveMode.FULL):
        console.print("Extracting original site assets...")
        
        # Detect base URL from HAR
        site_extractor = SiteExtractor()
        base_url = site_extractor.get_base_url_from_entries(entries)
        if base_url:
            site_extractor = SiteExtractor(base_url=base_url)
            console.print(f"  Base URL: [cyan]{base_url}[/]")
        
        # Create directory for extracted site
        site_dir = temp_dir / "original"
        site_dir.mkdir(exist_ok=True)
        
        # Extract assets
        extracted_assets = site_extractor.extract_to_directory(entries, site_dir)
        
        if extracted_assets:
            console.print(f"  ✓ Extracted [cyan]{len(extracted_assets)}[/] site assets")
            
            # Count by type
            html_count = sum(1 for a in extracted_assets if a.mime_type == 'text/html')
            css_count = sum(1 for a in extracted_assets if a.mime_type == 'text/css')
            js_count = sum(1 for a in extracted_assets if 'javascript' in a.mime_type)
            other_count = len(extracted_assets) - html_count - css_count - js_count
            
            if verbose:
                console.print(f"    HTML: {html_count}, CSS: {css_count}, JS: {js_count}, Other: {other_count}")
        else:
            console.print("  [yellow]⚠ No site assets found to extract[/]")
        
        console.print()

    # Step 5c: Extract map resources (sprites, glyphs)
    console.print("Extracting map resources...")
    
    sprite_bundle, glyph_bundle = extract_all_resources(entries)
    
    resources_dir = temp_dir / "resources"
    resources_dir.mkdir(exist_ok=True)
    
    sprite_path = None
    if sprite_bundle.has_sprites:
        sprites_dir = resources_dir / "sprites"
        sprite_bundle.write_to_directory(sprites_dir)
        sprite_path = "resources/sprites/sprite"
        console.print(f"  ✓ Extracted sprites")
        if verbose:
            console.print(f"    1x: {'PNG+JSON' if sprite_bundle.png_1x and sprite_bundle.json_1x else 'partial'}")
            console.print(f"    2x: {'PNG+JSON' if sprite_bundle.png_2x and sprite_bundle.json_2x else 'partial'}")
    else:
        console.print("  [dim]No sprites found in HAR[/]")
    
    glyphs_path = None
    if glyph_bundle.has_glyphs:
        glyphs_dir = resources_dir / "glyphs"
        written = glyph_bundle.write_to_directory(glyphs_dir)
        glyphs_path = "resources/glyphs/{fontstack}/{range}.pbf"
        console.print(f"  ✓ Extracted glyphs for [cyan]{len(written)}[/] font stacks")
        if verbose:
            for font_stack, count in written.items():
                console.print(f"    {font_stack}: {count} ranges")
    else:
        console.print("  [dim]No glyphs found in HAR[/]")
    
    console.print()

    # Step 6: Extract styling from JavaScript files (or use override)
    console.print("Extracting styling from JavaScript...")

    # Get all tile URLs for matching
    detected_urls = []
    for template, (source, tiles) in sources.items():
        detected_urls.append(source.url_template)

    # Check if user provided a style override (from map.getStyle())
    override_style = None
    override_layers_by_source = {}  # source_id -> list of layer definitions
    
    if style_override:
        console.print(f"  Loading style override from [cyan]{style_override}[/]")
        try:
            with open(style_override, 'r') as f:
                override_style = json.load(f)
            
            # Extract layers grouped by source
            if 'layers' in override_style:
                for layer in override_style['layers']:
                    source_id = layer.get('source')
                    if source_id:
                        if source_id not in override_layers_by_source:
                            override_layers_by_source[source_id] = []
                        override_layers_by_source[source_id].append(layer)
            
            console.print(f"  ✓ Loaded style with {len(override_style.get('layers', []))} layers")
            console.print(f"  ✓ Sources in style: {list(override_style.get('sources', {}).keys())}")
        except Exception as e:
            console.print(f"  [red]✗ Failed to load style override: {e}[/]")
            override_style = None

    style_report = extract_styles_from_har(entries, detected_urls)

    if style_report.extracted_layers:
        console.print(f"  ✓ Extracted styling for [cyan]{len(style_report.extracted_layers)}[/] layers")
        for layer in style_report.extracted_layers:
            if verbose:
                console.print(f"    • {layer.source_layer or 'unknown'}: {len(layer.colors)} colors, confidence: {layer.extraction_confidence:.0%}")
    else:
        console.print("  [yellow]⚠ No data layer styling could be extracted from JavaScript[/]")

    if style_report.unmatched_sources:
        console.print(f"  [yellow]⚠ {len(style_report.unmatched_sources)} sources have no extracted styling[/]")

    console.print()

    # Step 7: Generate viewer
    # Detect which sources are "orphan" (not in style.json)
    # This is the common case - data layers added programmatically
    BASEMAP_DOMAINS = ['maptiler', 'mapbox', 'esri', 'osm']

    console.print("Generating viewer...")
    viewer_gen = ViewerGenerator()

    tile_source_configs = []
    for _, _, info in pmtiles_files:
        # Detect if this is likely a basemap vs data layer
        is_basemap = any(domain in info.name.lower() for domain in BASEMAP_DOMAINS)
        
        # Get discovered layer names for this source (from actual tile inspection)
        source_layers = discovered_layers.get(info.name, [])

        # Find extracted styling for this source if available (for colors, etc.)
        extracted_style = None
        for layer in style_report.extracted_layers:
            if layer.tile_url:
                # Match by checking if key identifying parts of the source name appear in the URL
                # This is more robust than substring matching after normalization
                source_parts = info.name.lower().replace('-', ' ').replace('_', ' ').replace('.', ' ').split()
                url_lower = layer.tile_url.lower()
                
                # Filter out common non-identifying parts
                skip_parts = {'pbf', 'mvt', 'tiles', 'api', 'v1', 'v2', 'v3', 'v4'}
                identifying_parts = [p for p in source_parts if p not in skip_parts and len(p) > 2]
                
                # Check if most identifying parts appear in URL
                matches = sum(1 for part in identifying_parts if part in url_lower)
                match_ratio = matches / len(identifying_parts) if identifying_parts else 0
                
                if verbose:
                    console.print(f"  Matching '{info.name}' against layer URL '{layer.tile_url}'")
                    console.print(f"    Identifying parts: {identifying_parts}")
                    console.print(f"    Matches: {matches}/{len(identifying_parts)} ({match_ratio:.0%})")
                
                # Consider it a match if at least 50% of identifying parts are found
                if match_ratio >= 0.5:
                    extracted_style = layer
                    if verbose:
                        console.print(f"  ✓ Matched {info.name} to extracted layer")
                    break

        # Build extracted style config
        # PRIORITY: 
        # 1. Style override (from map.getStyle()) - 100% accurate
        # 2. Discovered layer names from tiles (reliable for source-layer)
        # 3. JS-extracted colors (may be incomplete)
        extracted_style_config = None
        
        # Check if we have override layers for this source
        override_layers = None
        if override_style:
            # Try to match by source name
            for override_source_id in override_layers_by_source.keys():
                # Check if source names match (with some flexibility)
                if (override_source_id.lower() in info.name.lower() or 
                    info.name.lower() in override_source_id.lower() or
                    any(part in override_source_id.lower() for part in info.name.lower().split('-') if len(part) > 2)):
                    override_layers = override_layers_by_source[override_source_id]
                    if verbose:
                        console.print(f"  ✓ Found {len(override_layers)} override layers for {info.name} (matched {override_source_id})")
                    break
        
        if override_layers:
            # Use the complete layer definitions from override
            # Pass them directly to the viewer for rendering
            extracted_style_config = {
                "sourceLayer": source_layers[0] if source_layers else None,
                "allLayers": source_layers,
                "colors": {},  # Colors are embedded in the override layers
                "layerType": override_layers[0].get('type', 'line') if override_layers else 'line',
                "confidence": 1.0,  # 100% confidence with override
                "overrideLayers": override_layers  # Pass complete layer definitions
            }
        elif extracted_style or source_layers or not is_basemap:
            # Use discovered source-layer from tile inspection as primary source
            # Fall back to JS-extracted source-layer only if tile inspection failed
            primary_source_layer = None
            if source_layers:
                primary_source_layer = source_layers[0]  # Use first discovered layer
                if verbose:
                    console.print(f"  Using discovered source-layer: {primary_source_layer}")
            elif extracted_style and extracted_style.source_layer:
                primary_source_layer = extracted_style.source_layer
                if verbose:
                    console.print(f"  Using JS-extracted source-layer: {primary_source_layer}")
            
            extracted_style_config = {
                "sourceLayer": primary_source_layer,
                "allLayers": source_layers,  # Pass all discovered layers for reference
                "colors": extracted_style.colors if extracted_style else {},
                "layerType": extracted_style.layer_type if extracted_style else "line",
                "confidence": extracted_style.extraction_confidence if extracted_style else 0.0
            }

        tile_source_configs.append({
            "name": info.name,
            "path": info.path,
            "type": info.tile_type,
            "isOrphan": not is_basemap,  # Data layers are "orphan" = not in base style
            "extractedStyle": extracted_style_config
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
    console.print("Packaging archive...")
    packager = ArchivePackager(output)

    for name_, pmtiles_path, info in pmtiles_files:
        packager.add_pmtiles(name_, pmtiles_path)

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

    # Add viewer HTML (for standalone and full modes)
    if archive_mode in (ArchiveMode.STANDALONE, ArchiveMode.FULL):
        packager.add_viewer(viewer_html)
        console.print("  ✓ Added standalone viewer")

    # Add original site files (for original and full modes)
    if archive_mode in (ArchiveMode.ORIGINAL, ArchiveMode.FULL) and site_dir and site_dir.exists():
        # Add all files from the site directory
        for file_path in site_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(temp_dir)
                packager.temp_files.append((str(rel_path), file_path))
        
        console.print(f"  ✓ Added original site ({len(extracted_assets)} files)")
        
        # Add serve.py script
        serve_py_template = Path(__file__).parent / "templates" / "serve.py"
        if serve_py_template.exists():
            packager.temp_files.append(("serve.py", serve_py_template))
            console.print("  ✓ Added serve.py")
        else:
            console.print("  [yellow]⚠ serve.py template not found[/]")

    # Add resources (sprites, glyphs)
    if resources_dir.exists():
        for file_path in resources_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(temp_dir)
                packager.temp_files.append((str(rel_path), file_path))
        
        resource_count = sum(1 for _ in resources_dir.rglob('*') if _.is_file())
        if resource_count > 0:
            console.print(f"  ✓ Added map resources ({resource_count} files)")

    # Prepare manifest with additional metadata
    original_site_info = None
    if archive_mode in (ArchiveMode.ORIGINAL, ArchiveMode.FULL) and extracted_assets:
        original_site_info = {
            "available": True,
            "entry_point": "original/index.html",
            "file_count": len(extracted_assets),
            "total_size_bytes": sum(len(a.content) for a in extracted_assets)
        }

    resources_info = {}
    if sprite_bundle.has_sprites:
        resources_info["sprites"] = {
            "available": True,
            "path": sprite_path
        }
    if glyph_bundle.has_glyphs:
        resources_info["glyphs"] = {
            "available": True,
            "path": glyphs_path,
            "font_stacks": glyph_bundle.font_stacks
        }

    # Get original tile URLs for manifest (needed by serve.py)
    tile_source_manifest = []
    for _, _, info in pmtiles_files:
        # Find the original URL template
        original_url = None
        for template, (source, tiles) in sources.items():
            if source.name == info.name:
                original_url = source.url_template
                break
        
        tile_source_manifest.append({
            "name": info.name,
            "path": info.path,
            "tile_type": info.tile_type,
            "format": info.format,
            "tile_count": info.tile_count,
            "zoom_range": list(info.zoom_range),
            "original_url": original_url
        })

    packager.set_manifest(
        name=name,
        description=f"WebMap archive created from {har_file.name}",
        bounds=bounds,
        zoom_range=zoom_range,
        tile_sources=[info for _, _, info in pmtiles_files],
        style_extraction=style_report.to_manifest_section()
    )
    
    # Enhance manifest with additional info
    packager.manifest.archive_mode = archive_mode.value
    packager.manifest.tile_sources = tile_source_manifest
    if original_site_info:
        packager.manifest.to_dict()  # Ensure it's built
    if resources_info:
        pass  # Will add resources to manifest in future update

    packager.build()

    # Cleanup
    shutil.rmtree(temp_dir)

    console.print()
    console.print(f"[bold green]✓ Archive created:[/] {output}")
    console.print(f"  Size: {output.stat().st_size / 1024 / 1024:.2f} MB")
    console.print(f"  Mode: {archive_mode.value}")
    console.print()
    
    # Show usage instructions based on mode
    if archive_mode == ArchiveMode.STANDALONE:
        console.print("[bold]To view:[/]")
        console.print("  1. Extract the ZIP file")
        console.print("  2. Open viewer.html in a browser")
    elif archive_mode == ArchiveMode.ORIGINAL:
        console.print("[bold]To view:[/]")
        console.print("  1. Extract the ZIP file")
        console.print("  2. Run: python serve.py")
        console.print("  3. Open http://localhost:8080 in a browser")
    else:  # FULL
        console.print("[bold]To view:[/]")
        console.print("  Option A (standalone): Extract ZIP and open viewer.html")
        console.print("  Option B (original site): Extract ZIP and run: python serve.py")


@main.command()
@click.argument('capture_file', type=click.Path(exists=True, path_type=Path))
@click.option('-o', '--output', type=click.Path(path_type=Path), help='Output ZIP path')
@click.option('-n', '--name', help='Archive name (default: derived from metadata)')
@click.option('-v', '--verbose', is_flag=True, help='Verbose output')
@click.option('--mode', type=click.Choice(['standalone', 'original', 'full']), default='standalone',
              help='Archive mode (default: standalone)')
def process(capture_file: Path, output: Path | None, name: str | None, verbose: bool, mode: str):
    """Create an archive from a capture bundle.

    Capture bundles are richer than HAR files and can contain:
    - Pre-extracted tiles and resources
    - Runtime map style from map.getStyle()
    - Viewport state and metadata

    Supports multiple formats:
    - JSON: .webmap.json, .webmap-capture.json
    - Gzipped JSON: .webmap.json.gz
    - Directory bundle: folder with manifest.json
    - NDJSON stream: .webmap.ndjson
    """
    archive_mode = ArchiveMode(mode)

    console.print(f"[bold]Processing capture bundle:[/] {capture_file}")
    console.print(f"[bold]Mode:[/] {archive_mode.value}")
    console.print()

    # Step 1: Parse capture bundle
    with console.status("Parsing capture bundle..."):
        try:
            parser = CaptureParser()
            bundle = parser.parse(capture_file)
        except Exception as e:
            console.print(f"[red]✗ Failed to parse capture bundle: {e}[/]")
            raise click.Abort()

    console.print(f"  ✓ Parsed capture bundle (version {bundle.version})")
    console.print(f"  Source: [cyan]{bundle.metadata.url}[/]")
    console.print(f"  Title: {bundle.metadata.title or '(untitled)'}")
    console.print(f"  Captured: {bundle.metadata.captured_at}")
    if bundle.metadata.map_library_type:
        console.print(f"  Map library: {bundle.metadata.map_library_type} {bundle.metadata.map_library_version or ''}")
    console.print(f"  Viewport: {bundle.viewport.center} @ z{bundle.viewport.zoom}")
    console.print(f"  Tiles: {len(bundle.tiles)}")
    console.print(f"  Resources: {len(bundle.resources)}")
    console.print(f"  Has style: {'✓' if bundle.style else '✗'}")
    console.print(f"  Has HAR: {'✓' if bundle.har else '✗'}")
    console.print()

    # Step 2: Validate bundle
    with console.status("Validating bundle..."):
        warnings = validate_capture_bundle(bundle)

    if warnings:
        console.print("[yellow]Warnings:[/]")
        for warning in warnings:
            console.print(f"  ⚠ {warning}")
        console.print()

    # Step 3: Process bundle into intermediate form
    with console.status("Processing bundle..."):
        processed = process_capture_bundle(bundle)

    console.print(f"  ✓ Processed capture")
    console.print(f"  Tile sources: [cyan]{len(processed.tile_sources)}[/]")
    for source_id, source in processed.tile_sources.items():
        tile_count = len(processed.tiles_by_source.get(source_id, []))
        console.print(f"    • {source.name}: {tile_count} tiles ({source.tile_type})")
    console.print()

    # Set defaults
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

    if name is None:
        name = processed.title

    console.print(f"[bold]Output:[/] {output}")
    console.print()

    # Calculate zoom range
    all_coords = [coord for tiles in processed.tiles_by_source.values() for coord, _ in tiles]
    coverage_calc = CoverageCalculator()
    zoom_range = coverage_calc.get_zoom_range(all_coords) if all_coords else (0, 14)

    # Prepare capture metadata for manifest
    capture_metadata = {
        "source_url": processed.source_url,
        "captured_at": processed.captured_at,
        "viewport": {
            "center": bundle.viewport.center,
            "zoom": bundle.viewport.zoom,
            "bearing": bundle.viewport.bearing,
            "pitch": bundle.viewport.pitch
        }
    }

    # Step 4: Use shared archive builder
    build_archive_from_tiles(
        tile_sources=processed.tile_sources,
        tiles_by_source=processed.tiles_by_source,
        bounds=processed.bounds,
        zoom_range=zoom_range,
        output_path=output,
        archive_name=name,
        archive_mode=archive_mode,
        override_style=bundle.style,
        har_entries=processed.har_entries,
        capture_metadata=capture_metadata,
        verbose=verbose
    )

    # Show usage instructions
    if archive_mode == ArchiveMode.STANDALONE:
        console.print("[bold]To view:[/]")
        console.print("  1. Extract the ZIP file")
        console.print("  2. Open viewer.html in a browser")
    elif archive_mode == ArchiveMode.ORIGINAL:
        console.print("[bold]To view:[/]")
        console.print("  1. Extract the ZIP file")
        console.print("  2. Run: python serve.py")
        console.print("  3. Open http://localhost:8080 in a browser")
    else:  # FULL
        console.print("[bold]To view:[/]")
        console.print("  Option A (standalone): Extract ZIP and open viewer.html")
        console.print("  Option B (original site): Extract ZIP and run: python serve.py")


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


@main.command('capture-style-help')
def capture_style_help():
    """Show instructions for capturing map style from browser DevTools."""
    
    console.print()
    console.print("[bold cyan]How to Capture Map Style Using Browser DevTools[/]")
    console.print("=" * 60)
    console.print()
    console.print("The most accurate way to capture a web map's style is to extract")
    console.print("it directly from the running map using the browser's DevTools.")
    console.print()
    console.print("[bold]Step 1:[/] Open the web map in your browser")
    console.print()
    console.print("[bold]Step 2:[/] Open DevTools (press F12 or Cmd+Option+I)")
    console.print()
    console.print("[bold]Step 3:[/] Go to the Console tab")
    console.print()
    console.print("[bold]Step 4:[/] Paste this script and press Enter:")
    console.print()
    console.print("[dim]─" * 60 + "[/]")
    console.print("""[green]
// Find the map instance and copy its style to clipboard
(function() {
    // Try common variable names for the map
    const map = window.map || window._map || window.mapInstance ||
                Object.values(window).find(v => v && v.getStyle && v.getCenter);
    
    if (!map) {
        console.error('Could not find map instance. Try: Object.keys(window)');
        return;
    }
    
    if (!map.isStyleLoaded()) {
        console.error('Map style not yet loaded. Wait and try again.');
        return;
    }
    
    const style = map.getStyle();
    const output = JSON.stringify(style, null, 2);
    
    // Copy to clipboard
    copy(output);
    console.log('✓ Style copied to clipboard! (' + style.layers.length + ' layers)');
    console.log('Save to a file and use with: --style-override style.json');
})();
[/]""")
    console.print("[dim]─" * 60 + "[/]")
    console.print()
    console.print("[bold]Step 5:[/] Save the clipboard contents to a file (e.g., style.json)")
    console.print()
    console.print("[bold]Step 6:[/] Run webmap-archive with the style override:")
    console.print()
    console.print("  [cyan]webmap-archive create map.har --style-override style.json -o archive.zip[/]")
    console.print()
    console.print("[bold]Troubleshooting:[/]")
    console.print("  • If 'map' is not found, the site may use a different variable name.")
    console.print("  • Try typing 'map' in console to see if it exists.")
    console.print("  • Look for MapLibre/Mapbox map instances in the page's global scope.")
    console.print("  • Some sites wrap the map in a framework - you may need to dig deeper.")
    console.print()


if __name__ == '__main__':
    main()
