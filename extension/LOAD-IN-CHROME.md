# How to Load the Extension in Chrome

## Prerequisites

The extension has already been built. If you need to rebuild:

```bash
npm run build
```

## Loading Steps

1. **Open Chrome Extensions Page**
   - Navigate to `chrome://extensions/`
   - Or: Menu (⋮) → Extensions → Manage Extensions

2. **Enable Developer Mode**
   - Toggle the "Developer mode" switch in the top right corner
   - This enables the "Load unpacked" button

3. **Load the Extension**
   - Click "Load unpacked" button
   - Navigate to: `/Users/marioag/Documents/GitHub/snowglobe/extension/dist`
   - Select the `dist` folder
   - Click "Select" (or "Open")

4. **Verify Installation**
   - Extension should appear in the list
   - Name: "WebMap Archiver"
   - Version: 0.1.0
   - Status: Enabled (toggle should be ON)
   - No errors should be shown

5. **Pin the Extension (Optional)**
   - Click the puzzle piece icon (🧩) in the Chrome toolbar
   - Find "WebMap Archiver" in the list
   - Click the pin icon to pin it to the toolbar

## Testing the Extension

### Quick Test

1. **Navigate to a test site**:
   ```
   https://parkingregulations.nyc
   ```

2. **Check the badge**:
   - Extension icon should show badge "1"
   - This means 1 map detected

3. **Open the popup**:
   - Click the extension icon
   - Should show: "1 map detected (maplibre)"
   - Should show "🔴 Start Capture" button

### Full Capture Test

1. **Start recording**:
   - Click "🔴 Start Capture"
   - Grant debugger permission when prompted:
     ```
     WebMap Archiver wants to debug this browser
     [ Cancel ] [ Allow ]
     ```
   - Click "Allow"

2. **Verify recording started**:
   - Badge should change from "1" to "REC" (red background)
   - Popup should show "Recording..." state
   - Stats should show:
     - Tiles: 0 (initially)
     - Zoom levels: -
     - Data: 0 KB

3. **Capture tiles**:
   - Pan the map around
   - Zoom in/out
   - Watch stats update:
     - Tiles count increases
     - Zoom levels show range (e.g., "13-16")
     - Data size increases

4. **Stop and process**:
   - Click "⏹ Stop & Archive"
   - Watch progress:
     - "Stopping capture..." (10%)
     - "Capturing map style..." (30%)
     - "Processing tiles..." (50%)
     - "Uploading to cloud..." (40-80%)
   - Result:
     - If cloud available: Archive downloads as `.zip`
     - If cloud unavailable: Bundle downloads as `.webmap-capture.json`

## Troubleshooting

### Extension doesn't load

**Error**: "Manifest file is missing or unreadable"
- **Fix**: Make sure you selected the `dist` folder, not `extension` folder
- **Path should be**: `.../snowglobe/extension/dist`

**Error**: "Failed to load extension"
- **Fix**: Run `npm run build` in the extension directory
- Check for build errors in terminal

### Extension loads but has errors

**Error in extension list**: Red "Errors" button
- Click "Errors" to see details
- Check browser console: DevTools → Console
- Common issues:
  - Missing files (rebuild needed)
  - TypeScript errors (fix and rebuild)

### Badge doesn't show on map pages

- Refresh the page (content script runs on page load)
- Check browser console for errors
- Make sure page has a supported map library
- Try different map site (e.g., parkingregulations.nyc)

### Debugger permission denied

If you accidentally click "Cancel" on the debugger permission:
1. Close the permission popup
2. Try "Start Capture" again
3. Click "Allow" this time

Or manually grant permission:
1. Go to `chrome://extensions/`
2. Find "WebMap Archiver"
3. Click "Details"
4. Scroll to "Permissions"
5. Enable "debugger" permission

### Capture fails

**"Failed to start capture"**
- Check if another extension is using debugger
- Close DevTools if open on that tab
- Try refreshing the page and capturing again

**"Failed to stop capture"**
- Network issue during processing
- Check console for errors
- Bundle should still download as fallback

**Processing fails (all endpoints)**
- Expected if Modal cloud not deployed yet
- Expected if no local service running
- Bundle should download for manual processing:
  ```bash
  webmap-archive process downloaded-bundle.webmap-capture.json
  ```

### No maps detected

The extension looks for:
- MapLibre GL JS (class: `.maplibregl-map`)
- Mapbox GL JS (class: `.mapboxgl-map`)
- Leaflet (class: `.leaflet-container`)
- OpenLayers (class: `.ol-viewport`)

If your map isn't detected:
- Check browser console for detection logs
- Map may use different library
- Try opening DevTools and inspecting map container

## Uninstalling

1. Go to `chrome://extensions/`
2. Find "WebMap Archiver"
3. Click "Remove"
4. Confirm removal

## Rebuilding After Changes

If you modify the source code:

```bash
cd /Users/marioag/Documents/GitHub/snowglobe/extension
npm run build
```

Then in Chrome:
1. Go to `chrome://extensions/`
2. Find "WebMap Archiver"
3. Click the refresh icon (🔄) next to the extension
4. Reload any pages with maps

## Development Mode

For active development with auto-rebuild:

```bash
npm run dev
```

This watches for file changes and rebuilds automatically.
Still need to click refresh icon (🔄) in Chrome after each rebuild.

## Next Steps

Once the extension is loaded and tested:
1. Test on multiple map sites
2. Test different map libraries (MapLibre, Mapbox, Leaflet)
3. Test the complete capture → process → view workflow
4. Report any bugs or issues
5. Configure Modal cloud endpoint when ready

## Support

If you encounter issues:
1. Check browser console (F12 → Console)
2. Check extension service worker console:
   - Go to `chrome://extensions/`
   - Find "WebMap Archiver"
   - Click "service worker" link
   - Check console for errors
3. Check `extension/README.md` for more details
4. Check `PHASE-3-IMPLEMENTATION-SUMMARY.md` for implementation details
