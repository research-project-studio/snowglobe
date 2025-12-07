/**
 * Content script entry point.
 *
 * Runs on every page to detect maps and handle capture requests.
 */

import { MapDetector, getDetectedMaps } from "./detector";
import { captureStyleViaInjection } from "./capturer";
import { DetectedMap } from "../types/map-libraries";

let detectedMaps: DetectedMap[] = [];
let detector: MapDetector | null = null;

/**
 * Initialize detection on page load.
 */
function init(): void {
  detector = new MapDetector();

  // Initial detection
  detectedMaps = detector.detect();
  notifyBackground();

  // Watch for dynamically added maps
  detector.observe((maps) => {
    detectedMaps = maps;
    notifyBackground();
  });

  // Re-detect after a delay (for maps that take time to initialize)
  setTimeout(() => {
    detectedMaps = detector!.detect();
    notifyBackground();
  }, 2000);
}

/**
 * Notify background script of detected maps.
 */
function notifyBackground(): void {
  chrome.runtime.sendMessage({
    type: "MAPS_DETECTED",
    count: detectedMaps.length,
    maps: detectedMaps.map((m) => ({
      type: m.type,
      version: m.version,
    })),
  });
}

/**
 * Handle messages from popup/background.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case "GET_MAPS":
      sendResponse({
        count: detectedMaps.length,
        maps: detectedMaps.map((m) => ({
          type: m.type,
          version: m.version,
        })),
      });
      break;

    case "CAPTURE_STYLE":
      // Use injection to capture from page context
      captureStyleViaInjection().then((result) => {
        sendResponse(result);
      });
      return true; // Keep channel open for async response

    case "GET_PAGE_INFO":
      sendResponse({
        url: window.location.href,
        title: document.title,
      });
      break;

    default:
      break;
  }
});

// Initialize when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
