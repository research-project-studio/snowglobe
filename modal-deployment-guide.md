# WebMap Archiver: Modal Cloud Deployment

## Overview

This guide covers deploying the WebMap Archiver processing backend to [Modal](https://modal.com), enabling one-click map archiving directly from the browser extension without requiring users to install Python.

**Architecture:**
```
┌──────────────┐      HTTPS POST       ┌─────────────────────┐
│   Browser    │ ───────────────────►  │   Modal Function    │
│  Extension   │                       │   (Python 3.12)     │
└──────────────┘                       └─────────────────────┘
       │                                         │
       │                                         ▼
       │                               ┌─────────────────────┐
       │   Download URL                │   Modal Volume      │
       │ ◄──────────────────────────── │   (temp storage)    │
       │                               └─────────────────────┘
       ▼
┌──────────────┐
│  .zip file   │
└──────────────┘
```

---

## 1. Prerequisites

```bash
# Install Modal CLI
pip install modal

# Authenticate (opens browser)
modal token new
```

---

## 2. Project Structure

Add Modal deployment files to the repository:

```
webmap-archiver/
├── cli/
│   ├── pyproject.toml
│   └── src/webmap_archiver/
│       ├── __init__.py
│       ├── cli.py
│       ├── capture/
│       ├── tiles/
│       ├── viewer/
│       ├── archive/
│       └── modal_app.py          # NEW: Modal deployment
├── extension/
│   └── ...
└── modal.toml                     # NEW: Modal config (optional)
```

---

## 3. Modal App Implementation

Create `cli/src/webmap_archiver/modal_app.py`:

```python
"""
Modal cloud deployment for WebMap Archiver.

Provides a serverless HTTP endpoint for processing capture bundles
from the browser extension.

Deploy with:
    modal deploy webmap_archiver.modal_app

Local testing:
    modal serve webmap_archiver.modal_app
"""

import modal
import json
import tempfile
import uuid
import base64
from pathlib import Path
from datetime import datetime, timedelta

# Define the Modal app
app = modal.App("webmap-archiver")

# Container image with our dependencies
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "click>=8.0",
        "rich>=13.0",
    )
    .copy_local_dir("src/webmap_archiver", "/root/webmap_archiver")
)

# Volume for temporary archive storage (auto-cleaned after 24h)
volume = modal.Volume.from_name("webmap-archiver-outputs", create_if_missing=True)
VOLUME_PATH = "/outputs"

# Secrets (optional, for future integrations)
# secrets = modal.Secret.from_name("webmap-archiver-secrets")


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    timeout=300,  # 5 minutes max
    memory=1024,  # 1GB RAM
    cpu=1.0,
)
@modal.web_endpoint(method="POST", docs=True)
def process(bundle: dict) -> dict:
    """
    Process a capture bundle and return a download URL.
    
    Expects POST body:
    ```json
    {
        "version": "1.0",
        "metadata": { "url": "...", "capturedAt": "...", ... },
        "viewport": { "center": [...], "zoom": ... },
        "style": { ... },
        "har": { ... },
        "tiles": [ ... ]
    }
    ```
    
    Returns:
    ```json
    {
        "success": true,
        "archiveId": "abc12345",
        "filename": "example-com-2024-01-15.zip",
        "downloadUrl": "https://...",
        "expiresAt": "2024-01-16T10:30:00Z",
        "size": 1234567
    }
    ```
    """
    import sys
    sys.path.insert(0, "/root")
    
    from webmap_archiver.capture.parser import CaptureParser, CaptureValidationError
    from webmap_archiver.capture.processor import process_capture_bundle
    
    try:
        # Parse and validate the capture bundle
        parser = CaptureParser()
        capture = parser._build_bundle(bundle)
        
        # Process into intermediate form
        processed = process_capture_bundle(capture)
        
        # Generate archive
        archive_id = str(uuid.uuid4())[:8]
        filename = _generate_filename(capture.metadata.url, capture.metadata.captured_at)
        output_path = Path(VOLUME_PATH) / f"{archive_id}.zip"
        
        # Build the archive using existing pipeline
        _build_archive(processed, output_path, capture)
        
        # Get file size
        size = output_path.stat().st_size
        
        # Commit volume changes
        volume.commit()
        
        # Calculate expiry (24 hours from now)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        
        return {
            "success": True,
            "archiveId": archive_id,
            "filename": filename,
            "downloadUrl": f"https://YOUR_USERNAME--webmap-archiver-download.modal.run/{archive_id}",
            "expiresAt": expires_at.isoformat() + "Z",
            "size": size,
        }
        
    except CaptureValidationError as e:
        return {
            "success": False,
            "error": f"Invalid capture bundle: {str(e)}",
            "errorType": "validation",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "errorType": "processing",
        }


@app.function(
    image=image,
    volumes={VOLUME_PATH: volume},
    timeout=60,
)
@modal.web_endpoint(method="GET", docs=True)
def download(archive_id: str) -> modal.web_endpoint.Response:
    """
    Download a processed archive by ID.
    
    Archives are available for 24 hours after creation.
    """
    from modal import web_endpoint
    
    # Validate archive_id format (prevent path traversal)
    if not archive_id.isalnum() or len(archive_id) != 8:
        return web_endpoint.Response(
            content=json.dumps({"error": "Invalid archive ID"}),
            status_code=400,
            media_type="application/json",
        )
    
    archive_path = Path(VOLUME_PATH) / f"{archive_id}.zip"
    
    if not archive_path.exists():
        return web_endpoint.Response(
            content=json.dumps({"error": "Archive not found or expired"}),
            status_code=404,
            media_type="application/json",
        )
    
    # Read and return the archive
    content = archive_path.read_bytes()
    
    return web_endpoint.Response(
        content=content,
        status_code=200,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive_id}.zip"',
            "Content-Length": str(len(content)),
        },
    )


@app.function(image=image, timeout=30)
@modal.web_endpoint(method="GET")
def health() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "webmap-archiver",
        "version": "0.1.0",
    }


# ============================================================================
# Helper Functions
# ============================================================================

def _generate_filename(url: str, captured_at: str) -> str:
    """Generate archive filename from URL and timestamp."""
    from urllib.parse import urlparse
    
    parsed = urlparse(url)
    host = parsed.netloc.replace(".", "-").replace(":", "-")
    date = captured_at.split("T")[0]
    
    return f"{host}-{date}.zip"


def _build_archive(processed, output_path: Path, capture) -> None:
    """
    Build the archive ZIP file.
    
    This reuses the existing archive building logic from the CLI.
    """
    import sys
    sys.path.insert(0, "/root")
    
    from webmap_archiver.tiles.pmtiles import PMTilesBuilder, PMTilesMetadata
    from webmap_archiver.tiles.coverage import CoverageCalculator
    from webmap_archiver.viewer.generator import ViewerGenerator, ViewerConfig
    from webmap_archiver.archive.packager import ArchivePackager, TileSourceInfo
    
    import zipfile
    import tempfile
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        pmtiles_files = []
        tile_source_infos = []
        
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
            
            builder.set_metadata(PMTilesMetadata(
                name=source_name,
                description=f"Tiles from {capture.metadata.url}",
                bounds=bounds,
                min_zoom=zoom_range[0],
                max_zoom=zoom_range[1],
                tile_type=tile_type,
                format=tile_format,
            ))
            
            builder.build()
            pmtiles_files.append(pmtiles_path)
            
            tile_source_infos.append(TileSourceInfo(
                name=source_name,
                pmtiles_path=pmtiles_path,
                bounds=bounds,
                min_zoom=zoom_range[0],
                max_zoom=zoom_range[1],
                tile_type=tile_type,
                format=tile_format,
                original_url=source.url_template if source else "",
            ))
        
        # Generate viewer HTML
        viewer_config = ViewerConfig(
            title=capture.metadata.title or "Archived Map",
            center=list(processed.bounds.center),
            zoom=capture.viewport.zoom,
            bounds=[
                processed.bounds.west,
                processed.bounds.south,
                processed.bounds.east,
                processed.bounds.north,
            ],
        )
        
        # Add tile sources to viewer config
        for info in tile_source_infos:
            viewer_config.add_pmtiles_source(
                name=info.name,
                path=f"tiles/{info.name}.pmtiles",
                tile_type=info.tile_type,
            )
        
        # Use captured style if available
        if processed.style:
            viewer_config.style = processed.style
        
        generator = ViewerGenerator()
        viewer_html = generator.generate(viewer_config)
        
        # Create the archive
        packager = ArchivePackager(output_path)
        
        # Add PMTiles files
        for pmtiles_path in pmtiles_files:
            packager.add_file(pmtiles_path, f"tiles/{pmtiles_path.name}")
        
        # Add viewer
        packager.add_content("viewer.html", viewer_html)
        
        # Add manifest
        manifest = {
            "version": "1.0",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "source_url": capture.metadata.url,
            "title": capture.metadata.title,
            "viewport": {
                "center": list(capture.viewport.center),
                "zoom": capture.viewport.zoom,
            },
            "tile_sources": [
                {
                    "name": info.name,
                    "path": f"tiles/{info.name}.pmtiles",
                    "bounds": [info.bounds.west, info.bounds.south, info.bounds.east, info.bounds.north],
                    "zoom_range": [info.min_zoom, info.max_zoom],
                    "tile_type": info.tile_type,
                }
                for info in tile_source_infos
            ],
        }
        packager.add_content("manifest.json", json.dumps(manifest, indent=2))
        
        packager.finalize()


# ============================================================================
# Scheduled Cleanup (runs daily)
# ============================================================================

@app.function(
    volumes={VOLUME_PATH: volume},
    schedule=modal.Cron("0 0 * * *"),  # Midnight UTC daily
)
def cleanup_old_archives():
    """Remove archives older than 24 hours."""
    import os
    from datetime import datetime, timedelta
    
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


# ============================================================================
# Local Development
# ============================================================================

@app.local_entrypoint()
def main():
    """Local testing entrypoint."""
    # Test the health endpoint
    result = health.remote()
    print(f"Health check: {result}")
    
    # Test with a minimal capture bundle
    test_bundle = {
        "version": "1.0",
        "metadata": {
            "url": "https://example.com/map",
            "title": "Test Map",
            "capturedAt": datetime.utcnow().isoformat() + "Z",
        },
        "viewport": {
            "center": [-74.006, 40.7128],
            "zoom": 12,
        },
    }
    
    print("Testing process endpoint...")
    result = process.remote(test_bundle)
    print(f"Result: {result}")
```

---

## 4. Deployment

### First-time Setup

```bash
cd cli

# Create the Modal volume for archive storage
modal volume create webmap-archiver-outputs

# Deploy the app
modal deploy src/webmap_archiver/modal_app.py
```

### Output

```
✓ Created objects.
├── 🔨 Created process => https://YOUR_USERNAME--webmap-archiver-process.modal.run
├── 🔨 Created download => https://YOUR_USERNAME--webmap-archiver-download.modal.run
├── 🔨 Created health => https://YOUR_USERNAME--webmap-archiver-health.modal.run
└── 🔨 Created cleanup (scheduled)
```

### Update the Download URL

After deployment, update the `downloadUrl` in the `process` function with your actual Modal username:

```python
"downloadUrl": f"https://YOUR_USERNAME--webmap-archiver-download.modal.run/{archive_id}",
```

---

## 5. Local Development

Test locally before deploying:

```bash
# Run locally with hot reload
modal serve src/webmap_archiver/modal_app.py

# This starts a local server at http://localhost:8000
# with the same endpoints as production
```

Test with curl:

```bash
# Health check
curl http://localhost:8000/health

# Process a capture bundle
curl -X POST http://localhost:8000/process \
  -H "Content-Type: application/json" \
  -d '{
    "version": "1.0",
    "metadata": {
      "url": "https://example.com/map",
      "capturedAt": "2024-01-15T10:30:00Z"
    },
    "viewport": {
      "center": [-74.006, 40.7128],
      "zoom": 12
    }
  }'
```

---

## 6. Extension Integration

Update the browser extension to use the Modal endpoint.

### Configuration

Create `extension/src/config.ts`:

```typescript
export const CONFIG = {
  // Production Modal endpoint
  cloudEndpoint: "https://YOUR_USERNAME--webmap-archiver-process.modal.run",
  
  // Local development endpoint (modal serve)
  localDevEndpoint: "http://localhost:8000",
  
  // Local Python service (webmap-archive serve)
  localServiceEndpoint: "http://localhost:8765",
  
  // Processing timeout (5 minutes)
  processingTimeout: 300000,
  
  // Enable local fallback
  enableLocalFallback: true,
};
```

### Service Integration

Update `extension/src/background/service-worker.ts`:

```typescript
import { CONFIG } from "../config";
import { CaptureBundle } from "../types/capture-bundle";

interface ProcessResult {
  success: boolean;
  downloadUrl?: string;
  filename?: string;
  error?: string;
  fallbackToDownload?: boolean;
}

/**
 * Process capture bundle via cloud or local service.
 */
export async function processCapture(bundle: CaptureBundle): Promise<ProcessResult> {
  // Try endpoints in order of preference
  const endpoints = [
    CONFIG.cloudEndpoint,
    ...(CONFIG.enableLocalFallback ? [CONFIG.localServiceEndpoint] : []),
  ];

  for (const endpoint of endpoints) {
    try {
      console.log(`Trying endpoint: ${endpoint}`);
      
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bundle),
        signal: AbortSignal.timeout(CONFIG.processingTimeout),
      });

      if (!response.ok) {
        console.warn(`${endpoint} returned ${response.status}`);
        continue;
      }

      const result = await response.json();

      if (result.success) {
        return {
          success: true,
          downloadUrl: result.downloadUrl,
          filename: result.filename,
        };
      } else {
        console.warn(`${endpoint} processing failed:`, result.error);
        continue;
      }
    } catch (e) {
      console.warn(`${endpoint} request failed:`, e);
      continue;
    }
  }

  // All endpoints failed - fall back to raw file download
  return {
    success: false,
    fallbackToDownload: true,
    error: "Processing services unavailable",
  };
}

/**
 * Handle capture completion - process and download.
 */
export async function handleCaptureComplete(bundle: CaptureBundle): Promise<void> {
  const result = await processCapture(bundle);

  if (result.success && result.downloadUrl) {
    // Download the processed archive
    chrome.downloads.download({
      url: result.downloadUrl,
      filename: result.filename || "webmap-archive.zip",
      saveAs: true,
    });
  } else if (result.fallbackToDownload) {
    // Download raw capture bundle for manual processing
    const filename = generateBundleFilename(bundle);
    downloadBundle(bundle, filename);
    
    // Notify user
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon-48.png",
      title: "WebMap Archiver",
      message: "Cloud processing unavailable. Downloaded capture bundle for manual processing.",
    });
  } else {
    // Show error
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon-48.png",
      title: "WebMap Archiver - Error",
      message: result.error || "Failed to process capture",
    });
  }
}

function generateBundleFilename(bundle: CaptureBundle): string {
  const url = new URL(bundle.metadata.url);
  const host = url.hostname.replace(/\./g, "-");
  const date = bundle.metadata.capturedAt.split("T")[0];
  return `${host}-${date}.webmap-capture.json`;
}

function downloadBundle(bundle: CaptureBundle, filename: string): void {
  const json = JSON.stringify(bundle, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);

  chrome.downloads.download({
    url,
    filename,
    saveAs: true,
  });

  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
```

### Popup Updates

Update `extension/src/popup/popup.ts` to show processing status:

```typescript
async function startCapture(tabId: number): Promise<void> {
  try {
    captureBtn.setAttribute("disabled", "true");
    showProgress("Capturing map...", 20);

    // ... existing capture logic ...
    
    showProgress("Uploading to cloud...", 50);
    
    const result = await chrome.runtime.sendMessage({
      type: "PROCESS_CAPTURE",
      bundle,
    });

    if (result.success) {
      showProgress("Processing...", 70);
      
      // Wait for download to start
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      showProgress("Complete!", 100);
      showComplete(result.filename || "archive.zip");
    } else if (result.fallbackToDownload) {
      showProgress("Downloaded capture bundle", 100);
      showFallbackMessage();
    } else {
      showError(result.error || "Processing failed");
    }
  } catch (e) {
    showError(String(e));
  }
}

function showFallbackMessage(): void {
  // Show instructions for manual processing
  captureComplete.innerHTML = `
    <p class="icon">📦</p>
    <p>Capture bundle downloaded!</p>
    <p class="hint">
      Cloud processing unavailable.<br>
      Process manually with:<br>
      <code>webmap-archive process &lt;file&gt;</code>
    </p>
  `;
  captureComplete.classList.remove("hidden");
}
```

---

## 7. CORS Configuration

Modal automatically handles CORS for `@modal.web_endpoint`, but you can customize if needed:

```python
@app.function(image=image, volumes={VOLUME_PATH: volume}, timeout=300)
@modal.web_endpoint(
    method="POST",
    docs=True,
    # Custom CORS settings
    custom_domains=["webmap-archiver.com"],  # Optional: custom domain
)
def process(bundle: dict) -> dict:
    # Add CORS headers for browser extension
    response = _process_bundle(bundle)
    return response
```

---

## 8. Monitoring & Logs

### View Logs

```bash
# Stream live logs
modal logs webmap-archiver

# View recent logs
modal logs webmap-archiver --since 1h
```

### View Function Stats

```bash
modal app list
modal app stats webmap-archiver
```

### Dashboard

Visit https://modal.com/apps to see:
- Request counts
- Latency metrics  
- Error rates
- Cost breakdown

---

## 9. Cost Estimation

Modal pricing (as of 2024):

| Resource | Free Tier | Cost After |
|----------|-----------|------------|
| Compute | 30 CPU-hours/month | $0.192/CPU-hour |
| Memory | Included | $0.024/GB-hour |
| Storage | 5 GB | $0.20/GB-month |

**Estimated cost per archive:**
- Processing time: ~5 seconds
- Memory: 1 GB
- **Cost: ~$0.0003 per archive** (essentially free)

**Monthly estimate for 1,000 archives:** ~$0.30

---

## 10. Custom Domain (Optional)

Add a custom domain for cleaner URLs:

```bash
# Add custom domain
modal domain add api.webmap-archiver.com

# Update your DNS with the provided CNAME
```

Update the endpoint URLs:
```typescript
cloudEndpoint: "https://api.webmap-archiver.com/process",
```

---

## 11. Production Checklist

Before going live:

- [ ] Deploy to Modal: `modal deploy`
- [ ] Test health endpoint: `curl https://...health.modal.run`
- [ ] Test with real capture bundle
- [ ] Update extension config with production URL
- [ ] Set up monitoring alerts (optional)
- [ ] Document the cloud endpoint in README
- [ ] Consider rate limiting for abuse prevention

---

## 12. Troubleshooting

### "Module not found" errors

Ensure the local directory copy is correct:
```python
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("click>=8.0", "rich>=13.0")
    .copy_local_dir("src/webmap_archiver", "/root/webmap_archiver")
)
```

### Cold start latency

First request after idle may take 2-3 seconds. Mitigate with:
```python
@app.function(
    image=image,
    keep_warm=1,  # Keep 1 instance warm (costs ~$5/month)
)
```

### Large capture bundles

Modal has a 6MB request body limit by default. For larger bundles:
1. Compress the HAR data before sending
2. Or use chunked upload to Modal Volume first

### Volume not persisting

Always call `volume.commit()` after writing:
```python
output_path.write_bytes(archive_data)
volume.commit()  # Required!
```