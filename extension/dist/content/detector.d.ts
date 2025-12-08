/**
 * Detect map libraries on the current page.
 *
 * Detection strategies:
 * 1. Check for global library objects (window.maplibregl, etc.)
 * 2. Look for characteristic DOM elements
 * 3. Check for canvas elements with map-like properties
 */
import { DetectedMap } from "../types/map-libraries";
export declare class MapDetector {
    private detectedMaps;
    private observer;
    /**
     * Start detecting maps on the page.
     * Returns initial detected maps and sets up observer for dynamic additions.
     */
    detect(): DetectedMap[];
    /**
     * Set up mutation observer to detect dynamically added maps.
     */
    observe(callback: (maps: DetectedMap[]) => void): void;
    /**
     * Stop observing for map changes.
     */
    disconnect(): void;
    private detectMapLibreGL;
    private detectMapboxGL;
    private detectLeaflet;
    private detectOpenLayers;
    private detectByDOM;
    private getMapInstance;
    private isValidMapInstance;
    private findMapInstanceOnWindow;
    private isAlreadyDetected;
}
/**
 * Check if current page has any maps.
 */
export declare function hasMap(): boolean;
/**
 * Get all detected maps on current page.
 */
export declare function getDetectedMaps(): DetectedMap[];
//# sourceMappingURL=detector.d.ts.map