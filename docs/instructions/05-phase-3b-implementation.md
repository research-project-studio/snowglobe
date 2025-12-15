# WebMap Archiver: Phase 3B - DevTools Capture Implementation

## For Claude Code

This document provides complete instructions for implementing a DevTools-based capture system. This approach replaces the unreliable `chrome.debugger` API (which doesn't fire events to MV3 service workers) with the reliable `chrome.devtools.network` API.

---

## 1. Background & Rationale

### Why DevTools Instead of Debugger API?

The original Phase 3 design used `chrome.debugger.attach()` and `chrome.debugger.onEvent` to capture network traffic. However, in Chrome's Manifest V3 architecture, **debugger events do not reliably fire to service workers**, even when listeners are registered synchronously at the top level.

The `chrome.devtools.network` API works reliably because:
- It runs in a DevTools page context, not a service worker
- It's specifically designed for network inspection
- It has full access to request/response data including bodies

### User Experience Goal

While requiring DevTools to be open isn't ideal, we can minimize friction by:
1. Providing a clear "Open Capture Panel" button in the popup
2. Creating an excellent, intuitive panel UI
3. Automating as much as possible once the panel is open
4. Providing clear visual feedback throughout the process

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         BROWSER                                      │
│                                                                      │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐ │
│  │   Popup      │     │   Service    │     │   DevTools Panel     │ │
│  │              │     │   Worker     │     │   (Primary Capture)  │ │
│  │ • Map status │     │              │     │                      │ │
│  │ • "Open      │────►│ • Badge      │◄───►│ • Start/Stop record  │ │
│  │   Capture"   │     │ • State mgmt │     │ • Live stats         │ │
│  │   button     │     │ • Processing │     │ • HAR capture        │ │
│  │ • Settings   │     │ • Downloads  │     │ • Style extraction   │ │
│  └──────────────┘     └──────────────┘     └──────────────────────┘ │
│                              │                        │              │
│                              │                        │              │
│                              ▼                        ▼              │
│                       ┌──────────────┐     ┌──────────────────────┐ │
│                       │   Content    │     │   Inspected Page     │ │
│                       │   Script     │     │   (Map Page)         │ │
│                       │              │     │                      │ │
│                       │ • Map detect │────►│ • MapLibre/Mapbox    │ │
│                       │ • Style get  │     │ • Tile requests      │ │
│                       └──────────────┘     └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| **Popup** | Show map detection status, provide "Open Capture Panel" button, show capture status |
| **Service Worker** | Manage state, coordinate processing, handle downloads, update badge |
| **DevTools Panel** | Primary capture interface - record network, show stats, trigger processing |
| **Content Script** | Detect maps, extract style/viewport via script injection |

---

## 3. File Structure

```
extension/
├── manifest.json              # Updated with DevTools permissions
├── src/
│   ├── config.ts              # Configuration (unchanged)
│   ├── background/
│   │   └── service-worker.ts  # Simplified - no debugger API
│   ├── content/
│   │   ├── detector.ts        # Map detection (unchanged)
│   │   ├── capturer.ts        # Style/viewport capture (unchanged)
│   │   └── index.ts           # Content script entry (unchanged)
│   ├── popup/
│   │   ├── popup.html         # Updated UI with "Open Capture" button
│   │   ├── popup.ts           # Updated logic
│   │   └── popup.css          # Updated styles
│   ├── devtools/
│   │   ├── devtools.html      # DevTools page (creates panel)
│   │   ├── devtools.ts        # Panel creation
│   │   ├── panel.html         # Main capture UI
│   │   ├── panel.ts           # Capture logic using devtools.network
│   │   └── panel.css          # Panel styles
│   └── types/
│       ├── capture-bundle.ts  # Bundle types (unchanged)
│       └── map-libraries.ts   # Map types (unchanged)
└── icons/
    └── ...
```

---

## 4. Implementation Tasks

### Task 1: Update Manifest

Update `extension/manifest.json` to ensure DevTools permissions are correct:

```json
{
  "manifest_version": 3,
  "name": "WebMap Archiver",
  "version": "0.2.0",
  "description": "Capture web maps for offline archiving",
  "default_locale": "en",

  "permissions": [
    "activeTab",
    "scripting",
    "storage",
    "downloads",
    "notifications"
  ],

  "host_permissions": [
    "<all_urls>"
  ],

  "background": {
    "service_worker": "service-worker.js",
    "type": "module"
  },

  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["content-script.js"],
      "run_at": "document_idle"
    }
  ],

  "action": {
    "default_popup": "popup.html",
    "default_icon": {
      "16": "icons/icon-16.png",
      "48": "icons/icon-48.png",
      "128": "icons/icon-128.png"
    },
    "default_title": "WebMap Archiver"
  },

  "devtools_page": "devtools.html",

  "icons": {
    "16": "icons/icon-16.png",
    "48": "icons/icon-48.png",
    "128": "icons/icon-128.png"
  },

  "web_accessible_resources": [
    {
      "resources": ["icons/*"],
      "matches": ["<all_urls>"]
    }
  ]
}
```

**Note:** Removed `debugger` from permissions since we no longer use it.

---

### Task 2: Simplified Service Worker

Replace `extension/src/background/service-worker.ts` with a simplified version that doesn't use the debugger API:

```typescript
/**
 * Background service worker.
 *
 * Handles:
 * - Badge updates based on map detection
 * - State management for capture sessions
 * - Processing via Modal cloud (primary) or local service (fallback)
 * - File downloads
 * 
 * NOTE: Network capture is handled by DevTools panel using chrome.devtools.network API,
 * NOT by the service worker. The debugger API does not work reliably in MV3 service workers.
 */

import { CaptureBundle, HARLog } from "../types/capture-bundle";
import { CONFIG, getProcessingEndpoints, CaptureState } from "../config";

// Track detected maps per tab
const tabMapState = new Map<number, { count: number; types: string[] }>();

// Track capture state per tab (managed by DevTools panel)
const tabCaptureState = new Map<number, CaptureState>();

console.log("[WebMap Archiver] Service worker initialized");

/**
 * Handle messages from content scripts, popup, and DevTools panel.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id || message.tabId;

  switch (message.type) {
    // === MAP DETECTION ===
    case "MAPS_DETECTED":
      if (tabId) {
        handleMapsDetected(tabId, message.count, message.maps);
      }
      break;

    case "GET_TAB_STATE":
      if (message.tabId) {
        const mapState = tabMapState.get(message.tabId);
        const captureState = tabCaptureState.get(message.tabId) || { status: "idle" };
        sendResponse({
          maps: mapState || { count: 0, types: [] },
          capture: captureState,
        });
      }
      break;

    // === CAPTURE STATE (from DevTools panel) ===
    case "CAPTURE_STARTED":
      if (message.tabId) {
        tabCaptureState.set(message.tabId, {
          status: "recording",
          startedAt: new Date().toISOString(),
          tileCount: 0,
          totalRequests: 0,
          zoomLevels: [],
          estimatedSize: 0,
        });
        updateBadgeForRecording(message.tabId);
        sendResponse({ success: true });
      }
      break;

    case "CAPTURE_STATS_UPDATE":
      if (message.tabId) {
        const state = tabCaptureState.get(message.tabId);
        if (state?.status === "recording") {
          Object.assign(state, message.stats);
        }
      }
      break;

    case "CAPTURE_STOPPED":
      if (message.tabId) {
        tabCaptureState.set(message.tabId, { status: "idle" });
        const mapState = tabMapState.get(message.tabId);
        updateBadgeForMapDetection(message.tabId, mapState?.count || 0);
        sendResponse({ success: true });
      }
      break;

    // === PROCESSING ===
    case "PROCESS_BUNDLE":
      processCapture(message.bundle).then(sendResponse);
      return true; // Async response

    case "DOWNLOAD_BUNDLE":
      downloadBundle(message.bundle, message.filename);
      sendResponse({ success: true });
      break;

    case "DOWNLOAD_FILE":
      chrome.downloads.download({
        url: message.url,
        filename: message.filename,
        saveAs: true,
      });
      sendResponse({ success: true });
      break;

    default:
      break;
  }
});

/**
 * Update badge when maps are detected.
 */
function handleMapsDetected(
  tabId: number,
  count: number,
  maps: Array<{ type: string; version?: string }>
): void {
  tabMapState.set(tabId, {
    count,
    types: maps.map((m) => m.type),
  });

  // Only update badge if not recording
  const captureState = tabCaptureState.get(tabId);
  if (!captureState || captureState.status !== "recording") {
    updateBadgeForMapDetection(tabId, count);
  }
}

function updateBadgeForMapDetection(tabId: number, count: number): void {
  if (count > 0) {
    chrome.action.setBadgeText({ text: count.toString(), tabId });
    chrome.action.setBadgeBackgroundColor({ color: "#4CAF50", tabId });
    chrome.action.setTitle({
      title: `WebMap Archiver (${count} map${count > 1 ? "s" : ""} detected)`,
      tabId,
    });
  } else {
    chrome.action.setBadgeText({ text: "", tabId });
    chrome.action.setTitle({ title: "WebMap Archiver", tabId });
  }
}

function updateBadgeForRecording(tabId: number): void {
  chrome.action.setBadgeText({ text: "REC", tabId });
  chrome.action.setBadgeBackgroundColor({ color: "#f44336", tabId });
  chrome.action.setTitle({ title: "WebMap Archiver (Recording...)", tabId });
}

function updateBadgeForProcessing(tabId: number, progress: number): void {
  const text = progress < 100 ? `${progress}%` : "✓";
  chrome.action.setBadgeText({ text, tabId });
  chrome.action.setBadgeBackgroundColor({ color: "#2196F3", tabId });
}

// ============================================================================
// PROCESSING
// ============================================================================

interface ProcessResult {
  success: boolean;
  downloadUrl?: string;
  filename?: string;
  size?: number;
  error?: string;
  fallbackToDownload?: boolean;
}

async function processCapture(bundle: CaptureBundle): Promise<ProcessResult> {
  const endpoints = getProcessingEndpoints();

  console.log("[WebMap Archiver] Processing bundle with endpoints:", endpoints);
  console.log("[WebMap Archiver] Bundle summary:", {
    tiles: bundle.tiles?.length || 0,
    harEntries: bundle.har?.log?.entries?.length || 0,
    hasStyle: !!bundle.style,
  });

  for (const endpoint of endpoints) {
    try {
      console.log(`[WebMap Archiver] Trying endpoint: ${endpoint}`);

      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bundle),
        signal: AbortSignal.timeout(CONFIG.processingTimeout),
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.warn(`[WebMap Archiver] ${endpoint} returned ${response.status}:`, errorText);
        continue;
      }

      const result = await response.json();
      console.log("[WebMap Archiver] Response:", result);

      if (result.success) {
        // Fix relative download URLs
        let downloadUrl = result.downloadUrl;
        if (downloadUrl && downloadUrl.startsWith("/")) {
          const endpointUrl = new URL(endpoint);
          const baseUrl = `${endpointUrl.protocol}//${endpointUrl.host}`;
          downloadUrl = `${baseUrl}${downloadUrl}`;
          console.log(`[WebMap Archiver] Fixed URL: ${downloadUrl}`);
        }

        return {
          success: true,
          downloadUrl,
          filename: result.filename,
          size: result.size,
        };
      } else {
        console.warn(`[WebMap Archiver] Processing failed:`, result.error);
        continue;
      }
    } catch (e) {
      console.warn(`[WebMap Archiver] Request failed:`, e);
      continue;
    }
  }

  return {
    success: false,
    fallbackToDownload: true,
    error: "Processing services unavailable",
  };
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

// Clean up state when tab is closed
chrome.tabs.onRemoved.addListener((tabId) => {
  tabMapState.delete(tabId);
  tabCaptureState.delete(tabId);
});

console.log("[WebMap Archiver] Service worker ready");
```

---

### Task 3: Updated Popup HTML

Replace `extension/src/popup/popup.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="popup.css">
</head>
<body>
  <div class="container">
    <header>
      <img src="icons/icon-48.png" alt="" class="logo">
      <h1>WebMap Archiver</h1>
    </header>

    <!-- No Map Detected -->
    <div id="no-map" class="state hidden">
      <p class="icon">🗺️</p>
      <p>No map detected on this page.</p>
      <p class="hint">Navigate to a page with a MapLibre, Mapbox, or Leaflet map.</p>
    </div>

    <!-- Map Detected - Ready to Capture -->
    <div id="map-found" class="state hidden">
      <p class="icon">✅</p>
      <p id="map-info">1 map detected (maplibre)</p>
      
      <div class="capture-instructions">
        <p><strong>To capture this map:</strong></p>
        <ol>
          <li>Click the button below to open the capture panel</li>
          <li>Click "Start Recording" in the panel</li>
          <li>Pan and zoom the map to capture tiles</li>
          <li>Click "Stop & Archive" when done</li>
        </ol>
      </div>

      <div class="actions">
        <button id="open-devtools-btn" class="primary">
          🎬 Open Capture Panel
        </button>
      </div>
      
      <p class="hint">
        This will open DevTools with the WebMap Archiver panel.
      </p>
    </div>

    <!-- Recording in Progress -->
    <div id="recording" class="state hidden">
      <p class="icon recording-pulse">🔴</p>
      <p><strong>Recording in progress...</strong></p>
      <p class="hint">Use the DevTools panel to stop recording.</p>
      
      <div class="stats">
        <div class="stat">
          <span class="stat-value" id="tile-count">0</span>
          <span class="stat-label">Tiles</span>
        </div>
        <div class="stat">
          <span class="stat-value" id="request-count">0</span>
          <span class="stat-label">Requests</span>
        </div>
        <div class="stat">
          <span class="stat-value" id="data-size">0 B</span>
          <span class="stat-label">Size</span>
        </div>
      </div>

      <div class="actions">
        <button id="focus-devtools-btn" class="secondary">
          Open DevTools Panel
        </button>
      </div>
    </div>

    <!-- Processing -->
    <div id="processing" class="state hidden">
      <p class="icon">⏳</p>
      <p id="processing-message">Processing...</p>
      <div class="progress-bar">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
      <p class="progress-text" id="progress-text">0%</p>
    </div>

    <!-- Error -->
    <div id="error" class="state hidden">
      <p class="icon">❌</p>
      <p id="error-message">An error occurred.</p>
      <div class="actions">
        <button id="retry-btn" class="secondary">Retry</button>
      </div>
    </div>

    <footer>
      <a href="#" id="help-link">Help</a>
      <span class="separator">•</span>
      <a href="#" id="about-link">About</a>
    </footer>
  </div>

  <script src="popup.js"></script>
</body>
</html>
```

---

### Task 4: Updated Popup CSS

Replace `extension/src/popup/popup.css`:

```css
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  color: #333;
  background: #fff;
  width: 320px;
}

.container {
  padding: 16px;
}

header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e0e0e0;
}

.logo {
  width: 32px;
  height: 32px;
}

header h1 {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
}

/* States */
.state {
  text-align: center;
  padding: 8px 0;
}

.state.hidden {
  display: none;
}

.state .icon {
  font-size: 32px;
  margin-bottom: 8px;
}

.state p {
  margin-bottom: 8px;
}

.hint {
  font-size: 12px;
  color: #666;
}

/* Capture Instructions */
.capture-instructions {
  text-align: left;
  background: #f8f9fa;
  border-radius: 8px;
  padding: 12px;
  margin: 12px 0;
}

.capture-instructions p {
  margin-bottom: 8px;
}

.capture-instructions ol {
  margin-left: 20px;
  font-size: 13px;
}

.capture-instructions li {
  margin-bottom: 4px;
}

/* Buttons */
.actions {
  margin: 16px 0 8px;
}

button {
  display: block;
  width: 100%;
  padding: 12px 16px;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.2s, transform 0.1s;
}

button:hover {
  transform: translateY(-1px);
}

button:active {
  transform: translateY(0);
}

button.primary {
  background: #2563eb;
  color: white;
}

button.primary:hover {
  background: #1d4ed8;
}

button.secondary {
  background: #f1f5f9;
  color: #334155;
  border: 1px solid #e2e8f0;
}

button.secondary:hover {
  background: #e2e8f0;
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

/* Recording State */
.recording-pulse {
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.stats {
  display: flex;
  justify-content: space-around;
  margin: 16px 0;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 8px;
}

.stat {
  text-align: center;
}

.stat-value {
  display: block;
  font-size: 20px;
  font-weight: 600;
  color: #1a1a1a;
}

.stat-label {
  font-size: 11px;
  color: #666;
  text-transform: uppercase;
}

/* Progress Bar */
.progress-bar {
  height: 8px;
  background: #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
  margin: 12px 0;
}

.progress-fill {
  height: 100%;
  background: #2563eb;
  border-radius: 4px;
  transition: width 0.3s ease;
  width: 0%;
}

.progress-text {
  font-size: 12px;
  color: #666;
}

/* Footer */
footer {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #e0e0e0;
  text-align: center;
  font-size: 12px;
}

footer a {
  color: #666;
  text-decoration: none;
}

footer a:hover {
  color: #2563eb;
}

.separator {
  margin: 0 8px;
  color: #ccc;
}
```

---

### Task 5: Updated Popup TypeScript

Replace `extension/src/popup/popup.ts`:

```typescript
/**
 * Popup UI logic.
 *
 * States:
 * - no-map: No map detected on page
 * - map-found: Map detected, show instructions to open DevTools
 * - recording: Recording in progress (managed by DevTools panel)
 * - processing: Building archive
 * - error: Something went wrong
 */

// UI Elements
const noMapState = document.getElementById("no-map")!;
const mapFoundState = document.getElementById("map-found")!;
const recordingState = document.getElementById("recording")!;
const processingState = document.getElementById("processing")!;
const errorState = document.getElementById("error")!;

const mapInfo = document.getElementById("map-info")!;
const openDevtoolsBtn = document.getElementById("open-devtools-btn")!;
const focusDevtoolsBtn = document.getElementById("focus-devtools-btn")!;

const tileCount = document.getElementById("tile-count")!;
const requestCount = document.getElementById("request-count")!;
const dataSize = document.getElementById("data-size")!;

const processingMessage = document.getElementById("processing-message")!;
const progressFill = document.getElementById("progress-fill")!;
const progressText = document.getElementById("progress-text")!;

const errorMessage = document.getElementById("error-message")!;
const retryBtn = document.getElementById("retry-btn")!;

let currentTabId: number | null = null;
let statsInterval: ReturnType<typeof setInterval> | null = null;

/**
 * Initialize popup.
 */
async function init(): Promise<void> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    showError("Cannot access current tab");
    return;
  }
  currentTabId = tab.id;

  // Get current state
  const state = await chrome.runtime.sendMessage({
    type: "GET_TAB_STATE",
    tabId: tab.id,
  });

  // Route to appropriate UI
  if (state.capture?.status === "recording") {
    showRecording(state.capture);
    startStatsPolling();
  } else if (state.capture?.status === "processing") {
    showProcessing(state.capture.progress || 0, state.capture.message || "Processing...");
  } else if (state.maps?.count > 0) {
    showMapFound(state.maps);
  } else {
    showNoMap();
  }

  setupEventHandlers();
}

function setupEventHandlers(): void {
  openDevtoolsBtn.addEventListener("click", handleOpenDevtools);
  focusDevtoolsBtn?.addEventListener("click", handleOpenDevtools);
  retryBtn.addEventListener("click", init);
}

// ============================================================================
// STATE DISPLAY
// ============================================================================

function hideAllStates(): void {
  noMapState.classList.add("hidden");
  mapFoundState.classList.add("hidden");
  recordingState.classList.add("hidden");
  processingState.classList.add("hidden");
  errorState.classList.add("hidden");
}

function showNoMap(): void {
  hideAllStates();
  noMapState.classList.remove("hidden");
}

function showMapFound(info: { count: number; types: string[] }): void {
  hideAllStates();
  mapFoundState.classList.remove("hidden");
  
  const mapTypes = info.types.join(", ");
  mapInfo.textContent = `${info.count} map${info.count > 1 ? "s" : ""} detected (${mapTypes})`;
}

function showRecording(state: { tileCount?: number; totalRequests?: number; estimatedSize?: number }): void {
  hideAllStates();
  recordingState.classList.remove("hidden");
  updateRecordingStats(state);
}

function updateRecordingStats(state: { tileCount?: number; totalRequests?: number; estimatedSize?: number }): void {
  tileCount.textContent = (state.tileCount || 0).toString();
  requestCount.textContent = (state.totalRequests || 0).toString();
  dataSize.textContent = formatBytes(state.estimatedSize || 0);
}

function showProcessing(progress: number, message: string): void {
  hideAllStates();
  processingState.classList.remove("hidden");
  progressFill.style.width = `${progress}%`;
  progressText.textContent = message;
}

function showError(message: string): void {
  hideAllStates();
  errorState.classList.remove("hidden");
  errorMessage.textContent = message;
  stopStatsPolling();
}

// ============================================================================
// EVENT HANDLERS
// ============================================================================

function handleOpenDevtools(): void {
  // Open DevTools for the current tab
  // Note: We can't directly open to our panel, but we can open DevTools
  if (currentTabId) {
    // Send message to open DevTools (will be handled by trying to inspect)
    chrome.tabs.sendMessage(currentTabId, { type: "OPEN_DEVTOOLS_HINT" });
    
    // Show instructions
    alert(
      "DevTools will open. Look for the 'WebMap Archiver' panel tab.\n\n" +
      "If you don't see it, you may need to:\n" +
      "1. Close and reopen DevTools\n" +
      "2. Click the >> arrows to find more panels"
    );
  }
}

// ============================================================================
// STATS POLLING
// ============================================================================

function startStatsPolling(): void {
  stopStatsPolling();
  statsInterval = setInterval(async () => {
    if (!currentTabId) return;
    
    const state = await chrome.runtime.sendMessage({
      type: "GET_TAB_STATE",
      tabId: currentTabId,
    });

    if (state.capture?.status === "recording") {
      updateRecordingStats(state.capture);
    } else if (state.capture?.status === "processing") {
      showProcessing(state.capture.progress || 0, state.capture.message || "Processing...");
    } else {
      stopStatsPolling();
      init(); // Refresh state
    }
  }, 500);
}

function stopStatsPolling(): void {
  if (statsInterval) {
    clearInterval(statsInterval);
    statsInterval = null;
  }
}

// ============================================================================
// HELPERS
// ============================================================================

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// Initialize
document.addEventListener("DOMContentLoaded", init);
window.addEventListener("unload", stopStatsPolling);
```

---

### Task 6: DevTools Page

Keep `extension/src/devtools/devtools.html` simple:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
</head>
<body>
  <script src="devtools.js"></script>
</body>
</html>
```

Update `extension/src/devtools/devtools.ts`:

```typescript
/**
 * DevTools page - creates the WebMap Archiver panel.
 */

chrome.devtools.panels.create(
  "WebMap Archiver",
  "icons/icon-16.png",
  "panel.html",
  (panel) => {
    console.log("[WebMap Archiver] DevTools panel created");
  }
);
```

---

### Task 7: DevTools Panel HTML

Replace `extension/src/devtools/panel.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="panel.css">
</head>
<body>
  <div class="panel-container">
    <header>
      <h1>🗺️ WebMap Archiver</h1>
      <span class="version">v0.2.0</span>
    </header>

    <!-- Idle State -->
    <div id="idle-state" class="state">
      <div class="status-card">
        <div class="status-icon">📍</div>
        <div class="status-text">
          <h2>Ready to Capture</h2>
          <p id="map-status">Checking for maps...</p>
        </div>
      </div>

      <div class="instructions">
        <h3>How to capture:</h3>
        <ol>
          <li>Click <strong>Start Recording</strong> below</li>
          <li>Pan and zoom the map to load tiles for areas you want</li>
          <li>Visit different zoom levels to capture detail</li>
          <li>Click <strong>Stop & Archive</strong> when done</li>
        </ol>
      </div>

      <button id="start-btn" class="primary-btn">
        ▶️ Start Recording
      </button>
    </div>

    <!-- Recording State -->
    <div id="recording-state" class="state hidden">
      <div class="status-card recording">
        <div class="status-icon pulse">🔴</div>
        <div class="status-text">
          <h2>Recording...</h2>
          <p id="recording-duration">0:00</p>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value" id="stat-tiles">0</div>
          <div class="stat-label">Tiles Captured</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="stat-requests">0</div>
          <div class="stat-label">Total Requests</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="stat-size">0 B</div>
          <div class="stat-label">Data Size</div>
        </div>
        <div class="stat-card">
          <div class="stat-value" id="stat-zooms">-</div>
          <div class="stat-label">Zoom Levels</div>
        </div>
      </div>

      <div class="recent-tiles">
        <h3>Recent Tiles</h3>
        <div id="tile-list" class="tile-list">
          <p class="empty-message">Pan or zoom the map to capture tiles...</p>
        </div>
      </div>

      <div class="button-group">
        <button id="stop-btn" class="primary-btn">
          ⏹️ Stop & Archive
        </button>
        <button id="cancel-btn" class="secondary-btn">
          Cancel
        </button>
      </div>
    </div>

    <!-- Processing State -->
    <div id="processing-state" class="state hidden">
      <div class="status-card">
        <div class="status-icon spin">⏳</div>
        <div class="status-text">
          <h2 id="processing-title">Processing...</h2>
          <p id="processing-message">Building capture bundle...</p>
        </div>
      </div>

      <div class="progress-container">
        <div class="progress-bar">
          <div class="progress-fill" id="progress-fill"></div>
        </div>
        <div class="progress-label" id="progress-label">0%</div>
      </div>
    </div>

    <!-- Complete State -->
    <div id="complete-state" class="state hidden">
      <div class="status-card success">
        <div class="status-icon">✅</div>
        <div class="status-text">
          <h2>Archive Complete!</h2>
          <p id="complete-filename">archive.zip</p>
        </div>
      </div>

      <div class="stats-summary">
        <p id="complete-stats">0 tiles • 0 B</p>
      </div>

      <div class="button-group">
        <button id="download-btn" class="primary-btn">
          ⬇️ Download Archive
        </button>
        <button id="download-bundle-btn" class="secondary-btn">
          📦 Download Raw Bundle
        </button>
        <button id="new-capture-btn" class="secondary-btn">
          🔄 New Capture
        </button>
      </div>
    </div>

    <!-- Error State -->
    <div id="error-state" class="state hidden">
      <div class="status-card error">
        <div class="status-icon">❌</div>
        <div class="status-text">
          <h2>Error</h2>
          <p id="error-message">Something went wrong.</p>
        </div>
      </div>

      <div class="button-group">
        <button id="retry-btn" class="primary-btn">
          🔄 Try Again
        </button>
      </div>
    </div>

    <footer>
      <a href="https://github.com/research-project-studio/snowglobe" target="_blank">Documentation</a>
    </footer>
  </div>

  <script src="panel.js"></script>
</body>
</html>
```

---

### Task 8: DevTools Panel CSS

Create `extension/src/devtools/panel.css`:

```css
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  line-height: 1.5;
  color: #333;
  background: #f8f9fa;
}

.panel-container {
  max-width: 500px;
  margin: 0 auto;
  padding: 16px;
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e0e0e0;
}

header h1 {
  font-size: 18px;
  font-weight: 600;
}

.version {
  font-size: 11px;
  color: #888;
  background: #eee;
  padding: 2px 8px;
  border-radius: 10px;
}

/* States */
.state {
  animation: fadeIn 0.2s ease;
}

.state.hidden {
  display: none;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-10px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Status Card */
.status-card {
  display: flex;
  align-items: center;
  gap: 16px;
  background: white;
  padding: 20px;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  margin-bottom: 20px;
}

.status-card.recording {
  background: #fff5f5;
  border: 1px solid #fed7d7;
}

.status-card.success {
  background: #f0fff4;
  border: 1px solid #c6f6d5;
}

.status-card.error {
  background: #fff5f5;
  border: 1px solid #fed7d7;
}

.status-icon {
  font-size: 36px;
  flex-shrink: 0;
}

.status-icon.pulse {
  animation: pulse 1.5s ease-in-out infinite;
}

.status-icon.spin {
  animation: spin 2s linear infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(0.95); }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.status-text h2 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 4px;
}

.status-text p {
  color: #666;
  font-size: 13px;
}

/* Instructions */
.instructions {
  background: white;
  padding: 16px;
  border-radius: 12px;
  margin-bottom: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.instructions h3 {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #555;
}

.instructions ol {
  margin-left: 20px;
}

.instructions li {
  margin-bottom: 8px;
  color: #444;
}

/* Stats Grid */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}

.stat-card {
  background: white;
  padding: 16px;
  border-radius: 10px;
  text-align: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: #2563eb;
}

.stat-label {
  font-size: 11px;
  color: #888;
  text-transform: uppercase;
  margin-top: 4px;
}

/* Recent Tiles */
.recent-tiles {
  background: white;
  padding: 16px;
  border-radius: 12px;
  margin-bottom: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.recent-tiles h3 {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #555;
}

.tile-list {
  max-height: 150px;
  overflow-y: auto;
  font-family: "SF Mono", Monaco, monospace;
  font-size: 11px;
}

.tile-list .tile-item {
  padding: 4px 8px;
  background: #f8f9fa;
  border-radius: 4px;
  margin-bottom: 4px;
  color: #444;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tile-list .tile-item.new {
  animation: highlight 1s ease;
}

@keyframes highlight {
  from { background: #dbeafe; }
  to { background: #f8f9fa; }
}

.empty-message {
  color: #888;
  font-style: italic;
  text-align: center;
  padding: 20px;
}

/* Buttons */
.button-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

button {
  display: block;
  width: 100%;
  padding: 14px 20px;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

button:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

button:active {
  transform: translateY(0);
}

.primary-btn {
  background: #2563eb;
  color: white;
}

.primary-btn:hover {
  background: #1d4ed8;
}

.secondary-btn {
  background: white;
  color: #333;
  border: 1px solid #ddd;
}

.secondary-btn:hover {
  background: #f8f9fa;
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

/* Progress */
.progress-container {
  background: white;
  padding: 20px;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.progress-bar {
  height: 12px;
  background: #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #2563eb, #3b82f6);
  border-radius: 6px;
  transition: width 0.3s ease;
  width: 0%;
}

.progress-label {
  text-align: center;
  margin-top: 10px;
  color: #666;
  font-size: 12px;
}

/* Stats Summary */
.stats-summary {
  text-align: center;
  margin-bottom: 20px;
  color: #666;
}

/* Footer */
footer {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #e0e0e0;
  text-align: center;
}

footer a {
  color: #666;
  text-decoration: none;
  font-size: 12px;
}

footer a:hover {
  color: #2563eb;
}
```

---

### Task 9: DevTools Panel TypeScript (Main Capture Logic)

Replace `extension/src/devtools/panel.ts`:

```typescript
/**
 * DevTools panel - main capture interface.
 *
 * Uses chrome.devtools.network API to capture network traffic reliably.
 * This works because it runs in the DevTools context, not a service worker.
 */

// State
let isRecording = false;
let recordingStartTime: number = 0;
let capturedRequests: CapturedRequest[] = [];
let tileCount = 0;
let totalSize = 0;
let zoomLevels: Set<number> = new Set();
let durationInterval: ReturnType<typeof setInterval> | null = null;
let lastBundle: any = null;
let lastDownloadUrl: string | null = null;
let lastFilename: string | null = null;

// Types
interface CapturedRequest {
  url: string;
  method: string;
  status: number;
  mimeType: string;
  size: number;
  body?: string;
  isTile: boolean;
  tileCoords?: { z: number; x: number; y: number };
  tileSource?: string;
}

// UI Elements
const idleState = document.getElementById("idle-state")!;
const recordingState = document.getElementById("recording-state")!;
const processingState = document.getElementById("processing-state")!;
const completeState = document.getElementById("complete-state")!;
const errorState = document.getElementById("error-state")!;

const mapStatus = document.getElementById("map-status")!;
const startBtn = document.getElementById("start-btn")!;

const recordingDuration = document.getElementById("recording-duration")!;
const statTiles = document.getElementById("stat-tiles")!;
const statRequests = document.getElementById("stat-requests")!;
const statSize = document.getElementById("stat-size")!;
const statZooms = document.getElementById("stat-zooms")!;
const tileList = document.getElementById("tile-list")!;
const stopBtn = document.getElementById("stop-btn")!;
const cancelBtn = document.getElementById("cancel-btn")!;

const processingTitle = document.getElementById("processing-title")!;
const processingMessage = document.getElementById("processing-message")!;
const progressFill = document.getElementById("progress-fill")!;
const progressLabel = document.getElementById("progress-label")!;

const completeFilename = document.getElementById("complete-filename")!;
const completeStats = document.getElementById("complete-stats")!;
const downloadBtn = document.getElementById("download-btn")!;
const downloadBundleBtn = document.getElementById("download-bundle-btn")!;
const newCaptureBtn = document.getElementById("new-capture-btn")!;

const errorMessage = document.getElementById("error-message")!;
const retryBtn = document.getElementById("retry-btn")!;

// ============================================================================
// INITIALIZATION
// ============================================================================

function init(): void {
  setupEventHandlers();
  checkForMaps();
  showState("idle");
}

function setupEventHandlers(): void {
  startBtn.addEventListener("click", startRecording);
  stopBtn.addEventListener("click", stopRecording);
  cancelBtn.addEventListener("click", cancelRecording);
  downloadBtn.addEventListener("click", handleDownload);
  downloadBundleBtn.addEventListener("click", handleDownloadBundle);
  newCaptureBtn.addEventListener("click", resetToIdle);
  retryBtn.addEventListener("click", resetToIdle);
}

async function checkForMaps(): Promise<void> {
  try {
    const result = await sendToContentScript({ type: "GET_MAPS" });
    if (result && result.count > 0) {
      const types = result.maps.map((m: any) => m.type).join(", ");
      mapStatus.textContent = `${result.count} map${result.count > 1 ? "s" : ""} detected (${types})`;
    } else {
      mapStatus.textContent = "No maps detected on this page";
    }
  } catch (e) {
    mapStatus.textContent = "Unable to detect maps";
  }
}

// ============================================================================
// STATE MANAGEMENT
// ============================================================================

function showState(state: "idle" | "recording" | "processing" | "complete" | "error"): void {
  idleState.classList.add("hidden");
  recordingState.classList.add("hidden");
  processingState.classList.add("hidden");
  completeState.classList.add("hidden");
  errorState.classList.add("hidden");

  switch (state) {
    case "idle":
      idleState.classList.remove("hidden");
      break;
    case "recording":
      recordingState.classList.remove("hidden");
      break;
    case "processing":
      processingState.classList.remove("hidden");
      break;
    case "complete":
      completeState.classList.remove("hidden");
      break;
    case "error":
      errorState.classList.remove("hidden");
      break;
  }
}

// ============================================================================
// RECORDING
// ============================================================================

function startRecording(): void {
  console.log("[WebMap Archiver] Starting recording...");

  isRecording = true;
  recordingStartTime = Date.now();
  capturedRequests = [];
  tileCount = 0;
  totalSize = 0;
  zoomLevels.clear();

  // Reset UI
  statTiles.textContent = "0";
  statRequests.textContent = "0";
  statSize.textContent = "0 B";
  statZooms.textContent = "-";
  tileList.innerHTML = '<p class="empty-message">Pan or zoom the map to capture tiles...</p>';

  // Start listening to network requests
  chrome.devtools.network.onRequestFinished.addListener(handleRequest);

  // Start duration timer
  durationInterval = setInterval(updateDuration, 1000);

  // Notify service worker
  chrome.runtime.sendMessage({
    type: "CAPTURE_STARTED",
    tabId: chrome.devtools.inspectedWindow.tabId,
  });

  showState("recording");
  console.log("[WebMap Archiver] Recording started");
}

function handleRequest(request: chrome.devtools.network.Request): void {
  if (!isRecording) return;

  const url = request.request.url;
  const mimeType = request.response.content.mimeType || "";
  const status = request.response.status;
  const size = request.response.content.size || 0;

  console.log(`[WebMap Archiver] Request: ${url.substring(0, 80)}... (${mimeType})`);

  // Parse tile info
  const tileInfo = parseTileUrl(url);

  const captured: CapturedRequest = {
    url,
    method: request.request.method,
    status,
    mimeType,
    size,
    isTile: tileInfo !== null,
    tileCoords: tileInfo?.coords,
    tileSource: tileInfo?.source,
  };

  // Get response body for tiles and important resources
  const shouldGetBody =
    tileInfo !== null ||
    mimeType.includes("json") ||
    mimeType.includes("pbf") ||
    mimeType.includes("protobuf") ||
    url.includes("sprite") ||
    url.includes("glyphs") ||
    url.includes("style");

  if (shouldGetBody && size < 10 * 1024 * 1024) {
    request.getContent((content, encoding) => {
      if (content) {
        captured.body = encoding === "base64" ? content : btoa(content);
      }
      capturedRequests.push(captured);
      updateStats(captured);
    });
  } else {
    capturedRequests.push(captured);
    updateStats(captured);
  }
}

function updateStats(request: CapturedRequest): void {
  // Update total requests
  statRequests.textContent = capturedRequests.length.toString();

  // Update size
  totalSize += request.size;
  statSize.textContent = formatBytes(totalSize);

  // Update tile stats
  if (request.isTile && request.tileCoords) {
    tileCount++;
    statTiles.textContent = tileCount.toString();

    zoomLevels.add(request.tileCoords.z);
    const sortedZooms = Array.from(zoomLevels).sort((a, b) => a - b);
    statZooms.textContent =
      sortedZooms.length > 0
        ? `${sortedZooms[0]}-${sortedZooms[sortedZooms.length - 1]}`
        : "-";

    // Add to tile list
    addTileToList(request);

    console.log(`[WebMap Archiver] ✅ Tile captured: z${request.tileCoords.z}/${request.tileCoords.x}/${request.tileCoords.y}`);
  }

  // Update service worker stats
  chrome.runtime.sendMessage({
    type: "CAPTURE_STATS_UPDATE",
    tabId: chrome.devtools.inspectedWindow.tabId,
    stats: {
      tileCount,
      totalRequests: capturedRequests.length,
      estimatedSize: totalSize,
      zoomLevels: Array.from(zoomLevels),
    },
  });
}

function addTileToList(request: CapturedRequest): void {
  // Remove empty message
  const emptyMsg = tileList.querySelector(".empty-message");
  if (emptyMsg) emptyMsg.remove();

  // Add tile item
  const item = document.createElement("div");
  item.className = "tile-item new";
  item.textContent = `z${request.tileCoords!.z}/${request.tileCoords!.x}/${request.tileCoords!.y} (${request.tileSource})`;

  tileList.insertBefore(item, tileList.firstChild);

  // Keep only last 20 items
  while (tileList.children.length > 20) {
    tileList.removeChild(tileList.lastChild!);
  }
}

function updateDuration(): void {
  const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
  const minutes = Math.floor(elapsed / 60);
  const seconds = elapsed % 60;
  recordingDuration.textContent = `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

async function stopRecording(): Promise<void> {
  console.log("[WebMap Archiver] Stopping recording...");

  isRecording = false;
  chrome.devtools.network.onRequestFinished.removeListener(handleRequest);

  if (durationInterval) {
    clearInterval(durationInterval);
    durationInterval = null;
  }

  showState("processing");
  updateProgress(10, "Extracting map style...");

  try {
    // Get style and viewport from content script
    const styleResult = await sendToContentScript({ type: "CAPTURE_STYLE" });
    const pageInfo = await sendToContentScript({ type: "GET_PAGE_INFO" });

    updateProgress(30, "Building capture bundle...");

    // Build capture bundle
    const bundle = buildCaptureBundle(styleResult, pageInfo);
    lastBundle = bundle;

    console.log("[WebMap Archiver] Bundle built:", {
      tiles: bundle.tiles?.length || 0,
      harEntries: bundle.har?.log?.entries?.length || 0,
      hasStyle: !!bundle.style,
    });

    updateProgress(50, "Uploading to cloud...");

    // Send to service worker for processing
    const result = await chrome.runtime.sendMessage({
      type: "PROCESS_BUNDLE",
      bundle,
    });

    if (result.success) {
      updateProgress(90, "Preparing download...");

      lastDownloadUrl = result.downloadUrl;
      lastFilename = result.filename;

      // Notify service worker
      chrome.runtime.sendMessage({
        type: "CAPTURE_STOPPED",
        tabId: chrome.devtools.inspectedWindow.tabId,
      });

      showComplete(result.filename, bundle.tiles?.length || 0, result.size || 0);
    } else if (result.fallbackToDownload) {
      // Offer raw bundle download
      lastFilename = generateFilename(pageInfo.url);
      
      chrome.runtime.sendMessage({
        type: "CAPTURE_STOPPED",
        tabId: chrome.devtools.inspectedWindow.tabId,
      });

      showComplete(lastFilename, bundle.tiles?.length || 0, totalSize, true);
    } else {
      throw new Error(result.error || "Processing failed");
    }
  } catch (e) {
    console.error("[WebMap Archiver] Error:", e);
    showError(String(e));
  }
}

function cancelRecording(): void {
  isRecording = false;
  chrome.devtools.network.onRequestFinished.removeListener(handleRequest);

  if (durationInterval) {
    clearInterval(durationInterval);
    durationInterval = null;
  }

  chrome.runtime.sendMessage({
    type: "CAPTURE_STOPPED",
    tabId: chrome.devtools.inspectedWindow.tabId,
  });

  resetToIdle();
}

// ============================================================================
// BUNDLE BUILDING
// ============================================================================

function buildCaptureBundle(styleResult: any, pageInfo: { url: string; title: string }): any {
  // Extract tiles with bodies
  const tiles = capturedRequests
    .filter((r) => r.isTile && r.body && r.tileCoords)
    .map((r) => ({
      z: r.tileCoords!.z,
      x: r.tileCoords!.x,
      y: r.tileCoords!.y,
      source: r.tileSource || "tiles",
      data: r.body!,
      format: r.mimeType.includes("png") ? "png" : "pbf",
    }));

  // Build HAR
  const har = {
    log: {
      version: "1.2",
      creator: { name: "WebMap Archiver", version: "0.2.0" },
      entries: capturedRequests.map((r) => ({
        startedDateTime: new Date().toISOString(),
        request: {
          method: r.method,
          url: r.url,
          headers: [],
        },
        response: {
          status: r.status,
          statusText: "OK",
          headers: [],
          content: {
            size: r.size,
            mimeType: r.mimeType,
            text: r.body,
            encoding: r.body ? "base64" : undefined,
          },
        },
        timings: { wait: 0, receive: 0 },
      })),
    },
  };

  return {
    version: "1.0",
    metadata: {
      url: pageInfo.url,
      title: pageInfo.title,
      capturedAt: new Date().toISOString(),
      userAgent: navigator.userAgent,
      mapLibrary: styleResult?.mapLibrary,
      captureStats: {
        totalRequests: capturedRequests.length,
        tileCount: tiles.length,
        zoomLevels: Array.from(zoomLevels).sort((a, b) => a - b),
        estimatedSize: totalSize,
        recordingDuration: Date.now() - recordingStartTime,
      },
    },
    viewport: styleResult?.viewport
      ? {
          center: [styleResult.viewport.center.lng, styleResult.viewport.center.lat],
          zoom: styleResult.viewport.zoom,
          bounds: styleResult.viewport.bounds
            ? [
                [styleResult.viewport.bounds._sw.lng, styleResult.viewport.bounds._sw.lat],
                [styleResult.viewport.bounds._ne.lng, styleResult.viewport.bounds._ne.lat],
              ]
            : undefined,
          bearing: styleResult.viewport.bearing || 0,
          pitch: styleResult.viewport.pitch || 0,
        }
      : { center: [0, 0], zoom: 10 },
    style: styleResult?.style,
    har,
    tiles,
  };
}

// ============================================================================
// UI HELPERS
// ============================================================================

function updateProgress(percent: number, message: string): void {
  progressFill.style.width = `${percent}%`;
  progressLabel.textContent = `${percent}%`;
  processingMessage.textContent = message;
}

function showComplete(filename: string, tiles: number, size: number, isFallback: boolean = false): void {
  completeFilename.textContent = filename;
  completeStats.textContent = `${tiles} tiles • ${formatBytes(size)}`;

  if (isFallback) {
    downloadBtn.textContent = "📦 Download Capture Bundle";
    downloadBtn.onclick = handleDownloadBundle;
    downloadBundleBtn.classList.add("hidden");
  } else {
    downloadBtn.textContent = "⬇️ Download Archive";
    downloadBtn.onclick = handleDownload;
    downloadBundleBtn.classList.remove("hidden");
  }

  showState("complete");
}

function showError(message: string): void {
  errorMessage.textContent = message;
  showState("error");
}

function resetToIdle(): void {
  capturedRequests = [];
  tileCount = 0;
  totalSize = 0;
  zoomLevels.clear();
  lastBundle = null;
  lastDownloadUrl = null;
  lastFilename = null;

  checkForMaps();
  showState("idle");
}

// ============================================================================
// DOWNLOAD HANDLERS
// ============================================================================

function handleDownload(): void {
  if (lastDownloadUrl && lastFilename) {
    chrome.runtime.sendMessage({
      type: "DOWNLOAD_FILE",
      url: lastDownloadUrl,
      filename: lastFilename,
    });
  }
}

function handleDownloadBundle(): void {
  if (lastBundle && lastFilename) {
    const bundleFilename = lastFilename.replace(".zip", ".webmap-capture.json");
    chrome.runtime.sendMessage({
      type: "DOWNLOAD_BUNDLE",
      bundle: lastBundle,
      filename: bundleFilename,
    });
  }
}

// ============================================================================
// UTILITIES
// ============================================================================

function parseTileUrl(url: string): { coords: { z: number; x: number; y: number }; source: string } | null {
  const patterns: Array<{ regex: RegExp; groups: [number, number, number] }> = [
    { regex: /\/(\d+)\/(\d+)\/(\d+)\.(pbf|mvt|png|jpg|jpeg|webp|avif)/, groups: [1, 2, 3] },
    { regex: /\/tiles\/(\d+)\/(\d+)\/(\d+)/, groups: [1, 2, 3] },
    { regex: /[?&]z=(\d+)&x=(\d+)&y=(\d+)/, groups: [1, 2, 3] },
    { regex: /\/(\d{1,2})\/(\d+)\/(\d+)(?:\.|\/|$|\?)/, groups: [1, 2, 3] },
  ];

  for (const { regex, groups } of patterns) {
    const match = url.match(regex);
    if (match) {
      const z = parseInt(match[groups[0]]);
      const x = parseInt(match[groups[1]]);
      const y = parseInt(match[groups[2]]);

      if (z < 0 || z > 22) continue;

      try {
        const urlObj = new URL(url);
        const hostParts = urlObj.hostname.split(".");
        let source = hostParts[0];
        if ((source === "api" || source === "tiles") && hostParts.length > 1) {
          source = hostParts[1];
        }
        return { coords: { z, x, y }, source: source || "tiles" };
      } catch {
        return { coords: { z, x, y }, source: "tiles" };
      }
    }
  }

  return null;
}

function sendToContentScript(message: any): Promise<any> {
  return new Promise((resolve) => {
    chrome.devtools.inspectedWindow.eval(
      `(() => {
        return new Promise((resolve) => {
          chrome.runtime.sendMessage(${JSON.stringify(message)}, resolve);
        });
      })()`,
      (result, error) => {
        if (error) {
          console.error("Content script error:", error);
          resolve(null);
        } else {
          resolve(result);
        }
      }
    );
  });
}

function generateFilename(url: string): string {
  try {
    const urlObj = new URL(url);
    const host = urlObj.hostname.replace(/\./g, "-");
    const date = new Date().toISOString().split("T")[0];
    return `${host}-${date}.webmap-capture.json`;
  } catch {
    return `webmap-${Date.now()}.webmap-capture.json`;
  }
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// Initialize
document.addEventListener("DOMContentLoaded", init);
```

---

### Task 10: Update Webpack Config

Ensure `extension/webpack.config.js` includes the panel CSS:

```javascript
const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');

module.exports = {
  entry: {
    'service-worker': './src/background/service-worker.ts',
    'content-script': './src/content/index.ts',
    'popup': './src/popup/popup.ts',
    'devtools': './src/devtools/devtools.ts',
    'panel': './src/devtools/panel.ts',
  },
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: '[name].js',
    clean: true,
  },
  module: {
    rules: [
      {
        test: /\.tsx?$/,
        use: 'ts-loader',
        exclude: /node_modules/,
      },
      {
        test: /\.css$/,
        use: ['style-loader', 'css-loader'],
      },
    ],
  },
  resolve: {
    extensions: ['.tsx', '.ts', '.js'],
  },
  plugins: [
    new CopyPlugin({
      patterns: [
        { from: 'manifest.json', to: 'manifest.json' },
        { from: 'icons', to: 'icons' },
        { from: '_locales', to: '_locales' },
        { from: 'src/popup/popup.html', to: 'popup.html' },
        { from: 'src/popup/popup.css', to: 'popup.css' },
        { from: 'src/devtools/devtools.html', to: 'devtools.html' },
        { from: 'src/devtools/panel.html', to: 'panel.html' },
        { from: 'src/devtools/panel.css', to: 'panel.css' },
      ],
    }),
  ],
  optimization: {
    splitChunks: false,
  },
};
```

---

## 5. Testing Instructions

### Load the Extension

1. Run `npm install && npm run build` in the extension directory
2. Open `chrome://extensions/`
3. Enable "Developer mode"
4. Click "Load unpacked" and select `extension/dist`

### Test the Flow

1. Navigate to https://parkingregulations.nyc
2. Click the extension icon → should show "1 map detected (maplibre)"
3. Click "Open Capture Panel"
4. Open DevTools (F12 or Cmd+Option+I)
5. Find the "WebMap Archiver" tab/panel (may need to click >> to find it)
6. Click "Start Recording"
7. Pan and zoom the map
8. Watch tiles appear in the list
9. Click "Stop & Archive"
10. Download should start automatically

### Expected Behavior

- Stats update in real-time as you pan/zoom
- Tile list shows recent captures
- Progress bar shows processing status
- Download URL works correctly

---

## 6. Success Criteria

Phase 3B is complete when:

1. ✅ DevTools panel opens and shows map detection status
2. ✅ "Start Recording" begins network capture via `chrome.devtools.network`
3. ✅ Tiles are detected and counted in real-time
4. ✅ Stats update as user pans/zooms
5. ✅ "Stop & Archive" builds bundle with tiles and HAR
6. ✅ Processing via Modal cloud works
7. ✅ Download URL is correctly resolved (not relative)
8. ✅ Fallback to raw bundle download works
9. ✅ Popup shows recording status when capture is in progress
10. ✅ Badge shows "REC" during recording

---

## 7. Future Enhancements

- Add keyboard shortcut to start/stop recording
- Add "capture viewport screenshot" feature
- Add quality/completeness indicators
- Support batch capture of multiple zoom levels
- Add capture presets (e.g., "High Quality", "Fast")