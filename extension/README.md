# WebMap Archiver Browser Extension

Chrome extension for capturing interactive web maps (MapLibre, Mapbox, Leaflet, OpenLayers) for offline archiving.

## Features

- **Automatic Map Detection**: Detects maps on any webpage
- **Two-Step Capture Flow**:
  1. Start recording → pan/zoom to capture desired areas
  2. Stop & Archive → get a complete offline archive
- **Network Interception**: Captures actual tile data via Chrome Debugger API
- **Cloud Processing**: Archives processed via Modal cloud (with local fallback)
- **Multiple Output Options**:
  - `.zip` archive (ready to use)
  - `.webmap-capture.json` bundle (for manual CLI processing)

## Installation

### Development

1. Install dependencies:
   ```bash
   npm install
   ```

2. Build the extension:
   ```bash
   npm run build
   ```

3. Load in Chrome:
   - Open `chrome://extensions/`
   - Enable "Developer mode"
   - Click "Load unpacked"
   - Select the `dist/` directory

### Production

(Extension will be published to Chrome Web Store after testing)

## Usage

1. **Navigate** to a page with a web map
2. **Click** the extension icon (badge will show map count)
3. **Start Capture** to begin recording
4. **Pan and zoom** the map to capture desired areas
5. **Stop & Archive** when done
6. **Download** the archive automatically

## Supported Map Libraries

- ✅ MapLibre GL JS
- ✅ Mapbox GL JS
- ✅ Leaflet
- ✅ OpenLayers
- ⚠️ Unknown maps (limited support)

## Architecture

### Components

- **Content Script** (`content/`): Detects maps and captures style/viewport
- **Background Service Worker** (`background/`): Handles recording via Debugger API
- **Popup UI** (`popup/`): User interface for capture flow
- **DevTools Panel** (`devtools/`): Optional advanced capture (placeholder)

### Capture Flow

```
User clicks "Start Capture"
  ↓
Extension requests debugger permission
  ↓
Network traffic recorded (tiles, styles, resources)
  ↓
User pans/zooms map (tiles captured)
  ↓
User clicks "Stop & Archive"
  ↓
Extension captures final style + viewport
  ↓
Builds capture bundle (JSON)
  ↓
Sends to processing endpoint:
  1. Modal cloud (primary)
  2. Local service (fallback)
  3. Local dev (fallback)
  ↓
Returns .zip archive or raw bundle
```

## Configuration

Edit `src/config.ts` to configure endpoints:

```typescript
export const CONFIG = {
  cloudEndpoint: "https://YOUR_USERNAME--webmap-archiver-process.modal.run",
  localServiceEndpoint: "http://localhost:8765",
  localDevEndpoint: "http://localhost:8000",
  enableLocalFallback: true,
};
```

## Development

### Build Commands

```bash
# Development build with watch
npm run dev

# Production build
npm run build

# Type checking
npm run typecheck

# Linting
npm run lint

# Clean dist/
npm run clean
```

### File Structure

```
extension/
├── manifest.json              # Extension manifest (V3)
├── src/
│   ├── config.ts             # API endpoints configuration
│   ├── background/
│   │   └── service-worker.ts # Network capture & processing
│   ├── content/
│   │   ├── detector.ts       # Map detection logic
│   │   ├── capturer.ts       # Style/viewport capture
│   │   └── index.ts          # Content script entry
│   ├── popup/
│   │   ├── popup.html        # Popup UI
│   │   ├── popup.css         # Popup styles
│   │   └── popup.ts          # Popup logic
│   ├── devtools/             # DevTools panel (placeholder)
│   └── types/
│       ├── capture-bundle.ts # Capture bundle types
│       └── map-libraries.ts  # Map library types
├── icons/                    # Extension icons
└── _locales/                 # Internationalization

```

## Testing

### Manual Testing

1. **Map Detection**:
   - Visit https://parkingregulations.nyc
   - Badge should show "1"
   - Popup should show "1 map detected (maplibre)"

2. **Capture Flow**:
   - Click "Start Capture"
   - Permission prompt for debugger access
   - Badge changes to "REC"
   - Pan/zoom map
   - Stats update in popup (tiles, zoom levels, size)
   - Click "Stop & Archive"
   - Archive downloads

3. **Fallback**:
   - Disable network
   - Capture should fall back to bundle download
   - Message shows CLI command for manual processing

### Automated Testing

(To be implemented)

## Permissions

The extension requires the following permissions:

- `activeTab`: Access current tab content
- `scripting`: Inject content scripts
- `storage`: Save configuration
- `downloads`: Trigger file downloads
- `notifications`: Show capture status
- `debugger` (optional): Capture network traffic for tiles
- `<all_urls>` (host): Access maps on any domain

## Troubleshooting

### Extension doesn't detect map

- Check if map library is supported
- Try reloading the page
- Check browser console for errors

### Capture fails

- Grant debugger permission when prompted
- Check if page uses authentication
- Try manual bundle download (fallback)

### Processing fails

- Check Modal endpoint configuration
- Try local service fallback
- Use CLI to process bundle manually

### Can't load extension

- Check `dist/` directory exists
- Run `npm run build`
- Check for TypeScript errors

## Integration with CLI

Captured bundles can be processed with the Python CLI:

```bash
# Install CLI
cd ../cli
pip install -e .

# Process captured bundle
webmap-archive process capture.webmap-capture.json -o archive.zip

# Inspect bundle
webmap-archive inspect capture.webmap-capture.json
```

## Related Documentation

- [Capture Bundle Specification](../docs/capture-bundle-spec.md)
- [Modal Deployment Guide](../modal-deployment-guide.md)
- [Architecture Overview](../architecture-overview.md)

## Implementation Status

### ✅ Implemented

- [x] Project setup (package.json, tsconfig, webpack)
- [x] Manifest V3 configuration
- [x] Map detection (MapLibre, Mapbox, Leaflet, OpenLayers)
- [x] Style/viewport capture
- [x] Content script
- [x] Background service worker with debugger API
- [x] Two-step capture flow (Start → Stop)
- [x] Network traffic capture
- [x] Tile extraction from requests
- [x] HAR building
- [x] Capture bundle creation
- [x] Processing endpoint integration
- [x] Fallback chain (cloud → local → bundle)
- [x] Popup UI with all states
- [x] Live stats during recording
- [x] Download handling
- [x] Badge updates
- [x] Error handling

### ⏳ To Be Implemented (Optional)

- [ ] DevTools panel (full implementation)
- [ ] Firefox support (WebExtensions polyfill)
- [ ] Safari Web Extension support
- [ ] Settings page
- [ ] Offline queue
- [ ] Zotero integration (Phase 4)
- [ ] Are.na integration (Phase 4)

## License

(To be determined)

## Contributing

(To be determined)
