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
export {};
//# sourceMappingURL=service-worker.d.ts.map