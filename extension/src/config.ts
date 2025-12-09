/**
 * Extension configuration.
 *
 * API endpoints for capture bundle processing.
 */

export const CONFIG = {
  // Modal cloud endpoint (primary)
  // Replace YOUR_USERNAME with your Modal username after deployment
  cloudEndpoint:
    "https://mariogiampieri--webmap-archiver-fastapi-app.modal.run/process",

  // Local development endpoint (modal serve)
  localDevEndpoint: "http://localhost:8000/process",

  // Local Python service (webmap-archive serve)
  localServiceEndpoint: "http://localhost:8765/process",

  // Processing timeout (5 minutes)
  processingTimeout: 300000,

  // Enable local fallback when cloud is unavailable
  enableLocalFallback: true,

  // Archive download expiry notice (hours)
  archiveExpiryHours: 24,
};

/**
 * Capture state machine.
 * Tracks recording state per tab.
 */
export type CaptureState =
  | { status: "idle" }
  | {
      status: "recording";
      startedAt: string;
      tileCount: number;
      totalRequests: number;
      zoomLevels: number[];
      estimatedSize: number; // bytes
    }
  | { status: "processing"; progress: number; message: string }
  | { status: "complete"; filename: string; size: number }
  | { status: "error"; message: string };

/**
 * Captured network request during recording.
 */
export interface CapturedRequest {
  requestId: string; // Chrome debugger request ID for matching
  url: string;
  method: string;
  status: number;
  mimeType: string;
  responseSize: number;
  responseBody?: string; // base64 encoded
  timestamp: number;
  // Tile-specific fields (if detected as tile request)
  isTile: boolean;
  tileCoords?: { z: number; x: number; y: number };
  tileSource?: string;
}

/**
 * Get ordered list of processing endpoints to try.
 * Cloud first, then local service, then local dev.
 */
export function getProcessingEndpoints(): string[] {
  const endpoints = [CONFIG.cloudEndpoint];

  if (CONFIG.enableLocalFallback) {
    endpoints.push(CONFIG.localServiceEndpoint);
    endpoints.push(CONFIG.localDevEndpoint);
  }

  return endpoints;
}
