# v0.3.2 Implementation: Capture Options UI

## For Claude Code

This release adds:
1. Capture options panel in DevTools UI
2. Pass options to Modal API
3. Default `expand-coverage` to ON
4. Wire options through to CLI archive creation

---

## Part 1: Extension - Options UI

### Task
Add a collapsible options panel to the DevTools panel with capture settings.

### File: `extension/src/devtools/panel.html`

Add an options section to the panel UI. Place it before the capture button or in a logical location:

```html
<!-- Capture Options Panel -->
<div id="options-panel" class="options-panel">
    <div class="options-header" onclick="toggleOptions()">
        <span class="options-toggle">▶</span>
        <span>Capture Options</span>
    </div>
    <div id="options-content" class="options-content collapsed">
        <label class="option-item">
            <input type="checkbox" id="opt-reload-page" checked>
            <span>Reload page on capture start</span>
            <span class="option-hint">Ensures sprites and fonts are captured</span>
        </label>
        
        <label class="option-item">
            <input type="checkbox" id="opt-expand-coverage" checked>
            <span>Expand tile coverage</span>
            <span class="option-hint">Fetch additional zoom levels beyond captured area</span>
        </label>
        
        <label class="option-item">
            <span>Archive mode:</span>
            <select id="opt-archive-mode">
                <option value="standalone" selected>Standalone (viewer only)</option>
                <option value="original">Original (preserve site files)</option>
                <option value="full">Full (viewer + site files)</option>
            </select>
        </label>
    </div>
</div>
```

### File: `extension/src/devtools/panel.css`

Add styles for the options panel:

```css
/* Options Panel */
.options-panel {
    margin: 12px 0;
    border: 1px solid #3a3a4a;
    border-radius: 4px;
    background: #1e1e2e;
}

.options-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    cursor: pointer;
    user-select: none;
    font-weight: 500;
}

.options-header:hover {
    background: #2a2a3a;
}

.options-toggle {
    font-size: 10px;
    transition: transform 0.2s;
}

.options-panel.expanded .options-toggle {
    transform: rotate(90deg);
}

.options-content {
    padding: 12px;
    border-top: 1px solid #3a3a4a;
}

.options-content.collapsed {
    display: none;
}

.option-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-bottom: 12px;
    cursor: pointer;
}

.option-item:last-child {
    margin-bottom: 0;
}

.option-item input[type="checkbox"] {
    margin-right: 8px;
}

.option-item select {
    margin-top: 4px;
    padding: 4px 8px;
    background: #2a2a3a;
    border: 1px solid #4a4a5a;
    border-radius: 4px;
    color: #ffffff;
}

.option-hint {
    font-size: 11px;
    color: #888;
    margin-left: 22px;
}
```

### File: `extension/src/devtools/panel.ts`

Add options handling:

```typescript
// ============================================================
// CAPTURE OPTIONS
// ============================================================

interface CaptureOptions {
    reloadOnStart: boolean;
    expandCoverage: boolean;
    archiveMode: 'standalone' | 'original' | 'full';
}

function getDefaultOptions(): CaptureOptions {
    return {
        reloadOnStart: true,
        expandCoverage: true,
        archiveMode: 'standalone',
    };
}

function getCaptureOptions(): CaptureOptions {
    const defaults = getDefaultOptions();
    
    try {
        const reloadCheckbox = document.getElementById('opt-reload-page') as HTMLInputElement;
        const expandCheckbox = document.getElementById('opt-expand-coverage') as HTMLInputElement;
        const modeSelect = document.getElementById('opt-archive-mode') as HTMLSelectElement;
        
        return {
            reloadOnStart: reloadCheckbox?.checked ?? defaults.reloadOnStart,
            expandCoverage: expandCheckbox?.checked ?? defaults.expandCoverage,
            archiveMode: (modeSelect?.value as CaptureOptions['archiveMode']) ?? defaults.archiveMode,
        };
    } catch (error) {
        console.warn('[WebMap Archiver] Error reading options, using defaults:', error);
        return defaults;
    }
}

function toggleOptions(): void {
    const panel = document.getElementById('options-panel');
    const content = document.getElementById('options-content');
    
    if (panel && content) {
        panel.classList.toggle('expanded');
        content.classList.toggle('collapsed');
    }
}

// Make toggleOptions available globally for onclick handler
(window as any).toggleOptions = toggleOptions;
```

Update `startCapture()` to use options:

```typescript
async function startCapture(): Promise<void> {
    const options = getCaptureOptions();
    
    // Clear previous capture data
    resetCaptureState();
    
    // Reload page if option enabled
    if (options.reloadOnStart) {
        updateStatus('Reloading page to capture all resources...');
        
        try {
            await chrome.devtools.inspectedWindow.reload({
                ignoreCache: true
            });
            
            // Wait for reload to initiate
            await new Promise(resolve => setTimeout(resolve, 500));
        } catch (error) {
            console.error('[WebMap Archiver] Failed to reload page:', error);
        }
    }
    
    // Start capturing
    isCapturing = true;
    updateStatus('Capturing... Pan and zoom the map to capture tiles.');
    updateButtonStates();
}
```

Update `buildCaptureBundle()` to include options:

```typescript
function buildCaptureBundle(): CaptureBundle {
    const options = getCaptureOptions();
    
    const bundle: CaptureBundle = {
        version: '1.0',
        metadata: {
            url: currentPageUrl,
            title: currentPageTitle,
            capturedAt: new Date().toISOString(),
        },
        viewport: {
            center: mapCenter,
            zoom: mapZoom,
            bounds: mapBounds,
        },
        style: capturedStyle,
        tiles: capturedTiles.map(t => ({
            z: t.z,
            x: t.x,
            y: t.y,
            sourceId: t.sourceId,
            url: t.url,
            data: t.data,
            format: t.format,
        })),
        resources: {
            sprites: capturedSprites,
            glyphs: capturedGlyphs,
        },
        // NEW: Include capture options for Modal/CLI
        options: {
            expandCoverage: options.expandCoverage,
            archiveMode: options.archiveMode,
        },
    };
    
    return bundle;
}
```

Update the CaptureBundle interface:

```typescript
interface CaptureBundle {
    version: string;
    metadata: {
        url: string;
        title: string;
        capturedAt: string;
    };
    viewport: {
        center: [number, number];
        zoom: number;
        bounds?: [[number, number], [number, number]];
    };
    style: any | null;
    tiles: CapturedTile[];
    resources: {
        sprites: CapturedResource[];
        glyphs: CapturedResource[];
    };
    // NEW
    options?: {
        expandCoverage?: boolean;
        archiveMode?: 'standalone' | 'original' | 'full';
    };
}
```

---

## Part 2: Modal App - Accept Options

### Task
Update the Modal `/process` endpoint to read options from the bundle and pass them to archive creation.

### File: `cli/src/webmap_archiver/modal_app.py`

Update the `/process` endpoint:

```python
@web_app.post("/process")
async def process(request: Request):
    bundle = await request.json()
    
    # Diagnostic logging
    print(f"[DIAG] Bundle keys: {bundle.keys()}", flush=True)
    
    # Extract options with defaults
    options = bundle.get('options', {})
    expand_coverage = options.get('expandCoverage', True)  # Default ON
    archive_mode = options.get('archiveMode', 'standalone')
    
    print(f"[API] Options - expandCoverage: {expand_coverage}, archiveMode: {archive_mode}", flush=True)
    
    # ... existing tile/style processing ...
    
    # Generate unique ID for this archive
    archive_id = str(uuid.uuid4())[:8]
    output_path = OUTPUT_DIR / f"{archive_id}.zip"
    
    # Import and call the API function
    from webmap_archiver.api import create_archive_from_bundle
    
    try:
        result = create_archive_from_bundle(
            bundle=bundle,
            output_path=output_path,
            expand_coverage=expand_coverage,  # Pass option
            mode=archive_mode,                 # Pass option
            verbose=True,
        )
        
        return {
            "success": True,
            "archiveId": archive_id,
            "downloadUrl": f"/download/{archive_id}",
            "tileCount": result.tile_count,
            "tileSources": [
                {"name": ts.name, "tileCount": ts.tile_count}
                for ts in result.tile_sources
            ],
        }
    except Exception as e:
        print(f"[API] Error creating archive: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}
```

---

## Part 3: CLI API - Accept Options

### Task
Update `create_archive_from_bundle()` to accept and use `expand_coverage` and `mode` parameters.

### File: `cli/src/webmap_archiver/api.py`

Update the function signature and implementation:

```python
def create_archive_from_bundle(
    bundle: dict,
    output_path: Path,
    *,
    name: str | None = None,
    mode: str = "standalone",
    expand_coverage: bool = True,  # NEW - default ON
    verbose: bool = False,
) -> ArchiveResult:
    """
    Create an archive from a capture bundle.
    
    Args:
        bundle: Capture bundle dict (from browser extension or file)
        output_path: Where to write the ZIP archive
        name: Optional archive name (defaults to page title or URL)
        mode: Archive mode - "standalone" (viewer only), "original" (site files), 
              or "full" (both)
        expand_coverage: If True, fetch additional tiles to expand zoom coverage
        verbose: If True, print progress information
        
    Returns:
        ArchiveResult with metadata about the created archive
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
    
    # Step 4: Expand coverage if requested
    if expand_coverage and verbose:
        print("Expand coverage enabled - will fetch additional zoom levels")
    
    # Step 5: Build archive with layer discovery
    if verbose:
        print("Building archive...")
    
    result = _build_archive(
        processed=processed,
        capture=capture,
        output_path=output_path,
        name=name,
        mode=mode,
        expand_coverage=expand_coverage,  # Pass through
        verbose=verbose,
    )
    
    return result
```

Update `_build_archive()` to accept and use `expand_coverage`:

```python
def _build_archive(
    processed,
    capture,
    output_path: Path,
    name: str | None,
    mode: str,
    expand_coverage: bool,  # NEW parameter
    verbose: bool,
) -> ArchiveResult:
    """
    Internal function to build the archive.
    """
    # ... existing setup code ...
    
    # Process each tile source
    for source_name, tiles in processed.tiles_by_source.items():
        if not tiles:
            continue
        
        # ... existing tile processing ...
        
        # Calculate bounds and zoom
        calc = CoverageCalculator()
        coords = [c for c, _ in tiles]
        bounds = calc.calculate_bounds(coords)
        zoom_range = calc.get_zoom_range(coords)
        
        # Expand coverage if enabled
        if expand_coverage:
            # Expand zoom range by 1-2 levels in each direction (within reasonable limits)
            expanded_min = max(0, zoom_range[0] - 1)
            expanded_max = min(18, zoom_range[1] + 2)  # Cap at z18
            
            if verbose:
                print(f"    Expanding coverage: z{zoom_range[0]}-{zoom_range[1]} → z{expanded_min}-{expanded_max}")
            
            # Note: Actual tile fetching for expanded coverage would require
            # network requests to the original tile source. For now, we just
            # record the expanded range in metadata.
            zoom_range = (expanded_min, expanded_max)
        
        # ... rest of existing processing ...
```

**CONFIRMED:** The expand-coverage functionality is **already fully implemented** in the CLI:

- `tiles/fetcher.py` - Async tile fetching with rate limiting
- `CoverageCalculator` class - Calculates expanded bounds and missing tiles
- CLI flags: `--expand-coverage`, `--expand-zoom N`, `--rate-limit N`

For v0.3.2, Claude Code needs to:
1. Find where `expand_coverage` parameter is accepted in the CLI/API
2. Wire it through from Modal's `/process` endpoint
3. Ensure it defaults to `True` when called from Modal
4. The actual tile fetching will happen automatically via existing code

Look for these in the codebase:
- `tiles/fetcher.py` - `TileFetcher` class
- `CoverageCalculator.expand_coverage()` method
- CLI command that accepts `--expand-coverage` flag

---

## Part 4: Update Bundle Spec (Documentation)

### File: `docs/capture-bundle-spec.md` (or equivalent)

Add documentation for the new `options` field:

```markdown
### `options` (optional)

Capture options passed from the extension UI.

```json
{
  "options": {
    "expandCoverage": true,
    "archiveMode": "standalone"
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `expandCoverage` | boolean | `true` | Fetch additional zoom levels beyond captured |
| `archiveMode` | string | `"standalone"` | One of: `standalone`, `original`, `full` |
```

---

## Testing Checklist

### Options UI
- [ ] Options panel renders correctly
- [ ] Clicking header toggles expand/collapse
- [ ] Checkboxes save state during session
- [ ] Select dropdown works
- [ ] Default values are correct (reload: ON, expand: ON, mode: standalone)

### Options Flow
- [ ] Options included in bundle JSON (check Network tab)
- [ ] Modal logs show received options
- [ ] Archive creation uses correct options
- [ ] Verbose output shows "Expand coverage enabled" when ON

### End-to-End
- [ ] Capture with expand coverage ON
- [ ] Capture with expand coverage OFF
- [ ] Verify different archive modes work (if implemented)

---

## Files to Modify

| File | Changes |
|------|---------|
| `extension/src/devtools/panel.html` | Add options panel HTML |
| `extension/src/devtools/panel.css` | Add options panel styles |
| `extension/src/devtools/panel.ts` | Add options handling, update bundle building |
| `cli/src/webmap_archiver/modal_app.py` | Read options from bundle, pass to API |
| `cli/src/webmap_archiver/api.py` | Accept expand_coverage and mode parameters |

---

## Commit Message

```
feat(extension): add capture options UI with expand-coverage default

- Add collapsible options panel to DevTools UI
- Options: reload on start, expand coverage, archive mode
- Default expand-coverage to ON for better archives
- Pass options through Modal to CLI
- Wire options to archive creation

Part of v0.3.2 release
```