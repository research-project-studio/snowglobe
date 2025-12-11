# WebMap Archiver: Complete Architecture

## End-to-End Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER'S BROWSER                                  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                        Web Page with Map                                │ │
│  │                                                                         │ │
│  │   ┌─────────────────────────────────────────────────────────────────┐  │ │
│  │   │                    MapLibre GL JS Map                            │  │ │
│  │   │                                                                  │  │ │
│  │   │  • Renders vector tiles from multiple sources                    │  │ │
│  │   │  • Has runtime style with colors, filters, etc.                  │  │ │
│  │   │  • Current viewport: center, zoom, bounds                        │  │ │
│  │   │                                                                  │  │ │
│  │   └─────────────────────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                   WebMap Archiver Extension                             │ │
│  │                                                                         │ │
│  │   TWO-STEP CAPTURE FLOW:                                               │ │
│  │                                                                         │ │
│  │   1. DETECT    - Find MapLibre/Mapbox/Leaflet on page                  │ │
│  │   2. START     - User clicks "Start Capture" → begin recording         │ │
│  │   3. RECORD    - Capture all network traffic via chrome.debugger       │ │
│  │   4. INTERACT  - User pans/zooms to capture desired areas              │ │
│  │   5. STOP      - User clicks "Stop & Archive"                          │ │
│  │   6. CAPTURE   - Extract style, viewport, metadata                     │ │
│  │   7. BUNDLE    - Create capture bundle JSON with tiles                 │ │
│  │   8. SEND      - POST to Modal cloud endpoint                          │ │
│  │   9. DOWNLOAD  - Trigger archive download                              │ │
│  │                                                                         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ HTTPS POST
                                      │ (Capture Bundle JSON, ~100KB-10MB)
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MODAL CLOUD                                     │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    webmap-archiver-process                              │ │
│  │                    (Python 3.12 Function)                               │ │
│  │                                                                         │ │
│  │   1. PARSE     - Validate capture bundle                                │ │
│  │   2. EXTRACT   - Get tiles from HAR or pre-extracted data               │ │
│  │   3. BUILD     - Create PMTiles archives                                │ │
│  │   4. GENERATE  - Create viewer.html with embedded style                 │ │
│  │   5. PACKAGE   - Bundle into ZIP archive                                │ │
│  │   6. STORE     - Save to Modal Volume                                   │ │
│  │   7. RETURN    - Send download URL to browser                           │ │
│  │                                                                         │ │
│  │   Processing time: 2-10 seconds                                         │ │
│  │   Memory: 1GB                                                           │ │
│  │   Cost: ~$0.0003 per archive                                            │ │
│  │                                                                         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                      │                                       │
│                                      ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    Modal Volume (Temp Storage)                          │ │
│  │                                                                         │ │
│  │   archives/                                                             │ │
│  │   ├── abc12345.zip  (expires in 24h)                                   │ │
│  │   ├── def67890.zip  (expires in 24h)                                   │ │
│  │   └── ...                                                               │ │
│  │                                                                         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ Download URL
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USER'S COMPUTER                                    │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    Downloaded Archive                                   │ │
│  │                    parkingregulations-nyc-2024-01-15.zip               │ │
│  │                                                                         │ │
│  │   ├── viewer.html              (open in browser)                        │ │
│  │   ├── manifest.json            (archive metadata)                       │ │
│  │   └── tiles/                                                            │ │
│  │       ├── basemap.pmtiles      (MapTiler tiles)                        │ │
│  │       └── parking.pmtiles      (data overlay tiles)                    │ │
│  │                                                                         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  TO VIEW: Extract ZIP → Open viewer.html → Map works offline!              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### 1. Capture Bundle (Extension → Cloud)

```json
{
  "version": "1.0",
  "metadata": {
    "url": "https://parkingregulations.nyc/",
    "title": "NYC Parking Regulations",
    "capturedAt": "2024-01-15T10:30:00Z",
    "mapLibrary": { "type": "maplibre", "version": "4.0.0" }
  },
  "viewport": {
    "center": [-74.006, 40.7128],
    "zoom": 12,
    "bounds": [[-74.1, 40.6], [-73.9, 40.8]],
    "bearing": 0,
    "pitch": 0
  },
  "style": {
    "version": 8,
    "sources": { ... },
    "layers": [ ... ],
    "sprite": "...",
    "glyphs": "..."
  },
  "har": {
    "log": {
      "entries": [
        // All network requests including tiles
      ]
    }
  }
}
```

### 2. Process Response (Cloud → Extension)

```json
{
  "success": true,
  "archiveId": "abc12345",
  "filename": "parkingregulations-nyc-2024-01-15.zip",
  "downloadUrl": "https://YOUR_USERNAME--webmap-archiver-download.modal.run/abc12345",
  "expiresAt": "2024-01-16T10:30:00Z",
  "size": 7654321
}
```

### 3. Archive Contents

```
parkingregulations-nyc-2024-01-15.zip
├── viewer.html           # Self-contained map viewer
├── manifest.json         # Archive metadata
└── tiles/
    ├── maptiler.pmtiles  # Basemap tiles (2.6 MB)
    └── parking.pmtiles   # Data layer tiles (4.7 MB)
```

---

## Component Responsibilities

### Browser Extension (JavaScript/TypeScript)

| Component | Responsibility |
|-----------|----------------|
| **Content Script** | Detect maps, inject capture script, extract style/viewport |
| **Service Worker** | Coordinate capture, manage state, handle downloads |
| **Popup** | User interface, capture button, progress display |
| **DevTools Panel** | Optional HAR capture for advanced users |

### Modal Cloud Function (Python)

| Component | Responsibility |
|-----------|----------------|
| **capture/parser.py** | Parse and validate capture bundles |
| **capture/processor.py** | Transform bundle to intermediate form |
| **tiles/pmtiles.py** | Build PMTiles archives |
| **viewer/generator.py** | Generate HTML viewer |
| **archive/packager.py** | Create ZIP bundle |
| **modal_app.py** | HTTP endpoints, orchestration |

### Archive Viewer (HTML/JavaScript)

| Component | Responsibility |
|-----------|----------------|
| **viewer.html** | Self-contained offline map viewer |
| **PMTiles JS** | Read tiles from PMTiles archives |
| **MapLibre GL JS** | Render map with captured style |

---

## Fallback Chain

The extension tries multiple backends in order:

```
1. Modal Cloud (Primary)
   ├── Success → Download .zip archive
   └── Failure ↓

2. Local Python Service (Optional)
   ├── Running `webmap-archive serve`
   ├── Success → Download .zip archive
   └── Not running ↓

3. Raw Bundle Download (Fallback)
   ├── Download .webmap-capture.json
   └── User processes manually with CLI
```

---

## Offline Capability

### Archive Viewer (viewer.html)

Works completely offline after extraction:
- MapLibre GL JS bundled inline (or via CDN with local fallback)
- PMTiles protocol handler reads local files
- Captured style with all colors, fonts, layers preserved
- No network requests required

### Limitations

- Cannot fetch additional tiles beyond captured area
- Cannot fetch tiles at zoom levels not captured
- External resources (sprites, glyphs) must be bundled or inlined

---

## Security Considerations

### Extension

- Minimal permissions (activeTab, storage, downloads)
- No persistent background access
- Content script isolated from page JavaScript
- Style extraction via injection (page context)

### Cloud Processing

- No authentication stored (API keys stripped from URLs)
- Archives auto-expire after 24 hours
- No user accounts or tracking
- CORS enabled for extension origin only

### Archive

- Self-contained, no external requests
- No executable code (just HTML/JS/JSON)
- Safe to share, email, or upload anywhere

---

## Performance Characteristics

| Stage | Duration | Notes |
|-------|----------|-------|
| Map detection | <100ms | DOM inspection |
| Style capture | <500ms | Script injection |
| HAR collection | 0ms (if using devtools) | Already captured by browser |
| Upload to cloud | 1-5s | Depends on bundle size |
| Cloud processing | 2-10s | PMTiles building is CPU-bound |
| Download | 1-10s | Depends on archive size |
| **Total** | **5-30s** | One-click to archive |

---

## Future Enhancements

### Phase 4: Integrations
- Zotero direct upload
- Are.na block creation
- Custom metadata fields

### Phase 5: Headless Capture
- `webmap-archive capture <URL>`
- Playwright-based automation
- Batch processing from URL list

### Potential Optimizations
- Client-side PMTiles building (eliminate cloud)
- Streaming upload for large captures
- Incremental archive updates
- Tile deduplication across archives