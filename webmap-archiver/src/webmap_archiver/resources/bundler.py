"""
Resource bundling for map assets.

Handles:
- Sprite atlas (PNG + JSON) extraction
- Glyph/font range extraction
- Style.json rewriting for local/offline use
"""

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ..har.parser import HAREntry


@dataclass
class SpriteBundle:
    """Bundled sprite atlas resources."""
    png_1x: bytes | None = None
    png_2x: bytes | None = None
    json_1x: dict | None = None
    json_2x: dict | None = None
    
    @property
    def has_sprites(self) -> bool:
        """Check if any sprite resources were found."""
        return any([self.png_1x, self.png_2x, self.json_1x, self.json_2x])
    
    def write_to_directory(self, output_dir: Path, name: str = "sprite") -> dict:
        """
        Write sprite files to directory.
        
        Returns dict with paths written.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        written = {}
        
        if self.png_1x:
            path = output_dir / f"{name}.png"
            path.write_bytes(self.png_1x)
            written['png_1x'] = str(path)
        
        if self.png_2x:
            path = output_dir / f"{name}@2x.png"
            path.write_bytes(self.png_2x)
            written['png_2x'] = str(path)
        
        if self.json_1x:
            path = output_dir / f"{name}.json"
            path.write_text(json.dumps(self.json_1x, indent=2))
            written['json_1x'] = str(path)
        
        if self.json_2x:
            path = output_dir / f"{name}@2x.json"
            path.write_text(json.dumps(self.json_2x, indent=2))
            written['json_2x'] = str(path)
        
        return written


@dataclass
class GlyphRange:
    """A single glyph range file."""
    font_stack: str
    range_start: int
    range_end: int
    content: bytes
    
    @property
    def filename(self) -> str:
        """Get the filename for this glyph range."""
        return f"{self.range_start}-{self.range_end}.pbf"


@dataclass 
class GlyphBundle:
    """Collection of glyph ranges organized by font stack."""
    ranges: list[GlyphRange] = field(default_factory=list)
    
    @property
    def font_stacks(self) -> list[str]:
        """Get unique font stack names."""
        return list(set(r.font_stack for r in self.ranges))
    
    @property
    def has_glyphs(self) -> bool:
        """Check if any glyph resources were found."""
        return len(self.ranges) > 0
    
    def write_to_directory(self, output_dir: Path) -> dict:
        """
        Write glyph files to directory structure.
        
        Creates: output_dir/{font_stack}/{start}-{end}.pbf
        
        Returns dict with font stacks and counts.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        written = {}
        
        for glyph in self.ranges:
            # Sanitize font stack name for filesystem
            safe_name = re.sub(r'[<>:"|?*\\]', '_', glyph.font_stack)
            font_dir = output_dir / safe_name
            font_dir.mkdir(parents=True, exist_ok=True)
            
            path = font_dir / glyph.filename
            path.write_bytes(glyph.content)
            
            if safe_name not in written:
                written[safe_name] = 0
            written[safe_name] += 1
        
        return written


class SpriteBundler:
    """Extract and bundle sprite resources from HAR."""
    
    # Patterns to match sprite URLs
    SPRITE_PNG_1X = re.compile(r'/sprite\.png(\?|$)', re.IGNORECASE)
    SPRITE_PNG_2X = re.compile(r'/sprite@2x\.png(\?|$)', re.IGNORECASE)
    SPRITE_JSON_1X = re.compile(r'/sprite\.json(\?|$)', re.IGNORECASE)
    SPRITE_JSON_2X = re.compile(r'/sprite@2x\.json(\?|$)', re.IGNORECASE)
    
    def extract(self, entries: list[HAREntry]) -> SpriteBundle:
        """Extract sprite files from HAR entries."""
        bundle = SpriteBundle()
        
        for entry in entries:
            if not entry.content or entry.status < 200 or entry.status >= 300:
                continue
            
            url = entry.url
            
            if self.SPRITE_PNG_2X.search(url):
                bundle.png_2x = entry.content
            elif self.SPRITE_PNG_1X.search(url):
                bundle.png_1x = entry.content
            elif self.SPRITE_JSON_2X.search(url):
                try:
                    bundle.json_2x = json.loads(entry.content)
                except json.JSONDecodeError:
                    pass
            elif self.SPRITE_JSON_1X.search(url):
                try:
                    bundle.json_1x = json.loads(entry.content)
                except json.JSONDecodeError:
                    pass
        
        return bundle


class GlyphBundler:
    """Extract and bundle glyph resources from HAR."""
    
    # Pattern: /fonts/{fontstack}/{range}.pbf
    # Examples:
    #   /fonts/Open Sans Regular/0-255.pbf
    #   /fonts/Noto%20Sans%20Bold/256-511.pbf
    GLYPH_PATTERN = re.compile(
        r'/fonts/(?P<fontstack>[^/]+)/(?P<start>\d+)-(?P<end>\d+)\.pbf',
        re.IGNORECASE
    )
    
    def extract(self, entries: list[HAREntry]) -> GlyphBundle:
        """Extract glyph files from HAR entries."""
        bundle = GlyphBundle()
        seen = set()  # Avoid duplicates
        
        for entry in entries:
            if not entry.content or entry.status < 200 or entry.status >= 300:
                continue
            
            # URL decode the path for matching
            from urllib.parse import unquote
            decoded_url = unquote(entry.url)
            
            match = self.GLYPH_PATTERN.search(decoded_url)
            if match:
                font_stack = match.group('fontstack')
                start = int(match.group('start'))
                end = int(match.group('end'))
                
                key = (font_stack, start, end)
                if key not in seen:
                    seen.add(key)
                    bundle.ranges.append(GlyphRange(
                        font_stack=font_stack,
                        range_start=start,
                        range_end=end,
                        content=entry.content
                    ))
        
        return bundle


class StyleRewriter:
    """Rewrite style.json for local/offline use."""
    
    def rewrite(
        self,
        style: dict,
        pmtiles_sources: dict[str, str] | None = None,
        sprite_path: str | None = None,
        glyphs_path: str | None = None,
    ) -> dict:
        """
        Rewrite style.json sources for local use.
        
        Args:
            style: Original style.json dict
            pmtiles_sources: Mapping of source name to local PMTiles path
            sprite_path: Local sprite path (without extension)
            glyphs_path: Local glyphs path template with {fontstack} and {range}
        
        Returns:
            Modified style dict (deep copy, original unchanged)
        """
        style = copy.deepcopy(style)
        
        # Rewrite sources to use PMTiles
        if pmtiles_sources:
            for source_name, source_def in style.get('sources', {}).items():
                if source_name in pmtiles_sources:
                    local_path = pmtiles_sources[source_name]
                    # Use pmtiles:// protocol for MapLibre PMTiles plugin
                    source_def['url'] = f'pmtiles://{local_path}'
                    # Remove tiles array if present (pmtiles:// replaces it)
                    source_def.pop('tiles', None)
                    # Remove tilejson URL if present
                    if 'url' in source_def and 'tiles.json' in source_def.get('url', ''):
                        pass  # Already replaced above
        
        # Rewrite sprite URL
        if sprite_path and 'sprite' in style:
            style['sprite'] = sprite_path
        
        # Rewrite glyphs URL
        if glyphs_path and 'glyphs' in style:
            style['glyphs'] = glyphs_path
        
        return style
    
    def extract_source_urls(self, style: dict) -> dict[str, str]:
        """
        Extract tile source URLs from style.
        
        Returns dict mapping source name to tile URL template.
        """
        sources = {}
        
        for source_name, source_def in style.get('sources', {}).items():
            if source_def.get('type') == 'vector':
                # Check for tiles array first
                tiles = source_def.get('tiles', [])
                if tiles:
                    sources[source_name] = tiles[0]
                # Check for tilejson URL
                elif 'url' in source_def:
                    sources[source_name] = source_def['url']
            elif source_def.get('type') == 'raster':
                tiles = source_def.get('tiles', [])
                if tiles:
                    sources[source_name] = tiles[0]
        
        return sources


def extract_all_resources(entries: list[HAREntry]) -> tuple[SpriteBundle, GlyphBundle]:
    """
    Convenience function to extract all map resources from HAR.
    
    Returns (sprite_bundle, glyph_bundle) tuple.
    """
    sprite_bundler = SpriteBundler()
    glyph_bundler = GlyphBundler()
    
    sprites = sprite_bundler.extract(entries)
    glyphs = glyph_bundler.extract(entries)
    
    return sprites, glyphs
