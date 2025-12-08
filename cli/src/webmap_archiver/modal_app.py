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
from pathlib import Path
from datetime import datetime, timedelta

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
            parser = CaptureParser()
            capture = parser._build_bundle(bundle)

            # Process into intermediate form
            processed = process_capture_bundle(capture)

            # Generate archive ID and filename
            archive_id = str(uuid.uuid4())[:8]
            filename = _generate_filename(
                capture.metadata.url, capture.metadata.captured_at
            )
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

            # Build the archive using simplified approach
            _build_simple_archive(processed, output_path, capture)

            # Get file size
            size = output_path.stat().st_size

            # Commit volume changes
            volume.commit()

            # Calculate expiry (24 hours from now)
            expires_at = datetime.utcnow() + timedelta(hours=24)

            # Get the app URL for download
            # This will be filled in by Modal after deployment
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
            raise HTTPException(status_code=400, detail=f"Invalid capture bundle: {str(e)}")
        except Exception as e:
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


def _build_simple_archive(processed, output_path: Path, capture):
    """
    Build a simple archive ZIP file with PMTiles and viewer.
    """
    from webmap_archiver.tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
    from webmap_archiver.tiles.coverage import CoverageCalculator
    import zipfile

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        pmtiles_files = []

        # Build PMTiles for each source
        for source_name, tiles in processed.tiles_by_source.items():
            if not tiles:
                continue

            pmtiles_path = temp_path / f"{source_name}.pmtiles"
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
                    name=source_name,
                    description=f"Tiles from {capture.metadata.url}",
                    bounds=bounds,
                    min_zoom=zoom_range[0],
                    max_zoom=zoom_range[1],
                    tile_type=tile_type,
                    format=tile_format,
                )
            )

            builder.build()
            pmtiles_files.append((source_name, pmtiles_path))

        # Create ZIP file
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add PMTiles
            for name, path in pmtiles_files:
                zf.write(path, f"tiles/{name}.pmtiles")

            # Add manifest
            manifest = {
                "version": "1.0",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "source_url": capture.metadata.url,
                "title": capture.metadata.title,
                "tile_count": sum(len(tiles) for tiles in processed.tiles_by_source.values()),
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

            # Add simple README
            readme = f"""# WebMap Archive

Source: {capture.metadata.url}
Title: {capture.metadata.title}
Captured: {capture.metadata.captured_at}

## Contents

- tiles/: PMTiles archives
- manifest.json: Archive metadata

## Usage

Extract this archive and use the PMTiles files with any PMTiles-compatible viewer.
"""
            zf.writestr("README.txt", readme)


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

    cutoff = datetime.utcnow() - timedelta(hours=24)
    removed = 0

    for filename in os.listdir(VOLUME_PATH):
        filepath = Path(VOLUME_PATH) / filename
        if filepath.suffix == ".zip":
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
            if mtime < cutoff:
                filepath.unlink()
                removed += 1

    volume.commit()
    print(f"Cleaned up {removed} expired archives")
