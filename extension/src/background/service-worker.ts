/**
 * Background service worker.
 *
 * Handles:
 * - Badge updates based on map detection
 * - Two-step capture flow: Start Recording → Stop & Archive
 * - Network capture via chrome.debugger API
 * - Processing via Modal cloud (primary) or local service (fallback)
 * - File downloads
 */

import { CaptureBundle, HARLog, HAREntry } from "../types/capture-bundle";
import {
  CONFIG,
  getProcessingEndpoints,
  CaptureState,
  CapturedRequest,
} from "../config";

// Track detected maps per tab
const tabMapState = new Map<number, { count: number; types: string[] }>();

// Track capture state per tab
const tabCaptureState = new Map<number, CaptureState>();

// Track captured requests during recording
const tabCapturedRequests = new Map<number, CapturedRequest[]>();

// Track pending response bodies (debugger returns body separately)
const pendingBodies = new Map<string, { tabId: number; requestId: string }>();

// Track whether debugger listener has been set up
let debuggerListenerAttached = false;

/**
 * Set up debugger event listener if debugger API is available.
 * This is called lazily when needed, since debugger is an optional permission.
 */
function ensureDebuggerListener(): void {
  if (debuggerListenerAttached) {
    console.log("[WebMap Archiver] Debugger listener already attached");
    return;
  }

  if (typeof chrome.debugger === "undefined") {
    console.error("[WebMap Archiver] chrome.debugger is undefined!");
    return;
  }

  console.log("[WebMap Archiver] Attaching debugger event listener...");

  chrome.debugger.onEvent.addListener((source, method, params) => {
    const tabId = source.tabId;
    if (!tabId) return;

    const state = tabCaptureState.get(tabId);
    if (!state || state.status !== "recording") return;

    // Log all network events for debugging
    if (method.startsWith("Network.")) {
      console.log(`[WebMap Archiver] 📡 Debugger event: ${method}`);
    }

    switch (method) {
      case "Network.responseReceived":
        handleResponseReceived(tabId, params as NetworkResponseParams);
        break;
      case "Network.loadingFinished":
        handleLoadingFinished(tabId, params as NetworkLoadingFinishedParams);
        break;
    }
  });

  // Also listen for detach events
  chrome.debugger.onDetach.addListener((source, reason) => {
    console.log(
      `[WebMap Archiver] Debugger detached from tab ${source.tabId}: ${reason}`
    );
  });

  debuggerListenerAttached = true;
  console.log(
    "[WebMap Archiver] ✅ Debugger event listener attached successfully"
  );
}

/**
 * Handle messages from content scripts and popup.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id || message.tabId;

  switch (message.type) {
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

    case "START_CAPTURE":
      if (message.tabId) {
        startCapture(message.tabId).then(sendResponse);
      }
      return true; // Async response

    case "STOP_CAPTURE":
      if (message.tabId) {
        stopCapture(message.tabId).then(sendResponse);
      }
      return true; // Async response

    case "CANCEL_CAPTURE":
      if (message.tabId) {
        cancelCapture(message.tabId);
        sendResponse({ success: true });
      }
      break;

    case "PROCESS_BUNDLE":
      processCapture(message.bundle).then(sendResponse);
      return true;

    case "DOWNLOAD_BUNDLE":
      downloadBundle(message.bundle, message.filename);
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

// ============================================================================
// TWO-STEP CAPTURE FLOW
// ============================================================================

/**
 * Start recording network traffic for a tab.
 *
 * Note: The debugger permission flow in Chrome is handled by chrome.debugger.attach()
 * itself - it will show a yellow banner asking the user to allow debugging.
 */
async function startCapture(
  tabId: number
): Promise<{ success: boolean; error?: string }> {
  try {
    // Ensure the debugger listener is set up
    ensureDebuggerListener();

    // Initialize capture state
    tabCaptureState.set(tabId, {
      status: "recording",
      startedAt: new Date().toISOString(),
      tileCount: 0,
      totalRequests: 0,
      zoomLevels: [],
      estimatedSize: 0,
    });
    tabCapturedRequests.set(tabId, []);

    // Attach debugger to tab
    await chrome.debugger.attach({ tabId }, "1.3");
    console.log(`[WebMap Archiver] Debugger attached to tab ${tabId}`);

    // Enable network capture
    await chrome.debugger.sendCommand({ tabId }, "Network.enable", {
      maxResourceBufferSize: 100 * 1024 * 1024, // 100MB buffer
      maxTotalBufferSize: 200 * 1024 * 1024, // 200MB total
    });
    console.log(`[WebMap Archiver] Network capture enabled for tab ${tabId}`);

    // Update badge to show recording
    chrome.action.setBadgeText({ text: "REC", tabId });
    chrome.action.setBadgeBackgroundColor({ color: "#f44336", tabId }); // Red

    console.log(`[WebMap Archiver] Started recording for tab ${tabId}`);
    return { success: true };
  } catch (e) {
    const errorMsg = String(e);
    console.error("[WebMap Archiver] Failed to start capture:", e);

    // Provide a more helpful error message
    let userMessage = errorMsg;
    if (errorMsg.includes("Cannot access") || errorMsg.includes("permission")) {
      userMessage =
        "Cannot attach debugger. Try refreshing the page and trying again.";
    } else if (errorMsg.includes("Another debugger")) {
      userMessage =
        "Another debugger is already attached. Close DevTools and try again.";
    }

    tabCaptureState.set(tabId, { status: "error", message: userMessage });
    return { success: false, error: userMessage };
  }
}

/**
 * Stop recording and build capture bundle.
 */
async function stopCapture(
  tabId: number
): Promise<{ success: boolean; bundle?: CaptureBundle; error?: string }> {
  const state = tabCaptureState.get(tabId);
  if (!state || state.status !== "recording") {
    return { success: false, error: "Not recording" };
  }

  console.log(`[WebMap Archiver] Stopping capture for tab ${tabId}`);
  console.log(`[WebMap Archiver] Recording state:`, state);

  try {
    // Detach debugger
    await chrome.debugger.detach({ tabId });
    console.log(`[WebMap Archiver] Debugger detached`);

    // Update state to processing
    tabCaptureState.set(tabId, {
      status: "processing",
      progress: 10,
      message: "Building capture bundle...",
    });
    updateBadgeForProcessing(tabId, 10);

    // Get captured requests
    const requests = tabCapturedRequests.get(tabId) || [];
    console.log(
      `[WebMap Archiver] Total captured requests: ${requests.length}`
    );
    console.log(
      `[WebMap Archiver] Tile requests: ${
        requests.filter((r) => r.isTile).length
      }`
    );
    console.log(
      `[WebMap Archiver] Requests with body: ${
        requests.filter((r) => r.responseBody).length
      }`
    );

    // Get style and viewport from content script
    tabCaptureState.set(tabId, {
      status: "processing",
      progress: 30,
      message: "Capturing map style...",
    });
    updateBadgeForProcessing(tabId, 30);

    const styleResult = await chrome.tabs.sendMessage(tabId, {
      type: "CAPTURE_STYLE",
    });
    console.log(
      `[WebMap Archiver] Style result:`,
      styleResult ? "received" : "null"
    );

    const pageInfo = await chrome.tabs.sendMessage(tabId, {
      type: "GET_PAGE_INFO",
    });
    console.log(`[WebMap Archiver] Page info:`, pageInfo);

    // Build capture bundle
    tabCaptureState.set(tabId, {
      status: "processing",
      progress: 50,
      message: "Processing tiles...",
    });
    updateBadgeForProcessing(tabId, 50);

    const bundle = buildCaptureBundle(requests, styleResult, pageInfo, state);

    // Clean up
    tabCapturedRequests.delete(tabId);

    console.log(
      `[WebMap Archiver] ✅ Capture complete: ${
        bundle.tiles?.length || 0
      } tiles`
    );
    return { success: true, bundle };
  } catch (e) {
    console.error("[WebMap Archiver] Failed to stop capture:", e);
    tabCaptureState.set(tabId, { status: "error", message: String(e) });
    return { success: false, error: String(e) };
  }
}

/**
 * Cancel recording without processing.
 */
async function cancelCapture(tabId: number): Promise<void> {
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    // May already be detached
  }

  tabCaptureState.set(tabId, { status: "idle" });
  tabCapturedRequests.delete(tabId);

  // Restore map detection badge
  const mapState = tabMapState.get(tabId);
  updateBadgeForMapDetection(tabId, mapState?.count || 0);
}

function updateBadgeForProcessing(tabId: number, progress: number): void {
  const text = progress < 100 ? `${progress}%` : "✓";
  chrome.action.setBadgeText({ text, tabId });
  chrome.action.setBadgeBackgroundColor({ color: "#2196F3", tabId }); // Blue
}

// ============================================================================
// DEBUGGER EVENT HANDLERS
// ============================================================================

interface NetworkResponseParams {
  requestId: string;
  response: {
    url: string;
    status: number;
    mimeType: string;
  };
}

interface NetworkLoadingFinishedParams {
  requestId: string;
  encodedDataLength: number;
}

/**
 * Handle Network.responseReceived event.
 */
function handleResponseReceived(
  tabId: number,
  params: NetworkResponseParams
): void {
  const { requestId, response } = params;
  const { url, status, mimeType } = response;

  // Log all requests for debugging
  console.log(
    `[WebMap Archiver] Request: ${url.substring(0, 100)} (${mimeType})`
  );

  // Parse tile coordinates if this is a tile request
  const tileInfo = parseTileUrl(url);

  // Debug logging for tile-like URLs
  if (
    mimeType.includes("pbf") ||
    mimeType.includes("mvt") ||
    mimeType.includes("protobuf") ||
    mimeType.includes("octet-stream") ||
    mimeType.includes("png") ||
    mimeType.includes("jpeg") ||
    url.includes("/tiles/") ||
    /\/\d+\/\d+\/\d+/.test(url)
  ) {
    console.log(
      `[WebMap Archiver] 🗺️ Potential tile: ${url.substring(0, 120)}`,
      {
        mimeType,
        status,
        detected: tileInfo !== null,
        tileInfo,
      }
    );
  }

  const request: CapturedRequest = {
    requestId, // Store for matching in loadingFinished
    url,
    method: "GET",
    status,
    mimeType,
    responseSize: 0,
    timestamp: Date.now(),
    isTile: tileInfo !== null,
    tileCoords: tileInfo?.coords,
    tileSource: tileInfo?.source,
  };

  const requests = tabCapturedRequests.get(tabId) || [];
  requests.push(request);
  tabCapturedRequests.set(tabId, requests);

  // Track for body retrieval
  pendingBodies.set(requestId, { tabId, requestId });

  // Update stats
  const state = tabCaptureState.get(tabId);
  if (state?.status === "recording") {
    state.totalRequests++;
    if (tileInfo) {
      state.tileCount++;
      console.log(
        `[WebMap Archiver] ✅ Tile detected! Count: ${state.tileCount}, z=${tileInfo.coords.z}`
      );
      if (!state.zoomLevels.includes(tileInfo.coords.z)) {
        state.zoomLevels.push(tileInfo.coords.z);
        state.zoomLevels.sort((a, b) => a - b);
      }
    }
  }
}

/**
 * Handle Network.loadingFinished event.
 */
async function handleLoadingFinished(
  tabId: number,
  params: NetworkLoadingFinishedParams
): Promise<void> {
  const { requestId, encodedDataLength } = params;

  const pending = pendingBodies.get(requestId);
  if (!pending) {
    return;
  }
  pendingBodies.delete(requestId);

  const requests = tabCapturedRequests.get(tabId);
  if (!requests) return;

  // Find the request by matching the requestId we stored
  // We stored the URL at the time, find that request
  const request = requests.find((r) => r.requestId === requestId);
  if (!request) {
    console.debug(
      `[WebMap Archiver] No matching request found for requestId ${requestId}`
    );
    return;
  }

  request.responseSize = encodedDataLength;

  // Only fetch body for tiles and important resources
  const shouldFetchBody =
    request.isTile ||
    request.mimeType.includes("json") ||
    request.mimeType.includes("protobuf") ||
    request.mimeType.includes("octet-stream") ||
    request.url.includes("sprite") ||
    request.url.includes("glyphs") ||
    request.url.includes(".pbf");

  if (shouldFetchBody && encodedDataLength < 10 * 1024 * 1024) {
    // < 10MB
    try {
      console.log(
        `[WebMap Archiver] Fetching body for: ${request.url.substring(
          0,
          80
        )}...`
      );
      const result = (await chrome.debugger.sendCommand(
        { tabId },
        "Network.getResponseBody",
        { requestId }
      )) as { body: string; base64Encoded: boolean };

      request.responseBody = result.base64Encoded
        ? result.body
        : btoa(result.body);

      console.log(
        `[WebMap Archiver] Got body for ${
          request.isTile ? "TILE" : "resource"
        }: ${request.url.substring(0, 60)}... (${encodedDataLength} bytes)`
      );

      // Update estimated size
      const state = tabCaptureState.get(tabId);
      if (state?.status === "recording") {
        state.estimatedSize += encodedDataLength;
      }
    } catch (e) {
      // Body may not be available (e.g., cached)
      console.debug(
        `[WebMap Archiver] Could not get body for ${request.url}: ${e}`
      );
    }
  }
}

/**
 * Parse tile coordinates from URL.
 */
function parseTileUrl(
  url: string
): { coords: { z: number; x: number; y: number }; source: string } | null {
  // Common tile URL patterns:
  // /{z}/{x}/{y}.pbf (standard)
  // /{z}/{x}/{y}.pbf?key=xxx (MapTiler)
  // /{z}/{x}/{y}.png
  // /tiles/{z}/{x}/{y}
  // /v3/{z}/{x}/{y}.pbf (MapTiler)
  // /data/v3/{z}/{x}/{y}.pbf
  // ?x={x}&y={y}&z={z}

  const patterns: Array<{ regex: RegExp; groups: [number, number, number] }> = [
    // Standard: /{z}/{x}/{y}.ext or /{z}/{x}/{y}.ext?params
    {
      regex: /\/(\d+)\/(\d+)\/(\d+)\.(pbf|mvt|png|jpg|jpeg|webp|avif)/,
      groups: [1, 2, 3],
    },
    // /tiles/{z}/{x}/{y}
    { regex: /\/tiles\/(\d+)\/(\d+)\/(\d+)/, groups: [1, 2, 3] },
    // Query params: ?z={z}&x={x}&y={y} (various orders)
    { regex: /[?&]z=(\d+)&x=(\d+)&y=(\d+)/, groups: [1, 2, 3] },
    { regex: /[?&]x=(\d+)&y=(\d+)&z=(\d+)/, groups: [3, 1, 2] },
    // Broader pattern: any 3 consecutive numbers that look like z/x/y (z typically 0-22)
    { regex: /\/(\d{1,2})\/(\d+)\/(\d+)(?:\.|\/|$|\?)/, groups: [1, 2, 3] },
  ];

  for (const { regex, groups } of patterns) {
    const match = url.match(regex);
    if (match) {
      const z = parseInt(match[groups[0]]);
      const x = parseInt(match[groups[1]]);
      const y = parseInt(match[groups[2]]);

      // Sanity check: z should be 0-22 for web mercator
      if (z < 0 || z > 22) continue;

      // Extract source name from URL
      try {
        const urlObj = new URL(url);
        // Try to get a meaningful source name
        const hostParts = urlObj.hostname.split(".");
        let source = hostParts[0];

        // Handle api.maptiler.com -> maptiler
        if (source === "api" && hostParts.length > 1) {
          source = hostParts[1];
        }
        // Handle tiles.example.com -> example
        if (source === "tiles" && hostParts.length > 1) {
          source = hostParts[1];
        }

        console.log(
          `[WebMap Archiver] Detected tile: z=${z}, x=${x}, y=${y}, source=${source}, url=${url.substring(
            0,
            100
          )}`
        );

        return {
          coords: { z, x, y },
          source: source || "tiles",
        };
      } catch {
        return {
          coords: { z, x, y },
          source: "tiles",
        };
      }
    }
  }

  return null;
}

// ============================================================================
// BUNDLE BUILDING
// ============================================================================

/**
 * Build capture bundle from recorded data.
 */
function buildCaptureBundle(
  requests: CapturedRequest[],
  styleResult: any,
  pageInfo: { url: string; title: string },
  recordingState: CaptureState & { status: "recording" }
): CaptureBundle {
  // Log summary of captured requests
  console.log(
    `[WebMap Archiver] Building bundle from ${requests.length} total requests`
  );

  const tileRequests = requests.filter((r) => r.isTile);
  const tileWithBody = tileRequests.filter((r) => r.responseBody);
  console.log(
    `[WebMap Archiver] Tile requests: ${tileRequests.length}, with body: ${tileWithBody.length}`
  );

  // Log some sample URLs for debugging
  const sampleUrls = requests.slice(0, 10).map((r) => ({
    url: r.url.substring(0, 100),
    isTile: r.isTile,
    hasBody: !!r.responseBody,
    mimeType: r.mimeType,
  }));
  console.log("[WebMap Archiver] Sample requests:", sampleUrls);

  // Extract tiles
  const tiles = requests
    .filter((r) => r.isTile && r.responseBody)
    .map((r) => ({
      z: r.tileCoords!.z,
      x: r.tileCoords!.x,
      y: r.tileCoords!.y,
      source: r.tileSource!,
      data: r.responseBody!,
      format: r.mimeType.includes("png") ? ("png" as const) : ("pbf" as const),
    }));

  console.log(`[WebMap Archiver] Final tiles in bundle: ${tiles.length}`);

  // Build HAR from all requests
  const har: HARLog = {
    log: {
      version: "1.2",
      creator: { name: "WebMap Archiver", version: "0.1.0" },
      entries: requests.map((r) => ({
        startedDateTime: new Date(r.timestamp).toISOString(),
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
            size: r.responseSize,
            mimeType: r.mimeType,
            text: r.responseBody,
            encoding: r.responseBody ? "base64" : undefined,
          },
        },
        timings: {
          wait: 0,
          receive: 0,
        },
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
        totalRequests: requests.length,
        tileCount: tiles.length,
        zoomLevels: recordingState.zoomLevels,
        estimatedSize: recordingState.estimatedSize,
        recordingDuration:
          Date.now() - new Date(recordingState.startedAt).getTime(),
      },
    },
    viewport: styleResult?.viewport
      ? {
          center: [
            styleResult.viewport.center.lng,
            styleResult.viewport.center.lat,
          ],
          zoom: styleResult.viewport.zoom,
          bounds: styleResult.viewport.bounds
            ? [
                [
                  styleResult.viewport.bounds._sw.lng,
                  styleResult.viewport.bounds._sw.lat,
                ],
                [
                  styleResult.viewport.bounds._ne.lng,
                  styleResult.viewport.bounds._ne.lat,
                ],
              ]
            : undefined,
          bearing: styleResult.viewport.bearing || 0,
          pitch: styleResult.viewport.pitch || 0,
        }
      : {
          center: [0, 0],
          zoom: 10,
        },
    style: styleResult?.style,
    har,
    tiles,
  };
}

// ============================================================================
// PROCESSING & DOWNLOADS
// ============================================================================

/**
 * Process capture via cloud or local service.
 * Tries endpoints in order: Modal cloud → local service → local dev.
 */
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
    hasStyle: !!bundle.style,
    hasHar: !!bundle.har,
    metadata: bundle.metadata,
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

      console.log(`[WebMap Archiver] Response status: ${response.status}`);

      if (!response.ok) {
        const errorText = await response.text();
        console.warn(
          `[WebMap Archiver] ${endpoint} returned ${response.status}:`,
          errorText
        );
        continue;
      }

      const result = await response.json();
      console.log("[WebMap Archiver] Response JSON:", result);

      if (result.success) {
        console.log(`[WebMap Archiver] Processing successful via ${endpoint}`);
        console.log("[WebMap Archiver] Download URL:", result.downloadUrl);
        return {
          success: true,
          downloadUrl: result.downloadUrl,
          filename: result.filename,
          size: result.size,
        };
      } else {
        console.warn(
          `[WebMap Archiver] ${endpoint} processing failed:`,
          result.error
        );
        continue;
      }
    } catch (e) {
      console.warn(`[WebMap Archiver] ${endpoint} request failed:`, e);
      continue;
    }
  }

  // All endpoints failed - fall back to raw bundle download
  console.log(
    "[WebMap Archiver] All processing endpoints failed, falling back to bundle download"
  );
  return {
    success: false,
    fallbackToDownload: true,
    error: "Processing services unavailable",
  };
}

/**
 * Download capture bundle as JSON file.
 */
function downloadBundle(bundle: CaptureBundle, filename: string): void {
  const json = JSON.stringify(bundle, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);

  chrome.downloads.download({
    url,
    filename,
    saveAs: true,
  });

  // Clean up blob URL after download starts
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/**
 * Generate filename from bundle metadata.
 */
function generateFilename(bundle: CaptureBundle): string {
  const url = new URL(bundle.metadata.url);
  const host = url.hostname.replace(/\./g, "-");
  const date = bundle.metadata.capturedAt.split("T")[0];
  return `${host}-${date}.webmap-capture.json`;
}

// Clean up state when tab is closed
chrome.tabs.onRemoved.addListener((tabId) => {
  tabMapState.delete(tabId);
  tabCaptureState.delete(tabId);
  tabCapturedRequests.delete(tabId);
});
