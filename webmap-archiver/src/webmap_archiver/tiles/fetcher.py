"""
Tile fetching for coverage expansion.

Fetches missing tiles to ensure complete coverage of the session bounding box
at all zoom levels that were captured (or expanded to).

Key features:
- Calculates required tiles for bbox at each zoom level
- Identifies missing tiles by comparing to captured set
- Fetches with rate limiting and progress reporting
- Handles authentication failures gracefully
"""

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator
from urllib.parse import urlparse

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

from .coverage import GeoBounds, TileCoord


@dataclass
class FetchResult:
    """Result of a tile fetch operation."""
    coord: TileCoord
    content: bytes | None
    status: int | None = None
    error: str | None = None
    
    @property
    def success(self) -> bool:
        return self.content is not None and self.status == 200


@dataclass
class CoverageReport:
    """Report on coverage status before/after expansion."""
    bounds: GeoBounds
    zoom_levels: list[int]
    tiles_by_zoom: dict[int, int] = field(default_factory=dict)  # zoom -> count
    required_by_zoom: dict[int, int] = field(default_factory=dict)  # zoom -> count needed for full coverage
    missing_by_zoom: dict[int, int] = field(default_factory=dict)  # zoom -> count missing
    
    @property
    def total_captured(self) -> int:
        return sum(self.tiles_by_zoom.values())
    
    @property
    def total_required(self) -> int:
        return sum(self.required_by_zoom.values())
    
    @property
    def total_missing(self) -> int:
        return sum(self.missing_by_zoom.values())
    
    @property
    def coverage_percent(self) -> float:
        if self.total_required == 0:
            return 100.0
        return (self.total_captured / self.total_required) * 100


class TileMath:
    """Utilities for tile coordinate calculations."""
    
    @staticmethod
    def lon_to_tile_x(lon: float, zoom: int) -> int:
        """Convert longitude to tile X coordinate."""
        return int((lon + 180.0) / 360.0 * (1 << zoom))
    
    @staticmethod
    def lat_to_tile_y(lat: float, zoom: int) -> int:
        """Convert latitude to tile Y coordinate (TMS-style, Y increases southward)."""
        lat_rad = math.radians(lat)
        n = 1 << zoom
        return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    
    @staticmethod
    def tile_to_lon(x: int, zoom: int) -> float:
        """Convert tile X to longitude (west edge)."""
        return x / (1 << zoom) * 360.0 - 180.0
    
    @staticmethod
    def tile_to_lat(y: int, zoom: int) -> float:
        """Convert tile Y to latitude (north edge)."""
        n = math.pi - 2.0 * math.pi * y / (1 << zoom)
        return math.degrees(math.atan(math.sinh(n)))
    
    @classmethod
    def tiles_for_bounds(cls, bounds: GeoBounds, zoom: int) -> Iterator[TileCoord]:
        """
        Generate all tile coordinates covering the given bounds at zoom level.
        
        Yields TileCoord(z, x, y) for each tile.
        """
        min_x = cls.lon_to_tile_x(bounds.west, zoom)
        max_x = cls.lon_to_tile_x(bounds.east, zoom)
        min_y = cls.lat_to_tile_y(bounds.north, zoom)  # Note: north has smaller Y
        max_y = cls.lat_to_tile_y(bounds.south, zoom)
        
        # Clamp to valid tile range
        max_tile = (1 << zoom) - 1
        min_x = max(0, min_x)
        max_x = min(max_tile, max_x)
        min_y = max(0, min_y)
        max_y = min(max_tile, max_y)
        
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                yield TileCoord(zoom, x, y)
    
    @classmethod
    def count_tiles_for_bounds(cls, bounds: GeoBounds, zoom: int) -> int:
        """Count how many tiles cover the bounds at zoom level."""
        min_x = cls.lon_to_tile_x(bounds.west, zoom)
        max_x = cls.lon_to_tile_x(bounds.east, zoom)
        min_y = cls.lat_to_tile_y(bounds.north, zoom)
        max_y = cls.lat_to_tile_y(bounds.south, zoom)
        
        width = max_x - min_x + 1
        height = max_y - min_y + 1
        return width * height


class CoverageAnalyzer:
    """Analyze tile coverage and identify gaps."""
    
    def __init__(self, bounds: GeoBounds):
        self.bounds = bounds
        self.tile_math = TileMath()
    
    def analyze(
        self, 
        captured_tiles: set[TileCoord],
        expand_zoom: int = 0
    ) -> CoverageReport:
        """
        Analyze coverage of captured tiles against full bbox coverage.
        
        Args:
            captured_tiles: Set of TileCoord that were captured
            expand_zoom: Additional zoom levels to expand (0 = just fill gaps)
        
        Returns:
            CoverageReport with analysis
        """
        # Determine zoom levels from captured tiles
        captured_zooms = set(t.z for t in captured_tiles)
        if not captured_zooms:
            return CoverageReport(bounds=self.bounds, zoom_levels=[])
        
        min_zoom = min(captured_zooms)
        max_zoom = max(captured_zooms)
        
        # Expand zoom range if requested
        if expand_zoom > 0:
            max_zoom = min(22, max_zoom + expand_zoom)
        
        zoom_levels = list(range(min_zoom, max_zoom + 1))
        
        report = CoverageReport(
            bounds=self.bounds,
            zoom_levels=zoom_levels
        )
        
        for zoom in zoom_levels:
            # Count captured tiles at this zoom
            captured_at_zoom = sum(1 for t in captured_tiles if t.z == zoom)
            report.tiles_by_zoom[zoom] = captured_at_zoom
            
            # Count required tiles for full coverage
            required = self.tile_math.count_tiles_for_bounds(self.bounds, zoom)
            report.required_by_zoom[zoom] = required
            
            # Calculate missing
            report.missing_by_zoom[zoom] = max(0, required - captured_at_zoom)
        
        return report
    
    def find_missing_tiles(
        self,
        captured_tiles: set[TileCoord],
        zoom_levels: list[int]
    ) -> dict[int, list[TileCoord]]:
        """
        Find all missing tiles for full bbox coverage at specified zoom levels.
        
        Returns dict mapping zoom level to list of missing TileCoord.
        """
        missing = {}
        
        for zoom in zoom_levels:
            captured_at_zoom = set(t for t in captured_tiles if t.z == zoom)
            required = set(self.tile_math.tiles_for_bounds(self.bounds, zoom))
            missing_at_zoom = required - captured_at_zoom
            
            if missing_at_zoom:
                missing[zoom] = sorted(missing_at_zoom, key=lambda t: (t.z, t.x, t.y))
        
        return missing


class TileFetcher:
    """Fetch tiles from remote servers with rate limiting."""
    
    def __init__(
        self,
        rate_limit: float = 10.0,  # requests per second
        timeout: float = 30.0,
        max_retries: int = 2,
        user_agent: str = "WebMapArchiver/1.0"
    ):
        """
        Initialize fetcher.
        
        Args:
            rate_limit: Maximum requests per second
            timeout: Request timeout in seconds
            max_retries: Number of retries for failed requests
            user_agent: User-Agent header value
        """
        if not AIOHTTP_AVAILABLE:
            raise ImportError(
                "aiohttp is required for tile fetching. "
                "Install with: pip install aiohttp"
            )
        
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self._last_request_time = 0.0
        self._request_count = 0
    
    async def _rate_limit_wait(self):
        """Wait to respect rate limit."""
        if self.rate_limit <= 0:
            return
        
        now = time.time()
        min_interval = 1.0 / self.rate_limit
        elapsed = now - self._last_request_time
        
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        
        self._last_request_time = time.time()
    
    def _build_url(self, template: str, coord: TileCoord) -> str:
        """Build tile URL from template and coordinates."""
        return template.format(z=coord.z, x=coord.x, y=coord.y)
    
    async def fetch_tile(
        self,
        session: 'aiohttp.ClientSession',
        url: str,
        coord: TileCoord
    ) -> FetchResult:
        """Fetch a single tile with retries."""
        await self._rate_limit_wait()
        
        for attempt in range(self.max_retries + 1):
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    headers={"User-Agent": self.user_agent}
                ) as response:
                    self._request_count += 1
                    
                    if response.status == 200:
                        content = await response.read()
                        return FetchResult(
                            coord=coord,
                            content=content,
                            status=200
                        )
                    elif response.status in (401, 403):
                        # Auth failure - don't retry
                        return FetchResult(
                            coord=coord,
                            content=None,
                            status=response.status,
                            error="Authentication required"
                        )
                    elif response.status == 404:
                        # Tile doesn't exist - don't retry
                        return FetchResult(
                            coord=coord,
                            content=None,
                            status=404,
                            error="Tile not found"
                        )
                    else:
                        # Other error - may retry
                        if attempt < self.max_retries:
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                        return FetchResult(
                            coord=coord,
                            content=None,
                            status=response.status,
                            error=f"HTTP {response.status}"
                        )
                        
            except asyncio.TimeoutError:
                if attempt < self.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                return FetchResult(
                    coord=coord,
                    content=None,
                    error="Timeout"
                )
            except Exception as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                return FetchResult(
                    coord=coord,
                    content=None,
                    error=str(e)
                )
        
        return FetchResult(coord=coord, content=None, error="Max retries exceeded")
    
    async def fetch_tiles(
        self,
        url_template: str,
        coords: list[TileCoord],
        progress_callback: Callable[[int, int, TileCoord | None], None] | None = None,
        concurrency: int = 5
    ) -> list[FetchResult]:
        """
        Fetch multiple tiles with progress reporting.
        
        Args:
            url_template: URL template with {z}, {x}, {y} placeholders
            coords: List of tile coordinates to fetch
            progress_callback: Called with (completed, total, current_coord)
            concurrency: Number of concurrent requests
        
        Returns:
            List of FetchResult objects
        """
        results = []
        total = len(coords)
        completed = 0
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(concurrency)
        
        async def fetch_with_semaphore(session, coord):
            async with semaphore:
                url = self._build_url(url_template, coord)
                return await self.fetch_tile(session, url, coord)
        
        connector = aiohttp.TCPConnector(limit=concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Process in batches to report progress
            tasks = []
            for coord in coords:
                task = asyncio.create_task(fetch_with_semaphore(session, coord))
                tasks.append(task)
            
            # Gather results with progress updates
            for task in asyncio.as_completed(tasks):
                result = await task
                results.append(result)
                completed += 1
                
                if progress_callback:
                    progress_callback(completed, total, result.coord)
        
        return results


@dataclass
class ExpansionResult:
    """Result of coverage expansion operation."""
    source_name: str
    original_count: int
    fetched_count: int
    failed_count: int
    auth_failures: int
    new_tiles: list[tuple[TileCoord, bytes]]  # Successfully fetched tiles
    errors: list[str]
    
    @property
    def success_rate(self) -> float:
        total_attempted = self.fetched_count + self.failed_count
        if total_attempted == 0:
            return 100.0
        return (self.fetched_count / total_attempted) * 100


async def expand_coverage_async(
    url_template: str,
    source_name: str,
    captured_tiles: list[tuple[TileCoord, bytes]],
    bounds: GeoBounds,
    expand_zoom: int = 0,
    rate_limit: float = 10.0,
    progress_callback: Callable[[str, int, int], None] | None = None
) -> ExpansionResult:
    """
    Expand tile coverage to fill gaps in bounding box.
    
    Args:
        url_template: Tile URL template with {z}, {x}, {y}
        source_name: Name of the tile source
        captured_tiles: List of (TileCoord, bytes) already captured
        bounds: Geographic bounds to fill
        expand_zoom: Additional zoom levels beyond captured (0 = just fill gaps)
        rate_limit: Requests per second limit
        progress_callback: Called with (source_name, completed, total)
    
    Returns:
        ExpansionResult with fetched tiles and statistics
    """
    # Build set of captured coordinates
    captured_set = set(coord for coord, _ in captured_tiles)
    
    # Analyze coverage
    analyzer = CoverageAnalyzer(bounds)
    report = analyzer.analyze(captured_set, expand_zoom)
    
    # Find missing tiles
    missing = analyzer.find_missing_tiles(captured_set, report.zoom_levels)
    
    # Flatten missing tiles list
    all_missing = []
    for zoom, coords in missing.items():
        all_missing.extend(coords)
    
    if not all_missing:
        return ExpansionResult(
            source_name=source_name,
            original_count=len(captured_tiles),
            fetched_count=0,
            failed_count=0,
            auth_failures=0,
            new_tiles=[],
            errors=[]
        )
    
    # Fetch missing tiles
    fetcher = TileFetcher(rate_limit=rate_limit)
    
    def fetch_progress(completed, total, coord):
        if progress_callback:
            progress_callback(source_name, completed, total)
    
    results = await fetcher.fetch_tiles(
        url_template,
        all_missing,
        progress_callback=fetch_progress
    )
    
    # Process results
    new_tiles = []
    errors = []
    auth_failures = 0
    
    for result in results:
        if result.success:
            new_tiles.append((result.coord, result.content))
        else:
            if result.status in (401, 403):
                auth_failures += 1
            if result.error:
                errors.append(f"{result.coord}: {result.error}")
    
    return ExpansionResult(
        source_name=source_name,
        original_count=len(captured_tiles),
        fetched_count=len(new_tiles),
        failed_count=len(all_missing) - len(new_tiles),
        auth_failures=auth_failures,
        new_tiles=new_tiles,
        errors=errors[:10]  # Limit error list
    )


def expand_coverage(
    url_template: str,
    source_name: str,
    captured_tiles: list[tuple[TileCoord, bytes]],
    bounds: GeoBounds,
    expand_zoom: int = 0,
    rate_limit: float = 10.0,
    progress_callback: Callable[[str, int, int], None] | None = None
) -> ExpansionResult:
    """
    Synchronous wrapper for expand_coverage_async.
    """
    return asyncio.run(expand_coverage_async(
        url_template=url_template,
        source_name=source_name,
        captured_tiles=captured_tiles,
        bounds=bounds,
        expand_zoom=expand_zoom,
        rate_limit=rate_limit,
        progress_callback=progress_callback
    ))


def analyze_coverage(
    captured_tiles: list[tuple[TileCoord, bytes]],
    bounds: GeoBounds,
    expand_zoom: int = 0
) -> CoverageReport:
    """
    Analyze coverage without fetching.
    
    Returns report on current coverage and what's missing.
    """
    captured_set = set(coord for coord, _ in captured_tiles)
    analyzer = CoverageAnalyzer(bounds)
    return analyzer.analyze(captured_set, expand_zoom)
