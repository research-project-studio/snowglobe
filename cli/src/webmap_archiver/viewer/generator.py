"""
Generate self-contained HTML viewer for archived maps.

Key requirements:
- MapLibre GL JS viewer
- PMTiles protocol support
- CRITICAL: Generate styling for ALL tile sources, including those not in original style.json
- Display archive bounds and zoom info
- Work when served locally (not from file://)

Design note: Data layers are commonly added programmatically and won't be in the
captured style.json. The viewer MUST render these "orphan" sources with sensible
default styling. This is the primary use case, not an edge case.
"""

from dataclasses import dataclass
from pathlib import Path
import json

from ..tiles.coverage import GeoBounds


@dataclass
class ViewerConfig:
    """Configuration for the viewer."""
    name: str
    bounds: GeoBounds
    min_zoom: int
    max_zoom: int
    tile_sources: list[dict]  # [{name, path, type, is_orphan}]
    created_at: str
    captured_style: dict | None = None  # Full MapLibre style from map.getStyle()


# Large HTML template - viewer with MapLibre GL JS and PMTiles support
VIEWER_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} - WebMap Archive</title>
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <script src="https://unpkg.com/pmtiles@3.0.7/dist/pmtiles.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
        .info-panel {{
            position: absolute;
            top: 10px;
            left: 10px;
            background: white;
            padding: 12px 16px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.15);
            max-width: 300px;
            font-size: 13px;
            z-index: 100;
        }}
        .info-panel h1 {{
            font-size: 16px;
            margin-bottom: 8px;
        }}
        .info-panel .meta {{
            color: #666;
            line-height: 1.6;
        }}
        .layer-toggle {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #eee;
        }}
        .layer-toggle label {{
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            padding: 4px 0;
        }}
        .layer-toggle input {{
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info-panel">
        <h1>{name}</h1>
        <div class="meta">
            <div>Archived: {created_at}</div>
            <div>Zoom: {min_zoom}-{max_zoom}</div>
            <div>Sources: {source_count}</div>
        </div>
        <div class="layer-toggle" id="layer-controls"></div>
    </div>
    <script>
        // Register PMTiles protocol with MapLibre
        let protocol = new pmtiles.Protocol();
        maplibregl.addProtocol("pmtiles", protocol.tile);

        // Archive configuration
        const config = {config_json};

        // Color palette for data layers WITHOUT extracted styling
        const DEFAULT_COLORS = [
            "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
            "#ff7f00", "#ffff33", "#a65628", "#f781bf"
        ];
        let colorIndex = 0;

        // Function to generate default style (fallback when no captured style)
        function generateDefaultStyle() {{
            console.log("[WebMap Archiver] No captured style, generating default style");
            // Build sources object
            const sources = {{}};
            config.tileSources.forEach(src => {{
                sources[src.name] = {{
                    type: "vector",
                    url: "pmtiles://" + src.path
                }};
            }});

            // Create style with layers for ALL sources
            return {{
                version: 8,
                sources: sources,
                layers: [
                    {{
                        id: "background",
                        type: "background",
                        paint: {{ "background-color": "#1a1a2e" }}
                    }}
                ]
            }};
        }}

        // Main initialization function
        async function initMap() {{
            let style;

            // Check if we have a captured style from map.getStyle()
            if (config.capturedStyle) {{
                console.log("[WebMap Archiver] Using captured style from map.getStyle()");

                // Load style from external file to ensure it's processed before map creation
                try {{
                    const response = await fetch('style/captured_style.json');
                    if (!response.ok) {{
                        throw new Error(`Failed to load style: ${{response.status}}`);
                    }}
                    style = await response.json();
                    console.log(`[WebMap Archiver] Loaded style with ${{style.layers?.length || 0}} layers`);
                }} catch (error) {{
                    console.error("[WebMap Archiver] Failed to load captured style, falling back to default:", error);
                    style = generateDefaultStyle();
                }}

                // CRITICAL: Resolve sprite and glyph URLs to absolute paths BEFORE map creation
                // MapLibre requires absolute URLs and validates them during style parsing
                if (style.sprite && !style.sprite.startsWith('http') && !style.sprite.startsWith('data:')) {{
                    const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
                    style.sprite = baseUrl + style.sprite.replace(/^\\.?\\//, '');
                    console.log(`[WebMap Archiver] Resolved sprite URL: ${{style.sprite}}`);
                }}
                if (style.glyphs && !style.glyphs.startsWith('http') && !style.glyphs.startsWith('data:')) {{
                    const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
                    style.glyphs = baseUrl + style.glyphs.replace(/^\\.?\\//, '');
                    console.log(`[WebMap Archiver] Resolved glyphs URL: ${{style.glyphs}}`);
                }}

                // Simplify font stacks to single fonts to match captured glyph files
                // MapLibre requests fonts as comma-separated lists, but we only have individual files
                if (style.layers) {{
                    let fontSimplificationCount = 0;
                    style.layers.forEach(layer => {{
                        if (layer.layout && Array.isArray(layer.layout['text-font'])) {{
                            const fonts = layer.layout['text-font'];
                            if (fonts.length > 1) {{
                                layer.layout['text-font'] = [fonts[0]];
                                fontSimplificationCount++;
                            }}
                        }}
                    }});
                    if (fontSimplificationCount > 0) {{
                        console.log(`[WebMap Archiver] Simplified ${{fontSimplificationCount}} font stacks to single fonts`);
                    }}
                }}
            }} else {{
                style = generateDefaultStyle();
            }}

            // Track layers for toggle controls
            const layerGroups = {{}};

            // Helper to build color expression from extracted colors
            function buildColorExpression(colors, sourceLayer) {{
                if (!colors || Object.keys(colors).length === 0) {{
                    return null;
                }}

                // Build a case expression: ["case", condition1, color1, condition2, color2, ..., default]
                const expr = ["case"];
                for (const [category, color] of Object.entries(colors)) {{
                    if (category !== 'unknown' && category !== 'other' && color) {{
                        // Assume properties are boolean flags (==1 means true)
                        expr.push(["==", ["get", category], 1]);
                        expr.push(color);
                    }}
                }}
                // Default color
                expr.push(colors.unknown || colors.other || "#888888");

                return expr;
            }}

            // Add layers for each source (only if not using captured style)
            if (!config.capturedStyle) {{
                config.tileSources.forEach((src, i) => {{
                const isDataLayer = src.isOrphan !== false;
                const extracted = src.extractedStyle;
                const layerIds = [];

                // Check if we have override layers (from map.getStyle())
                // These are the exact layer definitions from the original map
                if (extracted?.overrideLayers && extracted.overrideLayers.length > 0) {{
                    console.log("Using override layers for", src.name, ":", extracted.overrideLayers.length, "layers");

                    extracted.overrideLayers.forEach((layerDef, idx) => {{
                        // Clone the layer definition and update source reference
                        const layer = JSON.parse(JSON.stringify(layerDef));
                        layer.id = src.name + "-" + (layer.id || idx);
                        layer.source = src.name;

                        // Ensure source-layer is set correctly
                        if (!layer["source-layer"] && extracted.sourceLayer) {{
                            layer["source-layer"] = extracted.sourceLayer;
                        }}

                        style.layers.push(layer);
                        layerIds.push(layer.id);
                    }});

                    layerGroups[src.name] = {{
                        label: src.name + " (original style)",
                        layers: layerIds,
                        isData: isDataLayer,
                        hasExtractedStyle: true,
                        sourceLayers: extracted.allLayers || []
                    }};
                    return; // Skip the default layer generation
                }}

                // Determine colors to use (for non-override case)
                let color;
                let colorExpr = null;

                if (extracted && extracted.colors && Object.keys(extracted.colors).length > 0) {{
                    // Use extracted colors - build expression
                    colorExpr = buildColorExpression(extracted.colors, extracted.sourceLayer);
                    color = Object.values(extracted.colors)[0];  // Fallback single color
                    console.log("Using extracted colors for", src.name, "confidence:", extracted.confidence);
                }} else {{
                    // Fall back to default palette
                    color = isDataLayer ? DEFAULT_COLORS[colorIndex++ % DEFAULT_COLORS.length] : "#4a4a6a";
                }}

                const layerType = extracted?.layerType || "line";

                // Get all discovered source layers, or fall back to single sourceLayer
                // This comes from actual tile inspection and is reliable
                let sourceLayers = extracted?.allLayers || [];
                if (sourceLayers.length === 0 && extracted?.sourceLayer) {{
                    sourceLayers = [extracted.sourceLayer];
                }}

                // If we have discovered source layers, create a layer for each
                // If not, create layers without source-layer (will try to render all)
                if (sourceLayers.length > 0) {{
                    console.log("Creating layers for source", src.name, "with discovered layers:", sourceLayers);

                    sourceLayers.forEach((sourceLayer, idx) => {{
                        const suffix = sourceLayers.length > 1 ? `-${{idx}}` : '';

                        // Line layer
                        if (layerType === "line" || !isDataLayer || !extracted) {{
                            const lineId = src.name + "-line" + suffix;
                            style.layers.push({{
                                id: lineId,
                                type: "line",
                                source: src.name,
                                "source-layer": sourceLayer,
                                paint: {{
                                    "line-color": colorExpr || color,
                                    "line-width": isDataLayer ? 2 : 1,
                                    "line-opacity": isDataLayer ? 0.9 : 0.5
                                }}
                            }});
                            layerIds.push(lineId);
                        }}

                        // Fill layer for polygons
                        if (layerType === "fill" || !extracted) {{
                            const fillId = src.name + "-fill" + suffix;
                            style.layers.push({{
                                id: fillId,
                                type: "fill",
                                source: src.name,
                                "source-layer": sourceLayer,
                                filter: ["==", ["geometry-type"], "Polygon"],
                                paint: {{
                                    "fill-color": colorExpr || color,
                                    "fill-opacity": isDataLayer ? 0.4 : 0.2
                                }}
                            }});
                            layerIds.push(fillId);
                        }}

                        // Circle layer for points
                        if (layerType === "circle" || !extracted) {{
                            const circleId = src.name + "-circle" + suffix;
                            style.layers.push({{
                                id: circleId,
                                type: "circle",
                                source: src.name,
                                "source-layer": sourceLayer,
                                filter: ["==", ["geometry-type"], "Point"],
                                paint: {{
                                    "circle-color": colorExpr || color,
                                    "circle-radius": isDataLayer ? 6 : 3,
                                    "circle-stroke-color": "#ffffff",
                                    "circle-stroke-width": isDataLayer ? 1 : 0
                                }}
                            }});
                            layerIds.push(circleId);
                        }}
                    }});
                }} else {{
                    // No source layers discovered - this shouldn't happen for vector tiles
                    // but handle gracefully by omitting source-layer
                    console.warn("No source layers discovered for", src.name, "- layers may not render correctly");

                    const lineId = src.name + "-line";
                    style.layers.push({{
                        id: lineId,
                        type: "line",
                        source: src.name,
                        paint: {{
                            "line-color": color,
                            "line-width": 2,
                            "line-opacity": 0.9
                        }}
                    }});
                    layerIds.push(lineId);
                }}

                layerGroups[src.name] = {{
                    label: src.name + (extracted?.confidence ? ` (${{Math.round(extracted.confidence * 100)}}% styled)` : ""),
                    layers: layerIds,
                    isData: isDataLayer,
                    hasExtractedStyle: !!(extracted && extracted.colors && Object.keys(extracted.colors).length > 0),
                    sourceLayers: sourceLayers
                }};
                }});
            }} // End if (!config.capturedStyle)

            // Transform request handler for glyphs only
            // Sprites are handled by URL resolution before map creation
            function transformRequest(url, resourceType) {{
                // Handle multi-font glyph requests as a fallback safety net
                // MapLibre may request multiple fonts in one path like "Font1,Font2/0-255.pbf"
                // But we only have individual font files, so use the first font in the list
                if (resourceType === 'Glyphs') {{
                    // Check if URL contains comma-separated fonts
                    const match = url.match(/\/glyphs\/([^/]+)\/(\d+-\d+\.pbf)/);
                    if (match) {{
                        const fontStacks = match[1];
                        const range = match[2];

                        // If multiple fonts (contains comma), use only the first one
                        if (fontStacks.includes(',')) {{
                            const firstFont = fontStacks.split(',')[0];
                            const newUrl = url.replace(
                                `/glyphs/${{fontStacks}}/${{range}}`,
                                `/glyphs/${{firstFont}}/${{range}}`
                            );
                            console.log(`[Glyphs] Multi-font request fallback: ${{fontStacks}} -> using ${{firstFont}}`);
                            return {{ url: newUrl }};
                        }}
                    }}
                }}

                return {{ url: url }};
            }}

            // Create map with processed style
            const map = new maplibregl.Map({{
                container: "map",
                style: style,
                center: [{center_lon}, {center_lat}],
                zoom: {initial_zoom},
                maxBounds: [[{west}, {south}], [{east}, {north}]],
                transformRequest: transformRequest
            }});

            map.addControl(new maplibregl.NavigationControl(), "top-right");
            map.addControl(new maplibregl.ScaleControl(), "bottom-right");

            // Add layer toggle controls
            map.on("load", () => {{
                console.log("[WebMap Archiver] Map loaded successfully");
                const controlsDiv = document.getElementById("layer-controls");

                Object.entries(layerGroups).forEach(([name, group]) => {{
                    const label = document.createElement("label");
                    const checkbox = document.createElement("input");
                    checkbox.type = "checkbox";
                    checkbox.checked = true;
                    checkbox.addEventListener("change", (e) => {{
                        const visibility = e.target.checked ? "visible" : "none";
                        group.layers.forEach(layerId => {{
                            if (map.getLayer(layerId)) {{
                                map.setLayoutProperty(layerId, "visibility", visibility);
                            }}
                        }});
                    }});

                    const span = document.createElement("span");
                    let labelText = group.label;
                    if (group.isData) {{
                        labelText += group.hasExtractedStyle ? " ✓" : " (default style)";
                    }}
                    span.textContent = labelText;

                    // Build tooltip with source layer info
                    let tooltip = group.hasExtractedStyle
                        ? "Styling extracted from original JavaScript"
                        : group.isData
                            ? "Using default styling - original could not be extracted"
                            : "Basemap layer";

                    if (group.sourceLayers && group.sourceLayers.length > 0) {{
                        tooltip += "\\n\\nSource layers: " + group.sourceLayers.join(", ");
                    }}
                    span.title = tooltip;

                    label.appendChild(checkbox);
                    label.appendChild(span);
                    controlsDiv.appendChild(label);
                }});
            }});

            // Log errors for debugging
            map.on("error", (e) => {{
                console.error("[WebMap Archiver] Map error:", e.error?.message || e);
            }});
        }}

        // Initialize map when DOM is ready
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', initMap);
        }} else {{
            initMap();
        }}
    </script>
</body>
</html>
'''


class ViewerGenerator:
    """Generate HTML viewer for archived maps."""

    def generate(self, config: ViewerConfig) -> str:
        """Generate viewer HTML from configuration."""
        center = config.bounds.center

        # Build config JSON for JavaScript
        config_dict = {
            "name": config.name,
            "bounds": {
                "west": config.bounds.west,
                "south": config.bounds.south,
                "east": config.bounds.east,
                "north": config.bounds.north,
            },
            "minZoom": config.min_zoom,
            "maxZoom": config.max_zoom,
            "tileSources": config.tile_sources,
            "createdAt": config.created_at,
            "capturedStyle": bool(config.captured_style),  # Flag indicating if captured style exists (actual style loaded from file)
        }

        return VIEWER_TEMPLATE.format(
            name=config.name,
            created_at=config.created_at,
            min_zoom=config.min_zoom,
            max_zoom=config.max_zoom,
            source_count=len(config.tile_sources),
            config_json=json.dumps(config_dict, indent=2),
            center_lon=center[0],
            center_lat=center[1],
            initial_zoom=(config.min_zoom + config.max_zoom) // 2,
            west=config.bounds.west,
            south=config.bounds.south,
            east=config.bounds.east,
            north=config.bounds.north,
        )

    def write(self, config: ViewerConfig, output_path: Path) -> None:
        """Generate and write viewer to file."""
        html = self.generate(config)
        output_path.write_text(html, encoding='utf-8')
