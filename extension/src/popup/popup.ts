/**
 * Popup UI logic for two-step capture flow.
 *
 * States:
 * - no-map: No map detected on page
 * - map-found: Map detected, ready to start capture
 * - recording: Actively recording network traffic
 * - processing: Building archive
 * - complete: Archive ready
 * - error: Something went wrong
 */

import { CaptureState } from "../config";

// UI Elements - States
const noMapState = document.getElementById("no-map")!;
const mapFoundState = document.getElementById("map-found")!;
const recordingState = document.getElementById("recording")!;
const processingState = document.getElementById("processing")!;
const completeState = document.getElementById("complete")!;
const errorState = document.getElementById("error")!;

// UI Elements - Map Found
const mapInfo = document.getElementById("map-info")!;
const startCaptureBtn = document.getElementById("start-capture-btn")!;

// UI Elements - Recording
const tileCount = document.getElementById("tile-count")!;
const zoomLevels = document.getElementById("zoom-levels")!;
const dataSize = document.getElementById("data-size")!;
const stopCaptureBtn = document.getElementById("stop-capture-btn")!;
const cancelCaptureBtn = document.getElementById("cancel-capture-btn")!;

// UI Elements - Processing
const processingMessage = document.getElementById("processing-message")!;
const progressFill = document.getElementById("progress-fill")!;
const progressText = document.getElementById("progress-text")!;

// UI Elements - Complete
const filenameEl = document.getElementById("filename")!;
const statsSummary = document.getElementById("stats-summary")!;
const newCaptureBtn = document.getElementById("new-capture-btn")!;

// UI Elements - Error
const errorMessage = document.getElementById("error-message")!;
const retryBtn = document.getElementById("retry-btn")!;

// Current tab ID
let currentTabId: number | null = null;

// Polling interval for recording stats
let statsInterval: ReturnType<typeof setInterval> | null = null;

/**
 * Initialize popup.
 */
async function init(): Promise<void> {
  // Get current tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    showError("Cannot access current tab");
    return;
  }
  currentTabId = tab.id;

  // Get current state from background
  const state = await chrome.runtime.sendMessage({
    type: "GET_TAB_STATE",
    tabId: tab.id,
  });

  // Route to appropriate UI state
  if (state.capture?.status === "recording") {
    showRecording(state.capture);
    startStatsPolling();
  } else if (state.capture?.status === "processing") {
    showProcessing(state.capture.progress, state.capture.message);
  } else if (state.maps?.count > 0) {
    showMapFound(state.maps);
  } else {
    showNoMap();
  }

  // Set up event handlers
  setupEventHandlers();
}

function setupEventHandlers(): void {
  startCaptureBtn.addEventListener("click", handleStartCapture);
  stopCaptureBtn.addEventListener("click", handleStopCapture);
  cancelCaptureBtn.addEventListener("click", handleCancelCapture);
  newCaptureBtn.addEventListener("click", handleNewCapture);
  retryBtn.addEventListener("click", init);
}

// ============================================================================
// STATE DISPLAY FUNCTIONS
// ============================================================================

function hideAllStates(): void {
  noMapState.classList.add("hidden");
  mapFoundState.classList.add("hidden");
  recordingState.classList.add("hidden");
  processingState.classList.add("hidden");
  completeState.classList.add("hidden");
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
  mapInfo.textContent = `${info.count} map${
    info.count > 1 ? "s" : ""
  } detected (${mapTypes})`;
}

function showRecording(state: CaptureState & { status: "recording" }): void {
  hideAllStates();
  recordingState.classList.remove("hidden");

  updateRecordingStats(state);
}

function updateRecordingStats(
  state: CaptureState & { status: "recording" }
): void {
  tileCount.textContent = state.tileCount.toString();
  zoomLevels.textContent =
    state.zoomLevels.length > 0
      ? `${Math.min(...state.zoomLevels)}-${Math.max(...state.zoomLevels)}`
      : "-";
  dataSize.textContent = formatBytes(state.estimatedSize);
}

function showProcessing(progress: number, message: string): void {
  hideAllStates();
  processingState.classList.remove("hidden");

  progressFill.style.width = `${progress}%`;
  progressText.textContent = message;
}

function showComplete(
  filename: string,
  stats?: { tiles: number; size: number }
): void {
  hideAllStates();
  completeState.classList.remove("hidden");

  filenameEl.textContent = filename;
  if (stats) {
    statsSummary.textContent = `${stats.tiles} tiles • ${formatBytes(
      stats.size
    )}`;
  }
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

/**
 * Request debugger permission if needed.
 * MUST be called from user gesture context (like button click in popup).
 */
async function ensureDebuggerPermission(): Promise<boolean> {
  try {
    // Check if we already have permission
    const hasPermission = await chrome.permissions.contains({
      permissions: ["debugger"],
    });

    if (hasPermission) {
      return true;
    }

    // Request permission - this works because we're in a click handler (user gesture)
    const granted = await chrome.permissions.request({
      permissions: ["debugger"],
    });

    return granted;
  } catch (e) {
    console.error("[WebMap Archiver] Permission request failed:", e);
    return false;
  }
}

async function handleStartCapture(): Promise<void> {
  if (!currentTabId) return;

  startCaptureBtn.setAttribute("disabled", "true");

  // Request debugger permission HERE in the popup (user gesture context)
  const hasPermission = await ensureDebuggerPermission();
  if (!hasPermission) {
    showError(
      "Debugger permission is required to capture map tiles. Please allow when prompted."
    );
    startCaptureBtn.removeAttribute("disabled");
    return;
  }

  // Now tell the service worker to start capture (it will just verify, not request)
  const result = await chrome.runtime.sendMessage({
    type: "START_CAPTURE",
    tabId: currentTabId,
  });

  if (result.success) {
    showRecording({
      status: "recording",
      startedAt: new Date().toISOString(),
      tileCount: 0,
      totalRequests: 0,
      zoomLevels: [],
      estimatedSize: 0,
    });
    startStatsPolling();
  } else {
    showError(result.error || "Failed to start capture");
  }

  startCaptureBtn.removeAttribute("disabled");
}

async function handleStopCapture(): Promise<void> {
  if (!currentTabId) return;

  stopCaptureBtn.setAttribute("disabled", "true");
  stopStatsPolling();

  showProcessing(10, "Stopping capture...");

  // Stop capture and get bundle
  const stopResult = await chrome.runtime.sendMessage({
    type: "STOP_CAPTURE",
    tabId: currentTabId,
  });

  if (!stopResult.success) {
    showError(stopResult.error || "Failed to stop capture");
    return;
  }

  // Process the bundle
  showProcessing(40, "Uploading to cloud...");

  const processResult = await chrome.runtime.sendMessage({
    type: "PROCESS_BUNDLE",
    bundle: stopResult.bundle,
  });

  if (processResult.success) {
    showProcessing(80, "Downloading archive...");

    // Trigger download
    chrome.downloads.download({
      url: processResult.downloadUrl,
      filename: processResult.filename,
      saveAs: true,
    });

    await new Promise((resolve) => setTimeout(resolve, 500));

    showComplete(processResult.filename, {
      tiles: stopResult.bundle.tiles?.length || 0,
      size: processResult.size || 0,
    });
  } else if (processResult.fallbackToDownload) {
    // Download raw bundle
    showProcessing(80, "Downloading capture bundle...");

    const filename = generateFilename(stopResult.bundle);
    await chrome.runtime.sendMessage({
      type: "DOWNLOAD_BUNDLE",
      bundle: stopResult.bundle,
      filename,
    });

    showFallback(filename);
  } else {
    showError(processResult.error || "Processing failed");
  }
}

async function handleCancelCapture(): Promise<void> {
  if (!currentTabId) return;

  stopStatsPolling();

  await chrome.runtime.sendMessage({
    type: "CANCEL_CAPTURE",
    tabId: currentTabId,
  });

  // Return to map-found state
  const state = await chrome.runtime.sendMessage({
    type: "GET_TAB_STATE",
    tabId: currentTabId,
  });

  if (state.maps?.count > 0) {
    showMapFound(state.maps);
  } else {
    showNoMap();
  }
}

function handleNewCapture(): void {
  init();
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
    } else {
      stopStatsPolling();
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

function showFallback(filename: string): void {
  hideAllStates();
  completeState.classList.remove("hidden");

  filenameEl.textContent = filename;
  completeState.innerHTML = `
    <p class="icon">📦</p>
    <p>Capture bundle downloaded!</p>
    <p class="filename">${filename}</p>
    <p class="hint" style="margin-top: 8px;">
      Cloud processing unavailable.<br>
      Process manually with CLI:<br>
      <code style="background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 11px;">
        webmap-archive process &lt;file&gt;
      </code>
    </p>
    <div class="actions">
      <button id="new-capture-btn" class="secondary">📸 New Capture</button>
    </div>
  `;

  // Re-attach event handler
  document
    .getElementById("new-capture-btn")
    ?.addEventListener("click", handleNewCapture);
}

function generateFilename(bundle: any): string {
  const url = new URL(bundle.metadata.url);
  const host = url.hostname.replace(/\./g, "-");
  const date = bundle.metadata.capturedAt.split("T")[0];
  return `${host}-${date}.webmap-capture.json`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

// Initialize on load
document.addEventListener("DOMContentLoaded", init);

// Clean up on unload
window.addEventListener("unload", () => {
  stopStatsPolling();
});
