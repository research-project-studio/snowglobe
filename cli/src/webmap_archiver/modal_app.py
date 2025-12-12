"""
Modal cloud deployment for WebMap Archiver.

This is a thin HTTP layer that uses the webmap-archiver package's API.
All archive creation logic lives in the package.

Provides two capture methods:
1. POST /process - Process a pre-captured bundle (from extension DevTools)
2. POST /capture - Capture directly from URL using Puppeteer (recommended)

Deploy with:
    modal deploy src/webmap_archiver/modal_app.py

Local testing:
    modal serve src/webmap_archiver/modal_app.py
"""

import modal
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Define the Modal app
app = modal.App("webmap-archiver")

# Container image - install from GitHub with browser support
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "git",  # Need git to install from GitHub
        # Chromium and dependencies for Puppeteer
        "chromium",
        "libnss3",
        "libatk1.0-0",
        "libatk-bridge2.0-0",
        "libcups2",
        "libdrm2",
        "libxkbcommon0",
        "libxcomposite1",
        "libxdamage1",
        "libxfixes3",
        "libxrandr2",
        "libgbm1",
        "libasound2",
        "libpango-1.0-0",
        "libcairo2",
        "fonts-liberation",
    )
    .pip_install(
        # Install webmap-archiver from GitHub
        "git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli",
        # Additional dependencies
        "fastapi>=0.109.0",
        "pyppeteer>=1.0.0",
    )
    .env({
        "PYPPETEER_CHROMIUM_EXECUTABLE": "/usr/bin/chromium",
        "PYPPETEER_HOME": "/tmp/pyppeteer",
    })
)

# Volume for temporary archive storage
volume = modal.Volume.from_name("webmap-archiver-outputs", create_if_missing=True)
VOLUME_PATH = "/outputs"


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    timeout=300,
    memory=2048,  # Browser needs more memory
    cpu=2.0,
)
@modal.asgi_app()
def fastapi_app():
    """FastAPI app for the WebMap Archiver API."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse

    # Import from the package
    from webmap_archiver import (
        create_archive_from_bundle,
        inspect_bundle,
        CaptureValidationError,
        __version__,
    )

    web_app = FastAPI(
        title="WebMap Archiver API",
        description="Process map capture bundles into archives",
        version=__version__,
    )

    @web_app.get("/health")
    async def health():
        """Health check endpoint."""
        return {
            "status": "ok",
            "service": "webmap-archiver",
            "version": __version__,
            "capabilities": ["process", "capture"],
        }

    @web_app.post("/inspect")
    async def inspect(bundle: dict):
        """
        Inspect a capture bundle without creating an archive.

        Useful for validation before processing.
        """
        result = inspect_bundle(bundle)
        return {
            "valid": result.is_valid,
            "url": result.url,
            "title": result.title,
            "tileCount": result.tile_count,
            "tileSources": result.tile_sources,
            "hasStyle": result.has_style,
            "hasHar": result.has_har,
            "errors": result.errors,
            "warnings": result.warnings,
        }

    @web_app.post("/capture")
    async def capture(request: dict):
        """
        Capture a map directly from URL using Puppeteer.

        This is the recommended method - it captures the complete style
        including programmatically added layers.

        Request body:
            url: str - URL of the page containing the map
            wait_for_idle: float - Seconds to wait after network idle (default: 5.0)
            wait_for_style: float - Max seconds to wait for style to load (default: 15.0)
        """
        try:
            from webmap_archiver.capture.browser_capture import (
                capture_map_from_url,
                capture_result_to_bundle,
            )
        except ImportError:
            raise HTTPException(
                status_code=501,
                detail="Browser capture not available. Install with: pip install 'webmap-archiver[capture]'"
            )

        url = request.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="Missing required field: url")

        wait_for_idle = request.get("wait_for_idle", 5.0)
        wait_for_style = request.get("wait_for_style", 15.0)

        try:
            archive_id = str(uuid.uuid4())[:8]
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

            print(f"[API] Capture request for {url}")
            print(f"[API] Archive ID: {archive_id}")

            # Capture using Puppeteer
            result = await capture_map_from_url(
                url=url,
                wait_for_idle=wait_for_idle,
                wait_for_style=wait_for_style,
                headless=True,
            )

            if not result.tiles:
                raise HTTPException(
                    status_code=400,
                    detail=f"No tiles captured from {url}. Errors: {result.errors}"
                )

            # Convert to bundle and create archive
            bundle = capture_result_to_bundle(result)

            archive_result = create_archive_from_bundle(
                bundle=bundle,
                output_path=output_path,
                verbose=True,
            )

            volume.commit()

            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

            # Generate filename
            from urllib.parse import urlparse
            host = urlparse(url).netloc.replace(".", "-")
            date = datetime.now().strftime("%Y-%m-%d")
            filename = f"{host}-{date}.zip"

            return {
                "success": True,
                "archiveId": archive_id,
                "filename": filename,
                "downloadUrl": f"/download/{archive_id}",
                "expiresAt": expires_at.isoformat() + "Z",
                "size": archive_result.size,
                "tileCount": archive_result.tile_count,
                "tileSources": [
                    {
                        "name": ts.name,
                        "tileCount": ts.tile_count,
                        "discoveredLayers": ts.discovered_layers,
                    }
                    for ts in archive_result.tile_sources
                ],
                "styleInfo": {
                    "captured": result.style is not None,
                    "layerCount": len(result.style.get('layers', [])) if result.style else 0,
                    "sources": list(result.style.get('sources', {}).keys()) if result.style else [],
                },
                "warnings": result.errors,
            }

        except HTTPException:
            raise
        except Exception as e:
            print(f"[API] Capture error: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    @web_app.post("/process")
    async def process(bundle: dict):
        """
        Process a capture bundle and return archive info.

        Use /capture instead if possible - it provides better style capture.

        The archive is stored temporarily and can be downloaded via /download/{archive_id}
        """
        try:
            # Generate archive ID
            archive_id = str(uuid.uuid4())[:8]
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

            print(f"Processing bundle -> {archive_id}")
            print(f"  Tiles: {len(bundle.get('tiles', []))}")

            # Use the package API - all logic is encapsulated here
            result = create_archive_from_bundle(
                bundle=bundle,
                output_path=output_path,
                verbose=True,
            )

            print(f"  Created: {result.output_path}")
            print(f"  Size: {result.size:,} bytes")
            print(f"  Tiles: {result.tile_count}")

            # Commit volume changes
            volume.commit()

            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

            # Generate filename from URL
            filename = _generate_filename(bundle)

            return {
                "success": True,
                "archiveId": archive_id,
                "filename": filename,
                "downloadUrl": f"/download/{archive_id}",
                "expiresAt": expires_at.isoformat() + "Z",
                "size": result.size,
                "tileCount": result.tile_count,
                "tileSources": [
                    {
                        "name": ts.name,
                        "tileCount": ts.tile_count,
                        "zoomRange": list(ts.zoom_range),
                        "discoveredLayers": ts.discovered_layers,
                    }
                    for ts in result.tile_sources
                ],
            }

        except CaptureValidationError as e:
            print(f"Validation error: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid capture bundle: {str(e)}"
            )
        except Exception as e:
            print(f"Processing error: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(
                status_code=500,
                detail=f"Processing failed: {str(e)}"
            )

    @web_app.get("/download/{archive_id}")
    async def download(archive_id: str):
        """Download a processed archive by ID."""
        # Validate archive_id format
        if not archive_id.isalnum() or len(archive_id) != 8:
            raise HTTPException(status_code=400, detail="Invalid archive ID")

        archive_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

        if not archive_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Archive not found or expired"
            )

        return FileResponse(
            path=str(archive_path),
            media_type="application/zip",
            filename=f"webmap-archive-{archive_id}.zip",
        )

    return web_app


def _generate_filename(bundle: dict) -> str:
    """Generate a filename from bundle metadata."""
    from urllib.parse import urlparse

    metadata = bundle.get("metadata", {})
    url = metadata.get("url", "")
    captured_at = metadata.get("capturedAt", "")

    # Extract hostname
    if url:
        parsed = urlparse(url)
        host = parsed.netloc.replace(".", "-").replace(":", "-")
    else:
        host = "unknown"

    # Extract date
    date = captured_at.split("T")[0] if captured_at else "undated"

    return f"{host}-{date}.zip"


# ============================================================================
# Scheduled Cleanup
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

    if removed > 0:
        volume.commit()

    print(f"Cleaned up {removed} expired archives")
