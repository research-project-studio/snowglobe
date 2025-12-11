# WebMap Archiver: Modal App Update Instructions

## For Claude Code

This document provides the updated Modal app that installs the webmap-archiver package from GitHub and uses its clean API.

---

## 1. Overview

After the API refactor and GitHub packaging, the Modal app becomes dramatically simpler:

**Before:** ~400 lines with duplicated logic
**After:** ~100 lines, just HTTP routing + API calls

---

## 2. Updated `modal_app.py`

Replace `cli/src/webmap_archiver/modal_app.py` with:

```python
"""
Modal cloud deployment for WebMap Archiver.

This is a thin HTTP layer that uses the webmap-archiver package's API.
All archive creation logic lives in the package.

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

# Container image - install from GitHub
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        # Install webmap-archiver from GitHub
        "git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli",
        # Additional dependencies for FastAPI
        "fastapi>=0.109.0",
    )
)

# Volume for temporary archive storage
volume = modal.Volume.from_name("webmap-archiver-outputs", create_if_missing=True)
VOLUME_PATH = "/outputs"


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    timeout=300,
    memory=1024,
    cpu=1.0,
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

    @web_app.post("/process")
    async def process(bundle: dict):
        """
        Process a capture bundle and return archive info.
        
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
```

---

## 3. Key Changes

### 3.1 Package Installation

**Before:**
```python
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(...)
    .add_local_dir(cli_dir, remote_path="/root/cli", copy=True)
    .run_commands("pip install -e /root/cli")
)
```

**After:**
```python
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli",
        "fastapi>=0.109.0",
    )
)
```

### 3.2 API Usage

**Before:** ~200 lines of archive building logic

**After:**
```python
from webmap_archiver import create_archive_from_bundle

result = create_archive_from_bundle(
    bundle=bundle,
    output_path=output_path,
    verbose=True,
)
```

### 3.3 No More Duplicated Code

- Layer discovery: In the package
- Bundle normalization: In the package
- Viewer generation: In the package
- Archive packaging: In the package

---

## 4. Deployment Steps

### 4.1 Prerequisites

1. **API refactor is complete** (phase-api-refactor-instructions.md)
2. **Package is on GitHub** (phase-github-package-instructions.md)
3. **Package is installable** from GitHub

### 4.2 Verify Package Installation

Test that the package installs correctly:

```bash
# In a fresh environment
pip install git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli

# Verify
python -c "from webmap_archiver import create_archive_from_bundle; print('OK')"
```

### 4.3 Deploy to Modal

```bash
# From the cli directory
modal deploy src/webmap_archiver/modal_app.py
```

### 4.4 Test the Deployment

```bash
# Check health
curl https://research-project-studio--webmap-archiver-fastapi-app.modal.run/health

# Should return:
# {"status":"ok","service":"webmap-archiver","version":"0.2.0"}
```

---

## 5. Version Pinning (Optional)

For production stability, pin to a specific version:

```python
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        # Pin to specific tag
        "git+https://github.com/research-project-studio/snowglobe.git@v0.2.0#subdirectory=cli",
        "fastapi>=0.109.0",
    )
)
```

---

## 6. Local Development Mode

For local development, you can still use the local files:

```python
# Development version of modal_app.py
import os

# Check if we're in development mode
DEV_MODE = os.environ.get("WEBMAP_DEV_MODE", "").lower() == "true"

if DEV_MODE:
    # Use local files
    cli_dir = Path(__file__).parent.parent.parent
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install("fastapi>=0.109.0")
        .add_local_dir(cli_dir, remote_path="/root/cli", copy=True)
        .run_commands("pip install -e /root/cli")
    )
else:
    # Use GitHub package
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install(
            "git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli",
            "fastapi>=0.109.0",
        )
    )
```

Then deploy with:
```bash
# Production (from GitHub)
modal deploy src/webmap_archiver/modal_app.py

# Development (local files)
WEBMAP_DEV_MODE=true modal serve src/webmap_archiver/modal_app.py
```

---

## 7. Troubleshooting

### "Module not found: webmap_archiver"

**Cause:** Package not installed in Modal image.

**Solution:** Check that the GitHub URL is correct and the package structure is valid:
```bash
pip install git+https://github.com/research-project-studio/snowglobe.git#subdirectory=cli
```

### "CaptureValidationError: Missing required field"

**Cause:** Bundle format issues.

**Solution:** The package handles normalization, but verify the bundle has required fields:
- `version`
- `metadata.url`
- `metadata.capturedAt`
- `viewport.center`
- `viewport.zoom`

### "Permission denied" on volume

**Cause:** Volume not properly configured.

**Solution:** Ensure volume exists:
```bash
modal volume create webmap-archiver-outputs
```

---

## 8. Summary

This update:

1. **Eliminates code duplication** - All logic in the package
2. **Simplifies Modal app** - Just HTTP + API calls
3. **Enables version control** - Pin package versions for stability
4. **Improves maintainability** - Fix bugs once, deploy everywhere
5. **Faster deployments** - Smaller image, cached package installs

The Modal app is now a thin HTTP wrapper around the webmap-archiver package.