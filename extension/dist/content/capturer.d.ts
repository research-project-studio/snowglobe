/**
 * Capture map state (style, viewport) from detected maps.
 */
import { DetectedMap } from "../types/map-libraries";
import { CaptureViewport, MapLibreStyle, CaptureMetadata } from "../types/capture-bundle";
export interface CaptureResult {
    metadata: CaptureMetadata;
    viewport: CaptureViewport;
    style?: MapLibreStyle;
}
export declare class MapCapturer {
    /**
     * Capture state from a detected map.
     */
    capture(map: DetectedMap): Promise<CaptureResult>;
    private captureMetadata;
    private captureViewport;
    private captureMapLibreViewport;
    private captureLeafletViewport;
    private captureStyle;
}
/**
 * Execute style capture via injected script.
 *
 * This is needed because content scripts run in an isolated world
 * and cannot access page JavaScript objects directly.
 */
export declare function captureStyleViaInjection(): Promise<MapLibreStyle | null>;
//# sourceMappingURL=capturer.d.ts.map