"""
Modal cloud deployment for WebMap Archiver.

Provides a serverless HTTP endpoint for processing capture bundles
from the browser extension.

IMPORTANT: This uses the existing CLI code (ViewerGenerator, ArchivePackager)
rather than reimplementing archive building. The CLI code handles:
- Proper viewer generation with layer toggles
- Source layer discovery from tiles
- Style extraction from JavaScript
- Proper manifest generation

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
    from fastapi.responses import FileResponse

    # Import CLI modules
    from webmap_archiver.capture.parser import CaptureParser, CaptureValidationError
    from webmap_archiver.capture.processor import process_capture_bundle
    from webmap_archiver.tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
    from webmap_archiver.tiles.coverage import CoverageCalculator, GeoBounds
    from webmap_archiver.viewer.generator import ViewerGenerator, ViewerConfig
    from webmap_archiver.archive.packager import ArchivePackager, TileSourceInfo

    web_app = FastAPI(
        title="WebMap Archiver API",
        description="Process map capture bundles into archives",
        version="0.2.0",
    )

    @web_app.get("/health")
    async def health():
        """Health check endpoint."""
        return {
            "status": "ok",
            "service": "webmap-archiver",
            "version": "0.2.0",
        }

    @web_app.post("/process")
    async def process(bundle: dict):
        """
        Process a capture bundle and return archive info.

        Uses the same code path as the CLI for consistency.
        """
        try:
            print("=" * 60)
            print("Processing capture bundle...")
            print(f"  Bundle keys: {list(bundle.keys())}")
            print(f"  Tiles count: {len(bundle.get('tiles', []))}")
            print(
                f"  HAR entries: {len(bundle.get('har', {}).get('log', {}).get('entries', []))}"
            )

            # Log sample tile info to debug field names
            if bundle.get("tiles"):
                sample = bundle["tiles"][0]
                print(f"  Sample tile keys: {list(sample.keys())}")
                print(f"  Sample tile sourceId: {sample.get('sourceId', 'MISSING')}")
                print(f"  Sample tile source: {sample.get('source', 'MISSING')}")

            # COMPATIBILITY FIX: Handle both 'source' and 'sourceId' field names
            # The extension may send 'source' but parser expects 'sourceId'
            if bundle.get("tiles"):
                for tile in bundle["tiles"]:
                    if "source" in tile and "sourceId" not in tile:
                        tile["sourceId"] = tile.pop("source")
                print(
                    f"  After fix - sample sourceId: {bundle['tiles'][0].get('sourceId', 'MISSING')}"
                )

            # Parse and validate
            print("Parsing with CaptureParser...")
            parser = CaptureParser()
            capture = parser._build_bundle(bundle)
            print(f"  Parsed metadata URL: {capture.metadata.url}")
            print(f"  Parsed tiles: {len(capture.tiles)}")
            if capture.tiles:
                print(f"  First tile source_id: {capture.tiles[0].source_id}")

            # Process into intermediate form
            print("Processing with process_capture_bundle...")
            processed = process_capture_bundle(capture)
            print(f"  Style present: {processed.style is not None}")
            print(f"  Tile sources: {list(processed.tiles_by_source.keys())}")
            print(
                f"  Total tiles: {sum(len(t) for t in processed.tiles_by_source.values())}"
            )

            # Generate archive
            archive_id = str(uuid.uuid4())[:8]
            filename = _generate_filename(
                capture.metadata.url, capture.metadata.captured_at
            )
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

            # Build using CLI's packager
            print("Building archive with CLI packager...")
            _build_archive_with_cli(
                processed=processed,
                capture=capture,
                output_path=output_path,
                PMTilesBuilder=PMTilesBuilder,
                PMTilesMetadata=PMTilesMetadata,
                CoverageCalculator=CoverageCalculator,
                GeoBounds=GeoBounds,
                ViewerGenerator=ViewerGenerator,
                ViewerConfig=ViewerConfig,
                ArchivePackager=ArchivePackager,
                TileSourceInfo=TileSourceInfo,
            )

            size = output_path.stat().st_size
            print(f"  Archive size: {size:,} bytes")

            volume.commit()

            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            download_url = f"/download/{archive_id}"

            print(f"  Archive ID: {archive_id}")
            print(f"  Download URL: {download_url}")
            print("=" * 60)

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


def _build_archive_with_cli(
    processed,
    capture,
    output_path: Path,
    # CLI classes passed in to avoid import issues at module level
    PMTilesBuilder,
    PMTilesMetadata,
    CoverageCalculator,
    GeoBounds,
    ViewerGenerator,
    ViewerConfig,
    ArchivePackager,
    TileSourceInfo,
):
    """
    Build archive using the CLI's ViewerGenerator and ArchivePackager.

    This ensures parity with the CLI's output format.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Track tile sources for viewer config
        tile_source_infos = []
        viewer_tile_sources = []
        all_coords = []

        # Build PMTiles for each source
        for source_name, tiles in processed.tiles_by_source.items():
            if not tiles:
                continue

            # Sanitize source name for filename
            safe_name = "".join(
                c if c.isalnum() or c in "-_" else "-" for c in source_name
            )
            if not safe_name:
                safe_name = "tiles"

            print(
                f"  Building PMTiles for '{source_name}' -> {safe_name}.pmtiles ({len(tiles)} tiles)"
            )

            pmtiles_path = temp_path / f"{safe_name}.pmtiles"
            builder = PMTilesBuilder(pmtiles_path)

            for coord, content in tiles:
                builder.add_tile(coord, content)
                all_coords.append(coord)

            # Calculate bounds
            calc = CoverageCalculator()
            coords = [c for c, _ in tiles]
            bounds = calc.calculate_bounds(coords)
            zoom_range = calc.get_zoom_range(coords)

            # Get source metadata
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

            # Track for manifest (TileSourceInfo)
            tile_source_infos.append(
                TileSourceInfo(
                    name=safe_name,
                    path=f"tiles/{safe_name}.pmtiles",
                    tile_type=tile_type,
                    format=tile_format,
                    tile_count=len(tiles),
                    zoom_range=zoom_range,
                )
            )

            # Track for viewer - include extracted style info if available
            extracted_style = None
            if (
                hasattr(processed, "extracted_styles")
                and source_name in processed.extracted_styles
            ):
                extracted_style = processed.extracted_styles[source_name]

            # Determine if this is an "orphan" source (data layer not in basemap style)
            is_orphan = True
            if processed.style and "sources" in processed.style:
                if source_name in processed.style["sources"]:
                    is_orphan = False

            # Build viewer tile source entry matching ViewerGenerator expectations
            viewer_tile_sources.append(
                {
                    "name": safe_name,
                    "path": f"tiles/{safe_name}.pmtiles",
                    "type": tile_type,
                    "isOrphan": is_orphan,
                    "extractedStyle": extracted_style,
                }
            )

        # Calculate overall bounds
        if all_coords:
            calc = CoverageCalculator()
            overall_bounds = calc.calculate_bounds(all_coords)
            overall_zoom_range = calc.get_zoom_range(all_coords)
        else:
            # Fallback bounds (shouldn't happen)
            overall_bounds = GeoBounds(west=-180, south=-90, east=180, north=90)
            overall_zoom_range = (0, 14)

        # Generate viewer using CLI's ViewerGenerator
        print("  Generating viewer with ViewerGenerator...")
        viewer_config = ViewerConfig(
            name=capture.metadata.title or "WebMap Archive",
            bounds=overall_bounds,
            min_zoom=overall_zoom_range[0],
            max_zoom=overall_zoom_range[1],
            tile_sources=viewer_tile_sources,
            created_at=capture.metadata.captured_at,
        )

        generator = ViewerGenerator()
        viewer_html = generator.generate(viewer_config)

        # Build archive using CLI's ArchivePackager
        print("  Packaging with ArchivePackager...")
        packager = ArchivePackager(output_path)

        # Add PMTiles files
        for info in tile_source_infos:
            pmtiles_path = temp_path / f"{info.name}.pmtiles"
            packager.add_pmtiles(info.name, pmtiles_path)

        # Add viewer
        packager.add_viewer(viewer_html)

        # Set manifest
        packager.set_manifest(
            name=capture.metadata.title or "WebMap Archive",
            description=f"Archived from {capture.metadata.url}",
            bounds=overall_bounds,
            zoom_range=overall_zoom_range,
            tile_sources=tile_source_infos,
        )

        # Build the ZIP
        packager.build()
        print(f"  Archive built: {output_path}")


# ============================================================================
# Scheduled Cleanup (runs daily)
# ============================================================================


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    schedule=modal.Cron("0 0 * * *"),
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
