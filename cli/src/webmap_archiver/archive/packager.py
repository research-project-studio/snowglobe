"""
Package all components into a ZIP archive.

Key requirements:
- Create proper directory structure
- Include PMTiles, viewer, manifest
- Generate manifest.json with metadata
"""

from pathlib import Path
from datetime import datetime
import zipfile
import json
from dataclasses import dataclass, asdict

from ..tiles.coverage import GeoBounds


@dataclass
class TileSourceInfo:
    """Information about a tile source in the archive."""
    name: str
    path: str
    tile_type: str
    format: str
    tile_count: int
    zoom_range: tuple[int, int]
    url_pattern: str | None = None  # Original tile URL pattern for source matching


@dataclass
class ArchiveManifest:
    """Manifest describing the archive contents."""
    name: str
    description: str
    created_at: str
    version: str
    bounds: dict
    zoom_range: tuple[int, int]
    tile_sources: list[dict]
    viewer_path: str
    archive_mode: str = "full"
    style_extraction: dict = None  # Added: documents what styling was/wasn't extracted
    known_limitations: list[dict] = None  # Added: documents limitations for future work

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "version": self.version,
            "bounds": self.bounds,
            "zoom_range": list(self.zoom_range),
            "tile_sources": self.tile_sources,
            "viewer_path": self.viewer_path,
            "archive_mode": self.archive_mode,
        }

        # Include style extraction report if available
        if self.style_extraction:
            result["style_extraction"] = self.style_extraction

        # Include known limitations for future refinement
        if self.known_limitations:
            result["known_limitations"] = self.known_limitations
        else:
            # Default limitations documentation
            result["known_limitations"] = [
                {
                    "id": "style_extraction_incomplete",
                    "area": "Data Layer Styling",
                    "description": "Styling for data layers added via JavaScript may be incomplete or simplified",
                    "impact": "Visual appearance may not match original map exactly",
                    "current_approach": "Regex-based extraction from minified JavaScript",
                    "future_improvements": [
                        "JavaScript AST parsing for complete expression extraction",
                        "Runtime style capture via browser extension calling map.getStyle()",
                        "User-provided layer configuration override file"
                    ],
                    "workaround": "Manually edit style/extracted_layers.json to refine styling"
                },
                {
                    "id": "interactive_states_missing",
                    "area": "Interactivity",
                    "description": "Hover, click, and other interactive states not captured",
                    "impact": "Map is static view only",
                    "current_approach": "Not implemented in Phase 1",
                    "future_improvements": [
                        "Extract feature-state expressions from JavaScript",
                        "Capture event handlers and popup content"
                    ]
                },
                {
                    "id": "basemap_style_simplified",
                    "area": "Basemap Styling",
                    "description": "Basemap uses captured style.json but sprites/glyphs may be missing",
                    "impact": "Labels and icons may not render",
                    "current_approach": "Style.json captured, sprites/glyphs not bundled in Phase 1",
                    "future_improvements": [
                        "Bundle sprite atlas and JSON",
                        "Bundle required glyph ranges",
                        "Rewrite URLs in style.json to local paths"
                    ]
                }
            ]

        return result


class ArchivePackager:
    """Package map archive into a ZIP file."""

    VERSION = "1.0.0"

    def __init__(self, output_path: Path):
        self.output_path = Path(output_path)
        self.temp_files: list[tuple[str, Path | bytes]] = []
        self.manifest: ArchiveManifest | None = None

    def add_pmtiles(self, name: str, pmtiles_path: Path) -> None:
        """Add a PMTiles file to the archive."""
        archive_path = f"tiles/{name}.pmtiles"
        self.temp_files.append((archive_path, pmtiles_path))

    def add_viewer(self, html_content: str) -> None:
        """Add the viewer HTML to the archive."""
        self.temp_files.append(("viewer.html", html_content.encode('utf-8')))

    def set_manifest(
        self,
        name: str,
        description: str,
        bounds: GeoBounds,
        zoom_range: tuple[int, int],
        tile_sources: list[TileSourceInfo],
        style_extraction: dict = None
    ) -> None:
        """Set the archive manifest."""
        self.manifest = ArchiveManifest(
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
            version=self.VERSION,
            bounds={
                "west": bounds.west,
                "south": bounds.south,
                "east": bounds.east,
                "north": bounds.north,
            },
            zoom_range=zoom_range,
            tile_sources=[
                {
                    "name": ts.name,
                    "path": ts.path,
                    "tile_type": ts.tile_type,
                    "format": ts.format,
                    "tile_count": ts.tile_count,
                    "zoom_range": list(ts.zoom_range),
                }
                for ts in tile_sources
            ],
            viewer_path="viewer.html",
            style_extraction=style_extraction,
        )

    def build(self) -> None:
        """Build the ZIP archive."""
        if not self.manifest:
            raise ValueError("Manifest not set")

        with zipfile.ZipFile(self.output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add manifest
            manifest_json = json.dumps(self.manifest.to_dict(), indent=2)
            zf.writestr("manifest.json", manifest_json)

            # Add all files
            for archive_path, content in self.temp_files:
                if isinstance(content, Path):
                    zf.write(content, archive_path)
                else:
                    zf.writestr(archive_path, content)
