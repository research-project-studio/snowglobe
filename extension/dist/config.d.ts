/**
 * Extension configuration.
 *
 * API endpoints for capture bundle processing.
 */
export declare const CONFIG: {
    cloudEndpoint: string;
    localDevEndpoint: string;
    localServiceEndpoint: string;
    processingTimeout: number;
    enableLocalFallback: boolean;
    archiveExpiryHours: number;
};
/**
 * Capture state machine.
 * Tracks recording state per tab.
 */
export type CaptureState = {
    status: "idle";
} | {
    status: "recording";
    startedAt: string;
    tileCount: number;
    totalRequests: number;
    zoomLevels: number[];
    estimatedSize: number;
} | {
    status: "processing";
    progress: number;
    message: string;
} | {
    status: "complete";
    filename: string;
    size: number;
} | {
    status: "error";
    message: string;
};
/**
 * Captured network request during recording.
 * Used by DevTools panel to track network traffic.
 */
export interface CapturedRequest {
    url: string;
    method: string;
    status: number;
    mimeType: string;
    size: number;
    body?: string;
    isTile: boolean;
    tileCoords?: {
        z: number;
        x: number;
        y: number;
    };
    tileSource?: string;
}
/**
 * Get ordered list of processing endpoints to try.
 * Cloud first, then local service, then local dev.
 */
export declare function getProcessingEndpoints(): string[];
//# sourceMappingURL=config.d.ts.map