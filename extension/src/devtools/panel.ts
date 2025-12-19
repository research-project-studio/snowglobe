/**
 * DevTools panel - main capture interface.
 *
 * Uses chrome.devtools.network API to capture network traffic reliably.
 * This works because it runs in the DevTools context, not a service worker.
 */

(function () {
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
    isSprite?: boolean;
    spriteInfo?: { filename: string; variant: string; contentType: string };
    isGlyph?: boolean;
    glyphInfo?: { fontStack: string; range: string };
  }

  interface CaptureOptions {
    reloadOnStart: boolean;
    expandCoverage: boolean;
    archiveMode: "standalone" | "original" | "full";
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
    // Note: downloadBtn handler is set dynamically in showComplete()
    // to switch between handleDownload and handleDownloadBundle
    downloadBundleBtn.addEventListener("click", handleDownloadBundle);
    newCaptureBtn.addEventListener("click", resetToIdle);
    retryBtn.addEventListener("click", resetToIdle);

    // Options panel toggle
    const optionsHeader = document.getElementById("options-header");
    if (optionsHeader) {
      optionsHeader.addEventListener("click", toggleOptions);
    }
  }

  async function checkForMaps(): Promise<void> {
    try {
      const result = await sendToContentScript({ type: "GET_MAPS" });
      if (result && result.count > 0) {
        const types = result.maps.map((m: any) => m.type).join(", ");
        mapStatus.textContent = `${result.count} map${
          result.count > 1 ? "s" : ""
        } detected (${types})`;
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

  function showState(
    state: "idle" | "recording" | "processing" | "complete" | "error"
  ): void {
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
  // CAPTURE OPTIONS
  // ============================================================================

  function getDefaultOptions(): CaptureOptions {
    return {
      reloadOnStart: true,
      expandCoverage: true,
      archiveMode: "standalone",
    };
  }

  function getCaptureOptions(): CaptureOptions {
    const defaults = getDefaultOptions();

    try {
      const reloadCheckbox = document.getElementById(
        "opt-reload-page"
      ) as HTMLInputElement;
      const expandCheckbox = document.getElementById(
        "opt-expand-coverage"
      ) as HTMLInputElement;
      const modeSelect = document.getElementById(
        "opt-archive-mode"
      ) as HTMLSelectElement;

      return {
        reloadOnStart: reloadCheckbox?.checked ?? defaults.reloadOnStart,
        expandCoverage: expandCheckbox?.checked ?? defaults.expandCoverage,
        archiveMode:
          (modeSelect?.value as CaptureOptions["archiveMode"]) ??
          defaults.archiveMode,
      };
    } catch (error) {
      console.warn(
        "[WebMap Archiver] Error reading options, using defaults:",
        error
      );
      return defaults;
    }
  }

  function toggleOptions(): void {
    const panel = document.getElementById("options-panel");
    const content = document.getElementById("options-content");

    if (panel && content) {
      panel.classList.toggle("expanded");
      content.classList.toggle("collapsed");
    }
  }

  // ============================================================================
  // RECORDING
  // ============================================================================

  async function startRecording(): Promise<void> {
    console.log("[WebMap Archiver] Starting recording...");

    // Get capture options
    const options = getCaptureOptions();

    // Reset capture state
    capturedRequests = [];
    tileCount = 0;
    totalSize = 0;
    zoomLevels.clear();
    isRecording = true;
    recordingStartTime = Date.now();

    // Reset UI
    statTiles.textContent = "0";
    statRequests.textContent = "0";
    statSize.textContent = "0 B";
    statZooms.textContent = "-";

    // IMPORTANT: Start listening to network requests BEFORE reloading
    // This ensures we capture sprites, glyphs, and style from the initial page load
    chrome.devtools.network.onRequestFinished.addListener(handleRequest);

    // Start duration timer
    durationInterval = setInterval(updateDuration, 1000);

    // Notify service worker
    chrome.runtime.sendMessage({
      type: "CAPTURE_STARTED",
      tabId: chrome.devtools.inspectedWindow.tabId,
    });

    // Reload page if option enabled
    if (options.reloadOnStart) {
      tileList.innerHTML =
        '<p class="empty-message">Reloading page to capture all resources...</p>';

      try {
        console.log(
          "[WebMap Archiver] Reloading page to capture initial resources..."
        );
        await chrome.devtools.inspectedWindow.reload({
          ignoreCache: true,
        });

        // Brief delay after reload before updating UI
        await new Promise((resolve) => setTimeout(resolve, 300));
      } catch (error) {
        console.error("[WebMap Archiver] Failed to reload page:", error);
        // Continue anyway - capture what we can
      }
    }

    // Update UI for capture mode
    tileList.innerHTML =
      '<p class="empty-message">Pan or zoom the map to capture tiles...</p>';

    showState("recording");
    console.log("[WebMap Archiver] Recording started");
  }

  function handleRequest(request: chrome.devtools.network.Request): void {
    if (!isRecording) return;

    const url = request.request.url;
    const mimeType = request.response.content.mimeType || "";
    const status = request.response.status;
    const size = request.response.content.size || 0;

    console.log(
      `[WebMap Archiver] Request: ${url.substring(0, 80)}... (${mimeType})`
    );

    // Parse tile info
    const tileInfo = parseTileUrl(url);

    // Check for sprites and glyphs
    const isSpriteResource = isSprite(url);
    const isGlyphResource = isGlyph(url);

    const captured: CapturedRequest = {
      url,
      method: request.request.method,
      status,
      mimeType,
      size,
      isTile: tileInfo !== null,
      tileCoords: tileInfo?.coords,
      tileSource: tileInfo?.source,
      isSprite: isSpriteResource,
      spriteInfo: isSpriteResource ? extractSpriteInfo(url) : undefined,
      isGlyph: isGlyphResource,
      glyphInfo: isGlyphResource ? parseGlyphUrl(url) : undefined,
    };

    // Get response body for tiles and important resources
    const shouldGetBody =
      tileInfo !== null ||
      isSpriteResource ||
      isGlyphResource ||
      mimeType.includes("json") ||
      mimeType.includes("pbf") ||
      mimeType.includes("protobuf") ||
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

      console.log(
        `[WebMap Archiver] ✅ Tile captured: z${request.tileCoords.z}/${request.tileCoords.x}/${request.tileCoords.y}`
      );
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
    item.textContent = `z${request.tileCoords!.z}/${request.tileCoords!.x}/${
      request.tileCoords!.y
    } (${request.tileSource})`;

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
    recordingDuration.textContent = `${minutes}:${seconds
      .toString()
      .padStart(2, "0")}`;
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
      const pageInfo = await new Promise<{ url: string; title: string }>(
        (resolve) => {
          chrome.devtools.inspectedWindow.eval(
            `({ url: window.location.href, title: document.title })`,
            (result, error) => {
              if (error) {
                console.error(
                  "[WebMap Archiver] Failed to get page info:",
                  error
                );
                // Fallback: try to get URL from inspected window
                chrome.devtools.inspectedWindow.eval(
                  `window.location.href`,
                  (urlResult) => {
                    resolve({
                      url: (urlResult as string) || "https://unknown",
                      title: "Unknown Page",
                    });
                  }
                );
              } else if (result && typeof result === "object") {
                const r = result as { url?: string; title?: string };
                resolve({
                  url: r.url || "https://unknown",
                  title: r.title || "Unknown Page",
                });
              } else {
                resolve({ url: "https://unknown", title: "Unknown Page" });
              }
            }
          );
        }
      );

      console.log("[WebMap Archiver] Page info:", pageInfo);

      // Verify we have a URL before proceeding
      if (!pageInfo.url || pageInfo.url === "https://unknown") {
        console.error("[WebMap Archiver] Could not determine page URL");
      }

      updateProgress(20, "Extracting GeoJSON sources...");

      // Debug: Log what we got from style capture
      console.log("[WebMap Archiver] Style result:", styleResult);
      console.log("[WebMap Archiver] Has style:", !!styleResult?.style);
      if (styleResult?.style?.sources) {
        const sources = styleResult.style.sources;
        console.log("[WebMap Archiver] Style sources:", Object.keys(sources));
        console.log("[WebMap Archiver] Source types:",
          Object.entries(sources).map(([name, def]: [string, any]) => `${name}: ${def.type}`));
      }

      // Extract GeoJSON data from all GeoJSON sources
      // This uses querySourceFeatures() to get actual loaded features
      if (styleResult && styleResult.style) {
        styleResult.style = await extractGeoJSONSources(styleResult.style);
      } else {
        console.warn("[WebMap Archiver] No style captured - skipping GeoJSON extraction");
      }

      updateProgress(30, "Building capture bundle...");

      // Build capture bundle
      const bundle = buildCaptureBundle(styleResult, pageInfo);
      lastBundle = bundle;

      console.log("[WebMap Archiver] Bundle built:", {
        tiles: bundle.tiles?.length || 0,
        harEntries: bundle.har?.log?.entries?.length || 0,
        hasStyle: !!bundle.style,
      });

      console.log("[WebMap Archiver] Bundle being sent to Modal:");
      console.log(JSON.stringify(bundle, null, 2).substring(0, 2000)); // First 2000 chars
      console.log("[WebMap Archiver] Bundle stats:", {
        version: bundle.version,
        hasMetadata: !!bundle.metadata,
        metadataUrl: bundle.metadata?.url,
        metadataCapturedAt: bundle.metadata?.capturedAt,
        hasViewport: !!bundle.viewport,
        viewportCenter: bundle.viewport?.center,
        viewportZoom: bundle.viewport?.zoom,
        tilesCount: bundle.tiles?.length,
        tilesFirstItem: bundle.tiles?.[0],
        hasHar: !!bundle.har,
        harEntriesCount: bundle.har?.log?.entries?.length,
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

        showComplete(
          result.filename,
          bundle.tiles?.length || 0,
          result.size || 0
        );
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
  // GEOJSON EXTRACTION
  // ============================================================================

  /**
   * Extract GeoJSON data from all GeoJSON sources using querySourceFeatures().
   * This is called at "Stop Capture" time, after async data has loaded.
   */
  async function extractGeoJSONSources(style: any): Promise<any> {
    if (!style || !style.sources) {
      return style;
    }

    console.log("[WebMap Archiver] Extracting GeoJSON sources...");

    // Find all GeoJSON sources
    const geojsonSourceIds = Object.entries(style.sources)
      .filter(([, sourceDef]: [string, any]) => sourceDef.type === "geojson")
      .map(([sourceId]) => sourceId);

    if (geojsonSourceIds.length === 0) {
      console.log("[WebMap Archiver] No GeoJSON sources found");
      return style;
    }

    console.log(
      `[WebMap Archiver] Found ${geojsonSourceIds.length} GeoJSON sources: ${geojsonSourceIds.join(", ")}`
    );

    // Extract features from each GeoJSON source using inspectedWindow.eval
    const extractionScript = `
      (function() {
        try {
          // Find the map instance
          const container = document.querySelector('.maplibregl-map, .mapboxgl-map');
          if (!container) {
            return { error: 'No map container found' };
          }

          const map = container.__maplibregl || container._map || container.__mapboxgl ||
                      window.map || window.maplibreMap || window.mapboxMap;
          if (!map || typeof map.querySourceFeatures !== 'function') {
            return { error: 'No map instance found' };
          }

          const style = map.getStyle();
          if (!style || !style.sources) {
            return { error: 'Could not get style' };
          }

          const geojsonSourceIds = ${JSON.stringify(geojsonSourceIds)};
          const extractedData = {};

          for (const sourceId of geojsonSourceIds) {
            try {
              const features = map.querySourceFeatures(sourceId);

              if (!features || features.length === 0) {
                console.warn('[WebMap Archiver] No features found in source ' + sourceId);

                // Try _data fallback
                const source = map.getSource(sourceId);
                if (source && source._data) {
                  extractedData[sourceId] = source._data;
                  continue;
                }

                continue;
              }

              console.log('[WebMap Archiver] Extracted ' + features.length + ' features from ' + sourceId);

              // Deduplicate features by ID or geometry
              const seen = new Map();
              const unique = [];
              for (const f of features) {
                const key = f.id !== undefined ? f.id : JSON.stringify(f.geometry);
                if (!seen.has(key)) {
                  seen.set(key, true);
                  unique.push({
                    type: 'Feature',
                    geometry: f.geometry,
                    properties: f.properties || {},
                    ...(f.id !== undefined && { id: f.id }),
                  });
                }
              }

              extractedData[sourceId] = {
                type: 'FeatureCollection',
                features: unique,
              };

              console.log('[WebMap Archiver] Injected ' + unique.length + ' unique features into ' + sourceId);

            } catch (e) {
              console.error('[WebMap Archiver] Error extracting ' + sourceId + ':', e);
            }
          }

          return { extractedData };

        } catch (e) {
          return { error: 'Extraction failed: ' + e.message };
        }
      })();
    `;

    return new Promise((resolve) => {
      chrome.devtools.inspectedWindow.eval(
        extractionScript,
        (result: any, error: any) => {
          if (error) {
            console.error(
              "[WebMap Archiver] GeoJSON extraction error:",
              error
            );
            resolve(style);
            return;
          }

          if (result && result.error) {
            console.error("[WebMap Archiver] GeoJSON extraction failed:", result.error);
            resolve(style);
            return;
          }

          if (result && result.extractedData) {
            // Inject extracted GeoJSON data into style
            for (const [sourceId, data] of Object.entries(
              result.extractedData
            )) {
              if (style.sources[sourceId]) {
                style.sources[sourceId].data = data;

                // Log size info
                const sizeBytes = JSON.stringify(data).length;
                const sizeMB = sizeBytes / (1024 * 1024);
                const featureCount = (data as any).features?.length || 0;

                console.log(
                  `[WebMap Archiver] GeoJSON '${sourceId}': ${sizeMB.toFixed(2)}MB, ${featureCount} features`
                );

                if (sizeMB > 5) {
                  console.warn(
                    `[WebMap Archiver] Large GeoJSON source '${sourceId}': ${sizeMB.toFixed(1)}MB`
                  );
                }
              }
            }
          }

          resolve(style);
        }
      );
    });
  }

  // ============================================================================
  // BUNDLE BUILDING
  // ============================================================================

  function buildCaptureBundle(
    styleResult: any,
    pageInfo: { url: string; title: string }
  ): any {
    // Get capture options
    const options = getCaptureOptions();
    // Extract tiles with bodies - NOTE: field names must match Python parser
    const tiles = capturedRequests
      .filter((r) => r.isTile && r.body && r.tileCoords)
      .map((r) => ({
        z: r.tileCoords!.z,
        x: r.tileCoords!.x,
        y: r.tileCoords!.y,
        sourceId: r.tileSource || "tiles",
        url: r.url, // Include original URL for pattern matching
        data: r.body!, // base64-encoded
        format: r.mimeType.includes("png")
          ? "png"
          : r.mimeType.includes("jpg") || r.mimeType.includes("jpeg")
          ? "jpg"
          : r.mimeType.includes("webp")
          ? "webp"
          : "pbf",
      }));

    // Extract resources (sprites and glyphs)
    const resources = capturedRequests
      .filter((r) => (r.isSprite || r.isGlyph) && r.body)
      .map((r) => {
        if (r.isSprite && r.spriteInfo) {
          return {
            resourceType: "sprite",
            url: r.url,
            data: r.body!,
            variant: r.spriteInfo.variant,
            contentType: r.spriteInfo.contentType,
          };
        } else if (r.isGlyph && r.glyphInfo) {
          const [rangeStart, rangeEnd] = r.glyphInfo.range
            .split("-")
            .map((n) => parseInt(n));
          return {
            resourceType: "glyph",
            url: r.url,
            data: r.body!,
            fontStack: r.glyphInfo.fontStack,
            rangeStart,
            rangeEnd,
          };
        }
        return null;
      })
      .filter((r) => r !== null);

    console.log(
      `[WebMap Archiver] Bundle resources: ${resources.length} (${
        resources.filter((r) => r.resourceType === "sprite").length
      } sprites, ${
        resources.filter((r) => r.resourceType === "glyph").length
      } glyphs)`
    );

    // Build HAR with correct structure
    const har = {
      log: {
        version: "1.2",
        creator: { name: "WebMap Archiver", version: "0.3.2" },
        entries: capturedRequests.map((r) => ({
          startedDateTime: new Date().toISOString(),
          request: {
            method: r.method,
            url: r.url,
            headers: [],
          },
          response: {
            status: r.status,
            statusText: r.status === 200 ? "OK" : "Error",
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

    // Build viewport - ensure center is [lng, lat] array
    let viewport: any = { center: [0, 0], zoom: 10 };
    if (styleResult?.viewport) {
      const vp = styleResult.viewport;
      viewport = {
        center: [
          vp.center?.lng ?? vp.center?.[0] ?? 0,
          vp.center?.lat ?? vp.center?.[1] ?? 0,
        ],
        zoom: vp.zoom ?? 10,
        bearing: vp.bearing ?? 0,
        pitch: vp.pitch ?? 0,
      };

      // Bounds must be [[sw_lng, sw_lat], [ne_lng, ne_lat]]
      if (vp.bounds) {
        viewport.bounds = [
          [
            vp.bounds._sw?.lng ?? vp.bounds[0]?.[0],
            vp.bounds._sw?.lat ?? vp.bounds[0]?.[1],
          ],
          [
            vp.bounds._ne?.lng ?? vp.bounds[1]?.[0],
            vp.bounds._ne?.lat ?? vp.bounds[1]?.[1],
          ],
        ];
      }
    }

    return {
      version: "1.0",
      metadata: {
        url: pageInfo.url,
        title: pageInfo.title,
        capturedAt: new Date().toISOString(), // ISO 8601 format
        userAgent: navigator.userAgent,
        mapLibrary: styleResult?.mapLibrary
          ? {
              type: styleResult.mapLibrary.type || "unknown",
              version: styleResult.mapLibrary.version,
            }
          : undefined,
      },
      viewport,
      style: styleResult?.style || null,
      har,
      tiles,
      resources,
      // NEW: Include capture options for Modal/CLI
      options: {
        expandCoverage: options.expandCoverage,
        archiveMode: options.archiveMode,
      },
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

  function showComplete(
    filename: string,
    tiles: number,
    size: number,
    isFallback: boolean = false
  ): void {
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
      const bundleFilename = lastFilename.replace(
        ".zip",
        ".webmap-capture.json"
      );
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

  async function getStyleFromPage(): Promise<any> {
    // Ask service worker to execute script in the tab
    const tabId = chrome.devtools.inspectedWindow.tabId;

    return new Promise((resolve) => {
      chrome.runtime.sendMessage(
        { type: "EXECUTE_CAPTURE_SCRIPT", tabId },
        (result) => {
          resolve(result);
        }
      );
    });
  }

  async function getPageInfo(): Promise<{ url: string; title: string }> {
    return new Promise((resolve) => {
      chrome.devtools.inspectedWindow.eval(
        `({ url: location.href, title: document.title })`,
        (result, error) => {
          if (error) {
            resolve({ url: "", title: "" });
          } else {
            resolve(result as { url: string; title: string });
          }
        }
      );
    });
  }

  // ============================================================================
  // RESOURCE DETECTION
  // ============================================================================

  const SPRITE_PATTERNS = [
    /\/sprite(@\dx)?\.(png|json)(\?|$)/i,
    /\/sprites?\//i,
  ];

  const GLYPH_PATTERNS = [
    /\/fonts\/[^/]+\/\d+-\d+\.pbf/i,
    /\/glyphs?\//i,
  ];

  function isSprite(url: string): boolean {
    return SPRITE_PATTERNS.some((p) => p.test(url));
  }

  function isGlyph(url: string): boolean {
    return GLYPH_PATTERNS.some((p) => p.test(url));
  }

  function extractSpriteInfo(
    url: string
  ): { filename: string; variant: string; contentType: string } {
    const match = url.match(/\/(sprite(@\dx)?\.(png|json))(\?|$)/i);
    const filename = match ? match[1] : "sprite.png";
    const variant = match && match[2] ? match[2].substring(1) : "1x"; // Remove @ prefix
    const contentType = filename.endsWith(".json") ? "json" : "image";
    return { filename, variant, contentType };
  }

  function parseGlyphUrl(url: string): { fontStack: string; range: string } {
    // Pattern: /fonts/{fontstack}/{range}.pbf
    const match = url.match(/\/fonts\/([^/]+)\/(\d+-\d+)\.pbf/i);
    if (match) {
      return {
        fontStack: decodeURIComponent(match[1]),
        range: match[2],
      };
    }
    return { fontStack: "unknown", range: "0-255" };
  }

  function parseTileUrl(
    url: string
  ): { coords: { z: number; x: number; y: number }; source: string } | null {
    const patterns: Array<{ regex: RegExp; groups: [number, number, number] }> =
      [
        {
          regex: /\/(\d+)\/(\d+)\/(\d+)\.(pbf|mvt|png|jpg|jpeg|webp|avif)/,
          groups: [1, 2, 3],
        },
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
          if (
            (source === "api" || source === "tiles") &&
            hostParts.length > 1
          ) {
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
})();
