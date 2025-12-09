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

(function() {
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
})();
