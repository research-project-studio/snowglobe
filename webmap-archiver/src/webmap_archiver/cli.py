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
@click.option('--style-override', type=click.Path(exists=True, path_type=Path),
              help='JSON file with complete MapLibre style (from map.getStyle())')
def create(har_file: Path, output: Path | None, name: str | None, verbose: bool, 
           style_override: Path | None):
    """Create an archive from a HAR file."""

    # Set defaults
    if output is None:
        output = har_file.with_suffix('.zip')
    if name is None:
        name = har_file.stem.replace('_', ' ').replace('-', ' ').replace('.', ' ').title()

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
    temp_dir = Path(tempfile.mkdtemp())
    pmtiles_files: list[tuple[str, Path, TileSourceInfo]] = []
    
    # Also store discovered layer names for each source
    discovered_layers: dict[str, list[str]] = {}

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
        
        # Discover layer names from tile content (only for vector tiles)
        if source.tile_type == "vector":
            layers = discover_layers_from_tiles(tiles)
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
            tile_count=len(tiles),
            zoom_range=source_zoom,
        )
        pmtiles_files.append((source.name, pmtiles_path, info))
        console.print(f"  ✓ Created {pmtiles_path.name}")

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
