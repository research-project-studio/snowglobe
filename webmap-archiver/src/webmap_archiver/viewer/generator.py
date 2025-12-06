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


# Large HTML template - viewer with MapLibre GL JS and PMTiles support
VIEWER_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} - WebMap Archive</title>
    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
    <script src="https://unpkg.com/pmtiles@2.11.0/dist/pmtiles.js"></script>
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
        // Register PMTiles protocol
        const protocol = new pmtiles.Protocol();
        maplibregl.addProtocol("pmtiles", protocol.tile);

        // Archive configuration
        const config = {config_json};

        // Color palette for data layers WITHOUT extracted styling
        const DEFAULT_COLORS = [
            "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
            "#ff7f00", "#ffff33", "#a65628", "#f781bf"
        ];
        let colorIndex = 0;

        // Build sources object
        const sources = {{}};
        config.tileSources.forEach(src => {{
            sources[src.name] = {{
                type: "vector",
                url: "pmtiles://" + src.path
            }};
        }});

        // Create style with layers for ALL sources
        const style = {{
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

        // Add layers for each source
        config.tileSources.forEach((src, i) => {{
            const isDataLayer = src.isOrphan !== false;
            const extracted = src.extractedStyle;

            // Determine colors to use
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
            const sourceLayer = extracted?.sourceLayer || "";
            const layerIds = [];

            // Create layer based on extracted or inferred type
            if (layerType === "line" || !isDataLayer) {{
                const lineId = src.name + "-line";
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

            if (layerType === "fill" || !extracted) {{
                const fillId = src.name + "-fill";
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

            if (layerType === "circle" || !extracted) {{
                const circleId = src.name + "-circle";
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

            layerGroups[src.name] = {{
                label: src.name + (extracted?.confidence ? ` (${{Math.round(extracted.confidence * 100)}}% styled)` : ""),
                layers: layerIds,
                isData: isDataLayer,
                hasExtractedStyle: !!(extracted && extracted.colors && Object.keys(extracted.colors).length > 0)
            }};
        }});

        const map = new maplibregl.Map({{
            container: "map",
            style: style,
            center: [{center_lon}, {center_lat}],
            zoom: {initial_zoom},
            maxBounds: [[{west}, {south}], [{east}, {north}]]
        }});

        map.addControl(new maplibregl.NavigationControl(), "top-right");
        map.addControl(new maplibregl.ScaleControl(), "bottom-right");

        // Add layer toggle controls
        map.on("load", () => {{
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
                span.title = group.hasExtractedStyle
                    ? "Styling extracted from original JavaScript"
                    : group.isData
                        ? "Using default styling - original could not be extracted"
                        : "Basemap layer";

                label.appendChild(checkbox);
                label.appendChild(span);
                controlsDiv.appendChild(label);
            }});
        }});

        // Log errors for debugging
        map.on("error", (e) => {{
            console.error("Map error:", e);
        }});
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
