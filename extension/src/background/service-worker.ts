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
        const captureState = tabCaptureState.get(message.tabId) || {
          status: "idle",
        };
        sendResponse({
          maps: mapState || { count: 0, types: [] },
          capture: captureState,
        });
      }
      break;

    case "EXECUTE_CAPTURE_SCRIPT":
      if (message.tabId) {
        // Execute the capture script in the tab
        chrome.scripting
          .executeScript({
            target: { tabId: message.tabId },
            func: () => {
              // This runs in the page context with access to window
              const maps = (window as any).__webmapArchiver?.detectedMaps || [];
              if (maps.length === 0) return null;

              const mapInstance = maps[0]?.instance;
              if (!mapInstance) return null;

              try {
                return {
                  style:
                    typeof mapInstance.getStyle === "function"
                      ? mapInstance.getStyle()
                      : null,
                  viewport: {
                    center: mapInstance.getCenter?.() || { lng: 0, lat: 0 },
                    zoom: mapInstance.getZoom?.() || 10,
                    bounds: mapInstance.getBounds?.() || null,
                    bearing: mapInstance.getBearing?.() || 0,
                    pitch: mapInstance.getPitch?.() || 0,
                  },
                  mapLibrary: maps[0]?.type
                    ? { type: maps[0].type, version: maps[0].version }
                    : null,
                };
              } catch (e) {
                console.error("Error capturing map state:", e);
                return null;
              }
            },
          })
          .then((results) => {
            sendResponse(results?.[0]?.result || null);
          })
          .catch((e) => {
            console.error("Script execution failed:", e);
            sendResponse(null);
          });
        return true; // Async response
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
        console.warn(
          `[WebMap Archiver] ${endpoint} returned ${response.status}:`,
          errorText
        );
        continue;
      }

      const result = await response.json();
      console.log("[WebMap Archiver] Response:", result);

      if (result.success) {
        // Fix relative download URLs
        let downloadUrl = result.downloadUrl;
        if (downloadUrl && downloadUrl.startsWith("/")) {
          const endpointUrl = new URL(endpoint);
          downloadUrl = `${endpointUrl.protocol}//${endpointUrl.host}${downloadUrl}`;
          console.log(`[WebMap Archiver] Fixed download URL: ${downloadUrl}`);
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
