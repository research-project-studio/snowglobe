"""
Modal cloud deployment for WebMap Archiver.

Provides:
- POST /process - Process bundle, optionally fetching style from URL
- POST /fetch-style - Fetch only the style from a URL
- GET /download/{id} - Download processed archive
"""

import modal
import asyncio
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

app = modal.App("webmap-archiver")

# Image with Chromium for Puppeteer-based style extraction
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "git",  # Need git to install from GitHub
        # Chromium dependencies
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
    # Install pyppeteer first, separately, before the package
    .pip_install("pyppeteer>=1.0.0")
    .env(
        {
            "PYPPETEER_CHROMIUM_EXECUTABLE": "/usr/bin/chromium",
            "PYPPETEER_HOME": "/tmp/pyppeteer",
        }
    )
    # Then install the package
    .pip_install(
        "git+https://github.com/research-project-studio/snowglobe.git@d5e9e76#subdirectory=cli",
        "fastapi>=0.109.0",
    )
)

volume = modal.Volume.from_name("webmap-archiver-outputs", create_if_missing=True)
VOLUME_PATH = "/outputs"


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    timeout=300,
    memory=2048,
    cpu=2.0,
)
@modal.asgi_app()
def fastapi_app():
    """FastAPI app for the WebMap Archiver API."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
    from typing import Optional
    import asyncio

    from webmap_archiver import (
        create_archive_from_bundle,
        inspect_bundle,
        CaptureValidationError,
        __version__,
    )

    # Import style extractor - will be None if pyppeteer not available
    try:
        from webmap_archiver.capture.style_extractor import extract_style_from_url
    except ImportError as e:
        print(f"WARNING: Style extractor not available: {e}")
        extract_style_from_url = None

    web_app = FastAPI(
        title="WebMap Archiver API",
        description="Archive web maps with full style preservation",
        version=__version__,
    )

    class FetchStyleRequest(BaseModel):
        """Request to fetch style from URL."""

        url: str
        wait_for_load: Optional[float] = 3.0
        wait_for_style: Optional[float] = 10.0

    @web_app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "webmap-archiver",
            "version": __version__,
            "capabilities": ["process", "fetch-style"],
        }

    @web_app.post("/fetch-style")
    async def fetch_style(request: FetchStyleRequest):
        """
        Fetch the runtime map style from a URL.

        Uses Puppeteer to navigate to the page, find the map instance,
        and extract the complete style including programmatic layers.

        Returns the style JSON for use with /process endpoint.
        """
        if extract_style_from_url is None:
            raise HTTPException(
                status_code=501, detail="Style extraction not available. Pyppeteer not installed."
            )

        try:
            print(f"[API] Fetching style from {request.url}", flush=True)

            result = await extract_style_from_url(
                url=request.url,
                wait_for_load=request.wait_for_load,
                wait_for_style=request.wait_for_style,
                headless=True,
            )

            if result.success:
                return {
                    "success": True,
                    "style": result.style,
                    "viewport": result.viewport,
                    "debug": result.debug,
                }
            else:
                return {
                    "success": False,
                    "error": result.error,
                    "debug": result.debug,
                }

        except Exception as e:
            print(f"[API] fetch-style error: {e}", flush=True)
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

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

    @web_app.post("/process")
    async def process(bundle: dict):
        """
        Process a capture bundle into an archive.

        If the bundle has no style but includes a URL in metadata,
        will attempt to fetch the style via Puppeteer.

        Args:
            bundle: Capture bundle dict with tiles, metadata, etc.
                    If bundle.metadata.url exists and bundle.style is null,
                    style will be fetched from that URL.
        """
        try:
            archive_id = str(uuid.uuid4())[:8]
            output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

            # Extract options with defaults
            options = bundle.get("options", {})
            expand_coverage = options.get("expandCoverage", True)  # Default ON
            archive_mode = options.get("archiveMode", "standalone")

            print(f"[API] Process request -> {archive_id}", flush=True)
            print(f"[API] Tiles in bundle: {len(bundle.get('tiles', []))}", flush=True)
            print(f"[API] Style in bundle: {bundle.get('style') is not None}", flush=True)
            print(
                f"[API] Options - expandCoverage: {expand_coverage}, archiveMode: {archive_mode}",
                flush=True,
            )

            # Check if we need to fetch style
            url = bundle.get("metadata", {}).get("url")
            has_style = bundle.get("style") is not None

            style_source = "bundle"

            if not has_style and url and extract_style_from_url is not None:
                print(f"[API] No style in bundle, fetching from {url}", flush=True)

                style_result = await extract_style_from_url(
                    url=url,
                    wait_for_load=3.0,
                    wait_for_style=15.0,
                    headless=True,
                )

                if style_result.success:
                    bundle["style"] = style_result.style
                    style_source = "extracted"
                    print(
                        f"[API] Style extracted: {len(style_result.style.get('layers', []))} layers",
                        flush=True,
                    )

                    # Also update viewport if we got better info
                    if style_result.viewport and not bundle.get("viewport", {}).get("bounds"):
                        bundle["viewport"] = {
                            **bundle.get("viewport", {}),
                            **style_result.viewport,
                        }
                else:
                    print(f"[API] Style extraction failed: {style_result.error}", flush=True)
                    # Continue without style - will use generated fallback

            # Create archive
            result = create_archive_from_bundle(
                bundle=bundle,
                output_path=output_path,
                mode=archive_mode,
                expand_coverage=expand_coverage,
                verbose=True,
            )

            volume.commit()

            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

            # Generate filename
            from urllib.parse import urlparse

            if url:
                host = urlparse(url).netloc.replace(".", "-").replace(":", "-")
            else:
                host = "archive"
            date = datetime.now().strftime("%Y-%m-%d")
            filename = f"{host}-{date}.zip"

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
                        "discoveredLayers": ts.discovered_layers,
                    }
                    for ts in result.tile_sources
                ],
                "styleSource": style_source,
                "styleInfo": {
                    "present": bundle.get("style") is not None,
                    "layerCount": (
                        len(bundle.get("style", {}).get("layers", [])) if bundle.get("style") else 0
                    ),
                    "sources": (
                        list(bundle.get("style", {}).get("sources", {}).keys())
                        if bundle.get("style")
                        else []
                    ),
                },
            }

        except CaptureValidationError as e:
            print(f"[API] Validation error: {e}", flush=True)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            print(f"[API] Process error: {e}", flush=True)
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

    @web_app.get("/download/{archive_id}")
    async def download(archive_id: str):
        """Download a processed archive."""
        if not archive_id.isalnum() or len(archive_id) != 8:
            raise HTTPException(status_code=400, detail="Invalid archive ID")

        archive_path = Path(VOLUME_PATH) / f"{archive_id}.zip"

        if not archive_path.exists():
            raise HTTPException(status_code=404, detail="Archive not found or expired")

        return FileResponse(
            path=str(archive_path),
            media_type="application/zip",
            filename=f"webmap-archive-{archive_id}.zip",
        )

    return web_app


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
