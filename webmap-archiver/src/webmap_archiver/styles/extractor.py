"""
Extract styling information from JavaScript files in HAR.

BACKGROUND:
Data layer styling is typically NOT in style.json—it's added programmatically
by the web application JavaScript at runtime. However, the JS files ARE captured
in the HAR, and we can extract useful styling information via regex patterns.

PHASE 1 APPROACH:
- Regex-based extraction of common patterns
- Extract: colors, source-layer names, basic paint properties
- Works for ~80% of common cases

LIMITATIONS:
- Minified code uses single-letter variable names (D, V, w, G, etc.)
- Complex expressions may span multiple variables
- Some patterns may be missed by regex
- Results should be validated against actual tile data
"""

from dataclasses import dataclass, field
from typing import Any
import re
import json


@dataclass
class ExtractedLayerStyle:
    """Styling information extracted from JavaScript."""
    source_id: str | None = None
    source_layer: str | None = None
    tile_url: str | None = None
    layer_type: str | None = None  # "line", "fill", "circle", etc.
    colors: dict[str, str] = field(default_factory=dict)  # category -> hex color
    paint_properties: dict[str, Any] = field(default_factory=dict)

    # Metadata about extraction quality
    extraction_confidence: float = 0.0  # 0.0 - 1.0
    extraction_notes: list[str] = field(default_factory=list)
    raw_matches: dict[str, str] = field(default_factory=dict)  # For debugging


@dataclass
class StyleExtractionReport:
    """Report on what styling was/wasn't extracted."""
    extracted_layers: list[ExtractedLayerStyle]
    unmatched_sources: list[str]  # Tile sources with no extracted styling
    js_files_analyzed: int
    extraction_method: str = "regex_v1"
    notes: list[str] = field(default_factory=list)

    def to_manifest_section(self) -> dict:
        """Generate manifest section documenting extraction results."""
        return {
            "style_extraction": {
                "method": self.extraction_method,
                "method_description": "Regex-based extraction from minified JavaScript",
                "limitations": [
                    "Complex MapLibre expressions may be simplified or incomplete",
                    "Interactive states (hover, click) not fully captured",
                    "Minified variable names require pattern matching heuristics",
                    "Some layer properties may be missing"
                ],
                "future_improvements": [
                    "JavaScript AST parsing for complete expression extraction",
                    "Runtime style capture via browser extension",
                    "User-provided layer configuration override"
                ],
                "layers_extracted": len(self.extracted_layers),
                "sources_without_styling": self.unmatched_sources,
                "js_files_analyzed": self.js_files_analyzed,
                "notes": self.notes,
                "layers": [
                    {
                        "source_id": layer.source_id,
                        "source_layer": layer.source_layer,
                        "layer_type": layer.layer_type,
                        "colors_extracted": len(layer.colors),
                        "confidence": layer.extraction_confidence,
                        "notes": layer.extraction_notes
                    }
                    for layer in self.extracted_layers
                ]
            }
        }


class StyleExtractor:
    """Extract layer styling from JavaScript files."""

    # Patterns for common MapLibre/Mapbox styling constructs
    PATTERNS = {
        # Hex color mappings: {category:"#hexcolor",...}
        'color_object': re.compile(
            r'\{[a-z_]+:"#[0-9a-fA-F]{6}"(?:,[a-z_]+:"#[0-9a-fA-F]{6}")*\}'
        ),

        # Individual color assignments: category:"#hexcolor"
        'color_pair': re.compile(
            r'([a-z_]+):"(#[0-9a-fA-F]{6})"'
        ),

        # Tile URL patterns
        'tile_url': re.compile(
            r'(https?://[^"\']+/\{z\}/\{x\}/\{y\}[^"\'\s]*)'
        ),

        # Source-layer string (often a specific identifier)
        'source_layer': re.compile(
            r'"source-layer"\s*:\s*([A-Za-z_][A-Za-z0-9_]*)|'
            r'"source-layer"\s*:\s*"([^"]+)"'
        ),

        # Layer type
        'layer_type': re.compile(
            r'type\s*:\s*"(line|fill|circle|symbol)"'
        ),

        # Paint properties
        'line_width': re.compile(
            r'"line-width"\s*:\s*(\d+(?:\.\d+)?|\[[^\]]+\])'
        ),
        'line_opacity': re.compile(
            r'"line-opacity"\s*:\s*(\d+(?:\.\d+)?|\[[^\]]+\])'
        ),
        'fill_opacity': re.compile(
            r'"fill-opacity"\s*:\s*(\d+(?:\.\d+)?|\[[^\]]+\])'
        ),
    }

    def extract_from_js(self, js_content: str, source_url: str = "") -> list[ExtractedLayerStyle]:
        """
        Extract styling information from JavaScript content.

        Args:
            js_content: The JavaScript file content
            source_url: URL of the JS file (for reporting)

        Returns:
            List of extracted layer styles
        """
        extracted = []

        # Find all tile URLs in the JS
        tile_urls = self.PATTERNS['tile_url'].findall(js_content)

        for tile_url in tile_urls:
            # Skip common basemap URLs
            if any(provider in tile_url.lower() for provider in
                   ['maptiler', 'mapbox', 'arcgis', 'openstreetmap', 'carto']):
                continue

            style = ExtractedLayerStyle(tile_url=tile_url)
            style.extraction_notes.append(f"Found tile URL: {tile_url}")

            # Try to find associated styling near this URL in the code
            url_pos = js_content.find(tile_url)
            if url_pos >= 0:
                # Search in a window around the URL
                window_start = max(0, url_pos - 2000)
                window_end = min(len(js_content), url_pos + 2000)
                context = js_content[window_start:window_end]

                # Extract colors
                self._extract_colors(context, style)

                # Extract source-layer
                self._extract_source_layer(context, style)

                # Extract layer type
                self._extract_layer_type(context, style)

                # Extract paint properties
                self._extract_paint_properties(context, style)

            # Also do a global search for color objects
            if not style.colors:
                self._extract_colors(js_content, style)

            # Calculate confidence
            style.extraction_confidence = self._calculate_confidence(style)

            if style.colors or style.source_layer:
                extracted.append(style)

        return extracted

    def _extract_colors(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract color mappings from content."""
        # Find color object patterns
        color_objects = self.PATTERNS['color_object'].findall(content)

        for obj_str in color_objects:
            pairs = self.PATTERNS['color_pair'].findall(obj_str)
            for category, color in pairs:
                style.colors[category] = color

        if style.colors:
            style.extraction_notes.append(f"Extracted {len(style.colors)} color mappings")
            style.raw_matches['colors'] = str(style.colors)

    def _extract_source_layer(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract source-layer name."""
        matches = self.PATTERNS['source_layer'].findall(content)
        for match in matches:
            # match is a tuple from the alternation groups
            source_layer = match[0] or match[1]
            if source_layer and source_layer not in ('null', 'undefined'):
                style.source_layer = source_layer
                style.extraction_notes.append(f"Found source-layer: {source_layer}")
                break

    def _extract_layer_type(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract layer type (line, fill, circle, etc.)."""
        matches = self.PATTERNS['layer_type'].findall(content)
        if matches:
            style.layer_type = matches[0]
            style.extraction_notes.append(f"Found layer type: {style.layer_type}")

    def _extract_paint_properties(self, content: str, style: ExtractedLayerStyle) -> None:
        """Extract paint properties."""
        for prop_name in ['line_width', 'line_opacity', 'fill_opacity']:
            matches = self.PATTERNS[prop_name].findall(content)
            if matches:
                # Convert prop_name to CSS property name
                css_name = prop_name.replace('_', '-')
                try:
                    # Try to parse as number or JSON
                    value = matches[0]
                    if value.startswith('['):
                        style.paint_properties[css_name] = json.loads(value)
                    else:
                        style.paint_properties[css_name] = float(value)
                except (json.JSONDecodeError, ValueError):
                    style.paint_properties[css_name] = matches[0]

    def _calculate_confidence(self, style: ExtractedLayerStyle) -> float:
        """Calculate confidence score for extraction."""
        score = 0.0

        if style.tile_url:
            score += 0.2
        if style.source_layer:
            score += 0.2
        if style.layer_type:
            score += 0.1
        if style.colors:
            # More colors = higher confidence
            score += min(0.3, len(style.colors) * 0.05)
        if style.paint_properties:
            score += min(0.2, len(style.paint_properties) * 0.05)

        return min(1.0, score)


def extract_styles_from_har(
    entries: list,
    detected_tile_sources: list[str]
) -> StyleExtractionReport:
    """
    Extract styling from all JavaScript files in HAR.

    Args:
        entries: Parsed HAR entries
        detected_tile_sources: List of tile source URLs found in HAR

    Returns:
        StyleExtractionReport with extraction results
    """
    extractor = StyleExtractor()
    all_extracted = []
    js_count = 0

    for entry in entries:
        # Check if this is a JavaScript file
        mime = entry.mime_type.lower()
        url = entry.url.lower()

        if 'javascript' in mime or url.endswith('.js'):
            if entry.content:
                js_count += 1
                try:
                    js_text = entry.content.decode('utf-8')
                    extracted = extractor.extract_from_js(js_text, entry.url)
                    all_extracted.extend(extracted)
                except UnicodeDecodeError:
                    pass

    # Determine which sources still have no styling
    extracted_urls = {s.tile_url for s in all_extracted if s.tile_url}
    unmatched = [url for url in detected_tile_sources if url not in extracted_urls]

    report = StyleExtractionReport(
        extracted_layers=all_extracted,
        unmatched_sources=unmatched,
        js_files_analyzed=js_count,
        notes=[
            f"Analyzed {js_count} JavaScript files",
            f"Extracted styling for {len(all_extracted)} layers",
            f"{len(unmatched)} tile sources have no extracted styling"
        ]
    )

    return report
