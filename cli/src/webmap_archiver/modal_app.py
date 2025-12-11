"""
Modal cloud deployment for WebMap Archiver.

Provides a serverless HTTP endpoint for processing capture bundles
from the browser extension.

Deploy with:
    modal deploy src/webmap_archiver/modal_app.py

Local testing:
    modal serve src/webmap_archiver/modal_app.py
"""

import modal
import json
import tempfile
import uuid
import base64
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Define the Modal app
app = modal.App("webmap-archiver")

# Get the path to the cli directory
cli_dir = Path(__file__).parent.parent.parent

# Container image with our dependencies
# Use pip install from the local directory in editable mode
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "click>=8.0",
        "pmtiles>=3.0",
        "pydantic>=2.0",
        "rich>=13.0",
        "fastapi>=0.109.0",
    )
    .add_local_dir(cli_dir, remote_path="/root/cli", copy=True)
    .run_commands("pip install -e /root/cli")
)

# Volume for temporary archive storage (auto-cleaned after 24h)
volume = modal.Volume.from_name("webmap-archiver-outputs", create_if_missing=True)
VOLUME_PATH = "/outputs"


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    timeout=300,  # 5 minutes max
    memory=1024,  # 1GB RAM
    cpu=1.0,
)
@modal.asgi_app()
def fastapi_app():
    """FastAPI app for the WebMap Archiver API."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from webmap_archiver.capture.parser import CaptureParser, CaptureValidationError
    from webmap_archiver.capture.processor import process_capture_bundle
    from webmap_archiver.tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
    from webmap_archiver.tiles.coverage import CoverageCalculator

    web_app = FastAPI(
        title="WebMap Archiver API",
        description="Process map capture bundles into archives",
        version="0.1.0",
    )

    @web_app.get("/health")
    async def health():
        """Health check endpoint."""
        return {
            "status": "ok",
            "service": "webmap-archiver",
            "version": "0.1.0",
        }

    @web_app.post("/process")
    async def process(bundle: dict):
        """
        Process a capture bundle and return archive info.

        The archive is stored temporarily and can be downloaded via /download/{archive_id}
        """
        try:
            # Parse and validate the capture bundle
            print("Parsing capture bundle...")
            print(f"  Bundle keys: {list(bundle.keys())}")
            print(f"  Tiles count: {len(bundle.get('tiles', []))}")
            print(
                f"  HAR entries: {len(bundle.get('har', {}).get('log', {}).get('entries', []))}"
            )

            # Log sample tile info
            if bundle.get("tiles"):
                sample_tile = bundle["tiles"][0]
                print(
                    f"  Sample tile: z={sample_tile.get('z')}, source={sample_tile.get('source')}"
                )

            # Log sample HAR entry URLs
            har_entries = bundle.get("har", {}).get("log", {}).get("entries", [])
            if har_entries:
                sample_urls = [
                    e.get("request", {}).get("url", "")[:60] for e in har_entries[:5]
                ]
                print(f"  Sample HAR URLs: {sample_urls}")

            parser = CaptureParser()
            capture = parser._build_bundle(bundle)

            # Process into intermediate form
            print("Processing capture bundle...")
            processed = process_capture_bundle(capture)

            # Log what we got
            print(f"  - Style present: {processed.style is not None}")
            print(f"  - Tile sources: {list(processed.tiles_by_source.keys())}")
            print(
                f"  - Total tiles: {sum(len(t) for t in processed.tiles_by_source.values())}"
            )

            # Log sample tile source info
            for source_name, tiles in processed.tiles_by_source.items():
                if tiles:
                    coord, _ = tiles[0]
                    print(
                        f"    Source '{source_name}': {len(tiles)} tiles, sample coord: z{coord.z}/{coord.x}/{coord.y}"
                    )

            # Generate archive ID and filename
            archive_id = str(uuid.uuid4())[:8]
            filename = _generate_filename(
                capture.metadata.url, capture.metadata.captured_at
            )
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

            # Build the archive with viewer
            print("Building archive...")
            _build_archive_with_viewer(processed, output_path, capture)

            # Get file size
            size = output_path.stat().st_size

            # Commit volume changes
            volume.commit()

            # Calculate expiry (24 hours from now)
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

            # Get the app URL for download
            print("Preparing download URL...")
            download_url = f"/download/{archive_id}"

            return {
                "success": True,
                "archiveId": archive_id,
                "filename": filename,
                "downloadUrl": download_url,
                "expiresAt": expires_at.isoformat() + "Z",
                "size": size,
            }

        except CaptureValidationError as e:
            print(f"Validation error: {e}")
            raise HTTPException(
                status_code=400, detail=f"Invalid capture bundle: {str(e)}"
            )
        except Exception as e:
            print(f"Processing error: {e}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

    @web_app.get("/download/{archive_id}")
    async def download(archive_id: str):
        """Download a processed archive by ID."""
        # Validate archive_id format (prevent path traversal)
        if not archive_id.isalnum() or len(archive_id) != 8:
            raise HTTPException(status_code=400, detail="Invalid archive ID")

        archive_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

        if not archive_path.exists():
            raise HTTPException(status_code=404, detail="Archive not found or expired")

        return FileResponse(
            path=str(archive_path),
            media_type="application/zip",
            filename=f"{archive_id}.zip",
        )

    return web_app


def _generate_filename(url: str, captured_at: str) -> str:
    """Generate archive filename from URL and timestamp."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc.replace(".", "-").replace(":", "-")
    date = captured_at.split("T")[0]

    return f"{host}-{date}.zip"


def _build_archive_with_viewer(processed, output_path: Path, capture):
    """
    Build a complete archive ZIP file with PMTiles, style, and viewer.
    """
    from webmap_archiver.tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
    from webmap_archiver.tiles.coverage import CoverageCalculator
    import zipfile

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        pmtiles_files = []
        source_info = {}  # Track info about each source for the viewer

        # Build PMTiles for each source
        for source_name, tiles in processed.tiles_by_source.items():
            if not tiles:
                continue

            # Clean up source name for filename
            safe_name = source_name.replace("/", "-").replace("\\", "-") or "tiles"
            print(f"  Building PMTiles for source: {source_name} ({len(tiles)} tiles)")

            pmtiles_path = temp_path / f"{safe_name}.pmtiles"
            builder = PMTilesBuilder(pmtiles_path)

            for coord, content in tiles:
                builder.add_tile(coord, content)

            # Calculate bounds and zoom range
            calc = CoverageCalculator()
            coords = [c for c, _ in tiles]
            bounds = calc.calculate_bounds(coords)
            zoom_range = calc.get_zoom_range(coords)

            # Get source info
            source = processed.tile_sources.get(source_name)
            tile_type = source.tile_type if source else "vector"
            tile_format = source.format if source else "pbf"

            builder.set_metadata(
                PMTilesMetadata(
                    name=safe_name,
                    description=f"Tiles from {capture.metadata.url}",
                    bounds=bounds,
                    min_zoom=zoom_range[0],
                    max_zoom=zoom_range[1],
                    tile_type=tile_type,
                    format=tile_format,
                )
            )

            builder.build()
            pmtiles_files.append((safe_name, pmtiles_path))
            source_info[source_name] = {
                "filename": f"{safe_name}.pmtiles",
                "bounds": bounds,
                "min_zoom": zoom_range[0],
                "max_zoom": zoom_range[1],
                "tile_type": tile_type,
                "format": tile_format,
            }

        # Extract or build style
        style = _prepare_style(processed, capture, source_info)

        # Calculate viewport from tiles or capture
        viewport = _get_viewport(capture, source_info)

        # Create ZIP file
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add PMTiles
            for name, path in pmtiles_files:
                zf.write(path, f"tiles/{name}.pmtiles")

            # Add style.json
            if style:
                zf.writestr("style.json", json.dumps(style, indent=2))
                print(f"  Added style.json")

            # Add viewer.html
            viewer_html = _generate_viewer_html(
                title=capture.metadata.title or "WebMap Archive",
                style=style,
                viewport=viewport,
                pmtiles_sources=source_info,
            )
            zf.writestr("viewer.html", viewer_html)
            print(f"  Added viewer.html")

            # Add manifest
            manifest = {
                "version": "1.0",
                "created_at": datetime.now(timezone.utc).isoformat() + "Z",
                "source_url": capture.metadata.url,
                "title": capture.metadata.title,
                "tile_sources": list(source_info.keys()),
                "tile_count": sum(
                    len(tiles) for tiles in processed.tiles_by_source.values()
                ),
                "has_style": style is not None,
                "viewport": viewport,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

            # Add README
            readme = f"""# WebMap Archive

Source: {capture.metadata.url}
Title: {capture.metadata.title}
Captured: {capture.metadata.captured_at}

## Contents

- viewer.html: Self-contained map viewer (open in browser)
- style.json: Map style definition
- tiles/: PMTiles archives
- manifest.json: Archive metadata

## Usage

1. Extract this archive
2. Open viewer.html in a web browser
3. The map should display automatically

Alternatively, use the PMTiles files with any PMTiles-compatible viewer.
"""
            zf.writestr("README.txt", readme)

        print(f"  Archive created: {output_path}")


def _prepare_style(processed, capture, source_info):
    """
    Prepare the map style, either from captured style or generate a basic one.
    """
    style = processed.style

    if style:
        # Rewrite source URLs to point to local PMTiles
        style = _rewrite_style_sources(style, source_info)
        print(f"  Using captured style with {len(style.get('layers', []))} layers")
        return style

    # Try to extract style from HAR
    if capture.har:
        extracted_style = _extract_style_from_har(capture.har)
        if extracted_style:
            extracted_style = _rewrite_style_sources(extracted_style, source_info)
            print(
                f"  Extracted style from HAR with {len(extracted_style.get('layers', []))} layers"
            )
            return extracted_style

    # Also try from the raw bundle's har if it's different
    if hasattr(capture, "_raw_har") and capture._raw_har:
        extracted_style = _extract_style_from_har(capture._raw_har)
        if extracted_style:
            extracted_style = _rewrite_style_sources(extracted_style, source_info)
            print(
                f"  Extracted style from raw HAR with {len(extracted_style.get('layers', []))} layers"
            )
            return extracted_style

    # Generate a basic style for vector tiles
    print("  Generating basic fallback style")
    return _generate_basic_style(source_info)


def _extract_style_from_har(har) -> dict | None:
    """Extract style.json from HAR entries."""
    print("  Attempting to extract style from HAR...")

    if not har:
        print("  No HAR data provided")
        return None

    # Handle different HAR structures
    if hasattr(har, "log"):
        log = har.log
    elif isinstance(har, dict) and "log" in har:
        log = har["log"]
    else:
        print(f"  Unexpected HAR structure: {type(har)}")
        return None

    if hasattr(log, "entries"):
        entries = log.entries
    elif isinstance(log, dict) and "entries" in log:
        entries = log["entries"]
    else:
        print(f"  No entries in HAR log")
        return None

    print(f"  Scanning {len(entries)} HAR entries for style...")

    style_candidates = []

    for i, entry in enumerate(entries):
        # Get URL from entry
        if hasattr(entry, "request"):
            url = (
                entry.request.url
                if hasattr(entry.request, "url")
                else entry.request.get("url", "")
            )
        elif isinstance(entry, dict):
            url = entry.get("request", {}).get("url", "")
        else:
            continue

        # Check if this looks like a style request
        is_style = (
            "style.json" in url
            or "style?" in url
            or "/styles/" in url
            or "maps/streets" in url
            or "/gl/style" in url
        )

        if is_style:
            print(f"  Found potential style URL: {url[:80]}...")

            # Get response content
            if hasattr(entry, "response"):
                response = entry.response
                content = (
                    response.content
                    if hasattr(response, "content")
                    else response.get("content", {})
                )
            elif isinstance(entry, dict):
                content = entry.get("response", {}).get("content", {})
            else:
                continue

            # Get the text content
            if hasattr(content, "text"):
                text = content.text
                encoding = getattr(content, "encoding", None)
                mime_type = getattr(content, "mimeType", "")
            elif isinstance(content, dict):
                text = content.get("text")
                encoding = content.get("encoding")
                mime_type = content.get("mimeType", "")
            else:
                continue

            if not text:
                print(f"    No text content in response")
                continue

            # Decode if base64
            if encoding == "base64":
                try:
                    text = base64.b64decode(text).decode("utf-8")
                    print(f"    Decoded base64 content ({len(text)} chars)")
                except Exception as e:
                    print(f"    Failed to decode base64: {e}")
                    continue

            # Try to parse as JSON
            if "json" in mime_type or text.strip().startswith("{"):
                try:
                    style = json.loads(text)
                    # Validate it looks like a MapLibre style
                    if "version" in style and ("layers" in style or "sources" in style):
                        print(
                            f"    ✓ Valid MapLibre style found with {len(style.get('layers', []))} layers"
                        )
                        style_candidates.append(
                            (url, style, len(style.get("layers", [])))
                        )
                    else:
                        print(
                            f"    JSON found but not a valid style (keys: {list(style.keys())[:5]})"
                        )
                except json.JSONDecodeError as e:
                    print(f"    JSON parse error: {e}")
                    continue

    # Return the style with the most layers (usually the main one)
    if style_candidates:
        style_candidates.sort(key=lambda x: x[2], reverse=True)
        best_url, best_style, layer_count = style_candidates[0]
        print(f"  Selected style from {best_url[:60]}... ({layer_count} layers)")
        return best_style

    print("  No style found in HAR entries")
    return None


def _rewrite_style_sources(style: dict, source_info: dict) -> dict:
    """Rewrite style source URLs to point to local PMTiles files."""
    style = json.loads(json.dumps(style))  # Deep copy

    if "sources" not in style:
        return style

    for source_name, source_def in style["sources"].items():
        source_type = source_def.get("type", "")

        if source_type == "vector":
            # Find matching PMTiles file
            pmtiles_file = None
            for captured_name, info in source_info.items():
                if source_name in captured_name or captured_name in source_name:
                    pmtiles_file = info["filename"]
                    break

            # Default to first PMTiles if no match
            if not pmtiles_file and source_info:
                pmtiles_file = list(source_info.values())[0]["filename"]

            if pmtiles_file:
                # Use PMTiles protocol
                source_def["url"] = f"pmtiles://tiles/{pmtiles_file}"
                # Remove tiles array if present
                source_def.pop("tiles", None)

        elif source_type == "raster":
            # Similar handling for raster tiles
            for captured_name, info in source_info.items():
                if info["tile_type"] == "raster":
                    source_def["url"] = f"pmtiles://tiles/{info['filename']}"
                    source_def.pop("tiles", None)
                    break

    return style


def _generate_basic_style(source_info: dict) -> dict:
    """Generate a basic MapLibre style for the captured tiles."""
    sources = {}
    layers = []

    for source_name, info in source_info.items():
        safe_id = source_name.replace("-", "_").replace(".", "_")

        sources[safe_id] = {
            "type": "vector" if info["tile_type"] == "vector" else "raster",
            "url": f"pmtiles://tiles/{info['filename']}",
        }

        if info["tile_type"] == "vector":
            # Add basic vector layers
            layers.extend(
                [
                    {
                        "id": f"{safe_id}_fill",
                        "type": "fill",
                        "source": safe_id,
                        "source-layer": "default",
                        "paint": {
                            "fill-color": "#e0e0e0",
                            "fill-opacity": 0.5,
                        },
                    },
                    {
                        "id": f"{safe_id}_line",
                        "type": "line",
                        "source": safe_id,
                        "source-layer": "default",
                        "paint": {
                            "line-color": "#666666",
                            "line-width": 1,
                        },
                    },
                ]
            )
        else:
            # Raster layer
            layers.append(
                {
                    "id": f"{safe_id}_raster",
                    "type": "raster",
                    "source": safe_id,
                }
            )

    return {
        "version": 8,
        "name": "WebMap Archive",
        "sources": sources,
        "layers": [
            # Background
            {
                "id": "background",
                "type": "background",
                "paint": {"background-color": "#f8f8f8"},
            },
            *layers,
        ],
    }


def _get_viewport(capture, source_info: dict) -> dict:
    """Get viewport from capture or calculate from tile bounds."""
    # Try capture viewport first
    if capture.viewport:
        center = capture.viewport.center
        if center and center != (0, 0) and center != [0, 0]:
            return {
                "center": list(center) if hasattr(center, "__iter__") else [0, 0],
                "zoom": capture.viewport.zoom or 10,
            }

    # Calculate from tile bounds
    for info in source_info.values():
        bounds = info.get("bounds")
        if bounds:
            # Handle GeoBounds object - try different attribute names
            try:
                if hasattr(bounds, "west"):
                    center_lng = (bounds.west + bounds.east) / 2
                    center_lat = (bounds.south + bounds.north) / 2
                elif hasattr(bounds, "min_lon"):
                    center_lng = (bounds.min_lon + bounds.max_lon) / 2
                    center_lat = (bounds.min_lat + bounds.max_lat) / 2
                elif hasattr(bounds, "__getitem__"):
                    # It's a list/tuple: [west, south, east, north]
                    center_lng = (bounds[0] + bounds[2]) / 2
                    center_lat = (bounds[1] + bounds[3]) / 2
                else:
                    continue

                return {
                    "center": [center_lng, center_lat],
                    "zoom": (info["min_zoom"] + info["max_zoom"]) // 2,
                }
            except Exception as e:
                print(f"  Warning: Could not parse bounds: {e}")
                continue

    # Default
    return {"center": [0, 0], "zoom": 2}


def _generate_viewer_html(
    title: str, style: dict, viewport: dict, pmtiles_sources: dict
) -> str:
    """Generate a self-contained HTML viewer."""

    # Encode style as JSON for embedding
    style_json = json.dumps(style) if style else "{}"
    viewport_json = json.dumps(viewport)

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.css" rel="stylesheet">
    <script src="https://unpkg.com/pmtiles@3.0.6/dist/pmtiles.js"></script>
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
        .info-panel {{
            position: absolute;
            top: 10px;
            left: 10px;
            background: white;
            padding: 10px 15px;
            border-radius: 4px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 13px;
            z-index: 1000;
            max-width: 300px;
        }}
        .info-panel h3 {{
            margin: 0 0 8px 0;
            font-size: 14px;
        }}
        .info-panel p {{
            margin: 4px 0;
            color: #666;
        }}
        .info-panel .close {{
            position: absolute;
            top: 5px;
            right: 8px;
            cursor: pointer;
            color: #999;
        }}
        .error-panel {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #fff3f3;
            border: 1px solid #ffcdd2;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info-panel" id="info">
        <span class="close" onclick="this.parentElement.style.display='none'">&times;</span>
        <h3>📍 {title}</h3>
        <p>This is an archived web map.</p>
        <p>Pan and zoom to explore.</p>
    </div>

    <script>
        // Register PMTiles protocol
        const protocol = new pmtiles.Protocol();
        maplibregl.addProtocol("pmtiles", protocol.tile);

        // Style and viewport from archive
        const style = {style_json};
        const viewport = {viewport_json};

        // Initialize map
        try {{
            const map = new maplibregl.Map({{
                container: 'map',
                style: style,
                center: viewport.center || [0, 0],
                zoom: viewport.zoom || 10,
                attributionControl: true,
            }});

            map.addControl(new maplibregl.NavigationControl(), 'top-right');
            
            map.on('error', function(e) {{
                console.error('Map error:', e);
            }});

            // Log when style loads
            map.on('load', function() {{
                console.log('Map loaded successfully');
                console.log('Sources:', Object.keys(map.getStyle().sources));
                console.log('Layers:', map.getStyle().layers.length);
            }});

        }} catch (e) {{
            console.error('Failed to initialize map:', e);
            document.getElementById('map').innerHTML = 
                '<div class="error-panel">' +
                '<h3>⚠️ Error Loading Map</h3>' +
                '<p>' + e.message + '</p>' +
                '<p>Make sure all files are extracted and you\\'re viewing this from a web server.</p>' +
                '</div>';
        }}
    </script>
</body>
</html>
"""


# ============================================================================
# Scheduled Cleanup (runs daily)
# ============================================================================


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    schedule=modal.Cron("0 0 * * *"),  # Midnight UTC daily
)
def cleanup_old_archives():
    """Remove archives older than 24 hours."""
    import os

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    removed = 0

    for filename in os.listdir(VOLUME_PATH):
        filepath = Path(VOLUME_PATH) / filename
        if filepath.suffix == ".zip":
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                filepath.unlink()
                removed += 1

    volume.commit()
    print(f"Cleaned up {removed} expired archives")
