"""
Extract original site assets from HAR for archival.

Preserves:
- HTML documents
- CSS stylesheets
- JavaScript files
- Images (png, jpg, svg, webp)
- Fonts (woff, woff2)
- JSON data files

Excludes:
- Analytics/tracking scripts
- Advertising resources
- Social media widgets
- Tile requests (handled separately)
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from typing import Iterator

from ..har.parser import HAREntry


@dataclass
class ExtractedAsset:
    """An asset extracted from HAR."""
    relative_path: str
    content: bytes
    mime_type: str
    original_url: str


class SiteExtractor:
    """Extract original site assets from HAR entries."""
    
    # MIME types to preserve
    PRESERVE_MIME_TYPES = {
        # Documents
        'text/html',
        'application/xhtml+xml',
        
        # Styles
        'text/css',
        
        # Scripts
        'text/javascript',
        'application/javascript',
        'application/x-javascript',
        
        # Images
        'image/png',
        'image/jpeg',
        'image/gif',
        'image/svg+xml',
        'image/webp',
        'image/x-icon',
        'image/vnd.microsoft.icon',
        
        # Fonts
        'font/woff',
        'font/woff2',
        'application/font-woff',
        'application/font-woff2',
        'application/x-font-woff',
        
        # Data
        'application/json',
        'application/geo+json',
        'application/ld+json',
    }
    
    # Domains to exclude (analytics, ads, etc.)
    EXCLUDE_DOMAIN_PATTERNS = [
        r'google-analytics\.com',
        r'googletagmanager\.com',
        r'googlesyndication\.com',
        r'doubleclick\.net',
        r'facebook\.com',
        r'facebook\.net',
        r'twitter\.com',
        r'linkedin\.com',
        r'analytics\.',
        r'tracking\.',
        r'adservice\.',
        r'ads\.',
        r'goatcounter\.com',
        r'plausible\.io',
        r'hotjar\.com',
        r'mixpanel\.com',
        r'segment\.com',
        r'amplitude\.com',
        r'sentry\.io',
        r'newrelic\.com',
        r'nr-data\.net',
    ]
    
    # URL patterns for tile requests (to exclude - handled separately)
    TILE_PATTERNS = [
        r'/\d+/\d+/\d+\.(pbf|mvt|png|jpg|jpeg|webp)(\?|$)',
        r'/tiles/',
        r'\.pmtiles',
        r'/v\d+/\d+/\d+/\d+',  # MapTiler style URLs
    ]
    
    # Patterns for map resources (sprites, glyphs) - handled separately
    MAP_RESOURCE_PATTERNS = [
        r'/sprite(@\d+x)?\.(png|json)',
        r'/fonts/[^/]+/\d+-\d+\.pbf',
        r'tiles\.json',
    ]
    
    def __init__(self, base_url: str | None = None):
        """
        Initialize extractor.
        
        Args:
            base_url: The main page URL (used to determine primary domain)
        """
        self.base_url = base_url
        self.base_domain = urlparse(base_url).netloc if base_url else None
        
        # Compile patterns
        self._exclude_patterns = [
            re.compile(p, re.IGNORECASE) 
            for p in self.EXCLUDE_DOMAIN_PATTERNS
        ]
        self._tile_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self.TILE_PATTERNS
        ]
        self._map_resource_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self.MAP_RESOURCE_PATTERNS
        ]
    
    def _should_exclude_domain(self, domain: str) -> bool:
        """Check if domain should be excluded."""
        return any(p.search(domain) for p in self._exclude_patterns)
    
    def _is_tile_request(self, url: str) -> bool:
        """Check if URL is a map tile request."""
        return any(p.search(url) for p in self._tile_patterns)
    
    def _is_map_resource(self, url: str) -> bool:
        """Check if URL is a map resource (sprite, glyph)."""
        return any(p.search(url) for p in self._map_resource_patterns)
    
    def _get_mime_type(self, entry: HAREntry) -> str:
        """Get normalized MIME type from entry."""
        mime = entry.mime_type or ''
        # Strip charset and parameters
        return mime.split(';')[0].strip().lower()
    
    def _url_to_path(self, url: str) -> str:
        """Convert URL to relative file path."""
        parsed = urlparse(url)
        
        # Determine if same domain or external
        if self.base_domain and parsed.netloc == self.base_domain:
            # Same domain - use path directly
            path = parsed.path.lstrip('/')
        elif not parsed.netloc:
            # Relative URL - use path directly
            path = parsed.path.lstrip('/')
        else:
            # External domain - preserve under _external/
            path = f"_external/{parsed.netloc}{parsed.path}"
        
        # Handle empty path or directory
        if not path or path.endswith('/'):
            path = path + 'index.html'
        
        # Sanitize path components
        parts = path.split('/')
        sanitized = []
        for part in parts:
            # Remove potentially problematic characters
            clean = re.sub(r'[<>:"|?*\\]', '_', part)
            # Limit length
            if len(clean) > 200:
                clean = clean[:200]
            if clean:  # Skip empty parts
                sanitized.append(clean)
        
        return '/'.join(sanitized) if sanitized else 'index.html'
    
    def extract(self, entries: list[HAREntry]) -> Iterator[ExtractedAsset]:
        """
        Extract site assets from HAR entries.
        
        Yields ExtractedAsset objects for each preserved resource.
        """
        seen_paths = set()
        
        for entry in entries:
            # Skip failed requests
            if entry.status < 200 or entry.status >= 300:
                continue
            
            # Skip entries without content
            if not entry.content:
                continue
            
            url = entry.url
            parsed = urlparse(url)
            
            # Skip excluded domains
            if self._should_exclude_domain(parsed.netloc):
                continue
            
            # Skip tile requests (handled separately)
            if self._is_tile_request(url):
                continue
            
            # Skip map resources (handled separately by resource bundler)
            if self._is_map_resource(url):
                continue
            
            # Check MIME type
            mime_type = self._get_mime_type(entry)
            if mime_type not in self.PRESERVE_MIME_TYPES:
                continue
            
            # Convert to relative path
            rel_path = self._url_to_path(url)
            
            # Skip duplicates
            if rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)
            
            yield ExtractedAsset(
                relative_path=rel_path,
                content=entry.content,
                mime_type=mime_type,
                original_url=url
            )
    
    def extract_to_directory(
        self, 
        entries: list[HAREntry], 
        output_dir: Path
    ) -> list[ExtractedAsset]:
        """
        Extract assets and write to directory.
        
        Returns list of extracted assets.
        """
        assets = []
        
        for asset in self.extract(entries):
            # Create directory structure
            file_path = output_dir / asset.relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write content
            file_path.write_bytes(asset.content)
            
            assets.append(asset)
        
        return assets
    
    def get_base_url_from_entries(self, entries: list[HAREntry]) -> str | None:
        """
        Detect the base URL from HAR entries.
        
        Looks for the first HTML document request.
        """
        for entry in entries:
            if entry.status >= 200 and entry.status < 300:
                mime = self._get_mime_type(entry)
                if mime == 'text/html':
                    return entry.url
        return None
