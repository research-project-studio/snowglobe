# Fix Coverage Expansion Bug

## Problem

The coverage expansion is calculating tiles for ALL zoom levels (0-22) across the bounding box, resulting in attempts to fetch 134+ million tiles. This causes Modal to timeout/cancel.

**Current broken behavior:**
```
Coverage: 0.0% (23/134258694 tiles)
Fetching 134258671 additional tiles...
[modal-client] Received a cancellation signal...
```

**Expected behavior:**
```
Captured zoom range: z12-z14
Target zoom range: z12-z15 (expanding by 1 level)
Fetching 87 additional tiles...
```

## Root Cause

The `analyze_coverage()` function is not constraining the zoom range. It's calculating coverage for z0-z22 (or similar) instead of only the captured zoom levels + 1 expansion level.

## Files to Modify

1. `cli/src/webmap_archiver/tiles/fetcher.py` - Fix coverage analysis and expansion
2. `cli/src/webmap_archiver/api.py` - Add safety limits

---

## Part 1: Fix Coverage Analysis

**File:** `cli/src/webmap_archiver/tiles/fetcher.py`

Find the `analyze_coverage()` function and the `CoverageReport` class. Update them to respect zoom bounds.

### Step 1.1: Update CoverageReport

Find the `CoverageReport` dataclass and ensure it has zoom range fields:

```python
@dataclass
class CoverageReport:
    """Report on tile coverage for a source."""
    source_name: str
    captured_count: int
    total_possible: int  # Within the CONSTRAINED zoom range
    missing_count: int
    coverage_percent: float
    bounds: GeoBounds
    min_zoom: int  # ADD: Minimum zoom in captured tiles
    max_zoom: int  # ADD: Maximum zoom in captured tiles
    tiles_per_zoom: dict[int, int]  # ADD: Count per zoom level
    
    @property
    def total_missing(self) -> int:
        return self.missing_count
```

### Step 1.2: Fix analyze_coverage Function

Find `analyze_coverage()` and update it to constrain zoom range:

```python
def analyze_coverage(
    captured_tiles: list[tuple[TileCoordinate, bytes]],
    bounds: GeoBounds,
    source_name: str = "unknown",
    expand_zoom: int = 1,  # ADD this parameter
) -> CoverageReport:
    """
    Analyze coverage of captured tiles within bounds.
    
    Only analyzes the captured zoom range + expand_zoom levels,
    NOT all possible zoom levels.
    """
    if not captured_tiles:
        return CoverageReport(
            source_name=source_name,
            captured_count=0,
            total_possible=0,
            missing_count=0,
            coverage_percent=0.0,
            bounds=bounds,
            min_zoom=0,
            max_zoom=0,
            tiles_per_zoom={},
        )
    
    # Extract coordinates
    captured_coords = [coord for coord, _ in captured_tiles]
    captured_set = {(c.z, c.x, c.y) for c in captured_coords}
    
    # Determine zoom range from captured tiles
    captured_zooms = [c.z for c in captured_coords]
    min_zoom = min(captured_zooms)
    max_zoom = max(captured_zooms)
    
    # Target range: captured range + expansion (capped at z18 for sanity)
    target_max_zoom = min(max_zoom + expand_zoom, 18)
    
    # Count tiles per zoom level
    tiles_per_zoom = {}
    for z in range(min_zoom, target_max_zoom + 1):
        tiles_per_zoom[z] = sum(1 for c in captured_coords if c.z == z)
    
    # Calculate total possible tiles ONLY within the target zoom range
    total_possible = 0
    missing_count = 0
    
    for z in range(min_zoom, target_max_zoom + 1):
        # Get tile bounds at this zoom level
        tiles_at_zoom = list(tiles_in_bounds(bounds, z))
        total_possible += len(tiles_at_zoom)
        
        # Count missing
        for coord in tiles_at_zoom:
            if (coord.z, coord.x, coord.y) not in captured_set:
                missing_count += 1
    
    captured_count = len(captured_tiles)
    coverage_percent = (captured_count / total_possible * 100) if total_possible > 0 else 0.0
    
    return CoverageReport(
        source_name=source_name,
        captured_count=captured_count,
        total_possible=total_possible,
        missing_count=missing_count,
        coverage_percent=coverage_percent,
        bounds=bounds,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        tiles_per_zoom=tiles_per_zoom,
    )
```

### Step 1.3: Fix expand_coverage_async Function

Find `expand_coverage_async()` and update it:

```python
async def expand_coverage_async(
    url_template: str,
    source_name: str,
    captured_tiles: list[tuple[TileCoordinate, bytes]],
    bounds: GeoBounds,
    expand_zoom: int = 1,
    rate_limit: float = 10.0,
    max_tiles: int = 500,  # ADD: Safety limit
    progress_callback=None,
) -> ExpansionResult:
    """
    Expand tile coverage by fetching missing tiles.
    
    Only fetches tiles within the captured zoom range + expand_zoom levels.
    Limited to max_tiles to prevent runaway fetching.
    """
    if not captured_tiles:
        return ExpansionResult(
            source_name=source_name,
            fetched_count=0,
            failed_count=0,
            tiles=[],
        )
    
    # Extract coordinates and determine zoom range
    captured_coords = [coord for coord, _ in captured_tiles]
    captured_set = {(c.z, c.x, c.y) for c in captured_coords}
    
    captured_zooms = [c.z for c in captured_coords]
    min_zoom = min(captured_zooms)
    max_zoom = max(captured_zooms)
    target_max_zoom = min(max_zoom + expand_zoom, 18)  # Cap at z18
    
    print(f"    Captured zoom range: z{min_zoom}-z{max_zoom}", flush=True)
    print(f"    Target zoom range: z{min_zoom}-z{target_max_zoom}", flush=True)
    
    # Collect tiles to fetch (only within target zoom range)
    tiles_to_fetch: list[TileCoordinate] = []
    
    for z in range(min_zoom, target_max_zoom + 1):
        tiles_at_zoom = list(tiles_in_bounds(bounds, z))
        for coord in tiles_at_zoom:
            if (coord.z, coord.x, coord.y) not in captured_set:
                tiles_to_fetch.append(coord)
    
    print(f"    Missing tiles: {len(tiles_to_fetch)}", flush=True)
    
    # Safety limit
    if len(tiles_to_fetch) > max_tiles:
        print(f"    Limiting to {max_tiles} tiles (was {len(tiles_to_fetch)})", flush=True)
        # Prioritize higher zoom levels (more detail)
        tiles_to_fetch.sort(key=lambda c: -c.z)
        tiles_to_fetch = tiles_to_fetch[:max_tiles]
    
    if not tiles_to_fetch:
        print(f"    No tiles to fetch - coverage is complete", flush=True)
        return ExpansionResult(
            source_name=source_name,
            fetched_count=0,
            failed_count=0,
            tiles=[],
        )
    
    print(f"    Fetching {len(tiles_to_fetch)} tiles...", flush=True)
    
    # Fetch tiles with rate limiting
    fetcher = TileFetcher(
        url_template=url_template,
        rate_limit=rate_limit,
    )
    
    fetched_tiles = []
    failed_count = 0
    
    async with aiohttp.ClientSession() as session:
        for i, coord in enumerate(tiles_to_fetch):
            try:
                content = await fetcher.fetch_tile(session, coord)
                if content:
                    fetched_tiles.append((coord, content))
                else:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
                if failed_count <= 3:  # Only log first few failures
                    print(f"    Failed to fetch {coord.z}/{coord.x}/{coord.y}: {e}", flush=True)
            
            # Progress callback
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(i + 1, len(tiles_to_fetch))
    
    print(f"    Fetched {len(fetched_tiles)} tiles, {failed_count} failed", flush=True)
    
    return ExpansionResult(
        source_name=source_name,
        fetched_count=len(fetched_tiles),
        failed_count=failed_count,
        tiles=fetched_tiles,
    )
```

### Step 1.4: Ensure tiles_in_bounds Helper Exists

Make sure there's a helper function to get tiles within bounds at a specific zoom:

```python
def tiles_in_bounds(bounds: GeoBounds, zoom: int) -> Iterator[TileCoordinate]:
    """
    Yield all tile coordinates that intersect the given bounds at a zoom level.
    """
    # Convert geographic bounds to tile coordinates
    min_x = lon_to_tile_x(bounds.min_lon, zoom)
    max_x = lon_to_tile_x(bounds.max_lon, zoom)
    min_y = lat_to_tile_y(bounds.max_lat, zoom)  # Note: lat is inverted
    max_y = lat_to_tile_y(bounds.min_lat, zoom)
    
    # Clamp to valid tile range
    max_tile = (1 << zoom) - 1  # 2^zoom - 1
    min_x = max(0, min(min_x, max_tile))
    max_x = max(0, min(max_x, max_tile))
    min_y = max(0, min(min_y, max_tile))
    max_y = max(0, min(max_y, max_tile))
    
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            yield TileCoordinate(z=zoom, x=x, y=y)


def lon_to_tile_x(lon: float, zoom: int) -> int:
    """Convert longitude to tile X coordinate."""
    return int((lon + 180.0) / 360.0 * (1 << zoom))


def lat_to_tile_y(lat: float, zoom: int) -> int:
    """Convert latitude to tile Y coordinate."""
    import math
    lat_rad = math.radians(lat)
    n = 1 << zoom
    return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
```

---

## Part 2: Add Safety Limits in API

**File:** `cli/src/webmap_archiver/api.py`

Find where `expand_coverage_async` is called in `_build_archive()` and add safety checks:

### Step 2.1: Find the Coverage Expansion Block

Look for code like:
```python
if report.total_missing > 0:
    result = await expand_coverage_async(...)
```

### Step 2.2: Add Safety Limits

Update that block to:

```python
# Coverage expansion with safety limits
MAX_EXPANSION_TILES = 500  # Don't fetch more than this

if expand_coverage and report.total_missing > 0:
    print(f"    Zoom range: z{report.min_zoom}-z{report.max_zoom}", flush=True)
    print(f"    Missing tiles: {report.total_missing}", flush=True)
    
    if report.total_missing > 10000:
        # Something is wrong with the calculation
        print(f"    [Warning] Unreasonable tile count ({report.total_missing}), skipping expansion", flush=True)
        print(f"    [Warning] This usually indicates the bounds or zoom range is too large", flush=True)
    else:
        result = await expand_coverage_async(
            url_template=url_pattern,
            source_name=source_name,
            captured_tiles=tiles,
            bounds=bounds,
            expand_zoom=expand_zoom,
            rate_limit=10,
            max_tiles=MAX_EXPANSION_TILES,  # Pass safety limit
            progress_callback=None,
        )
        
        if result.fetched_count > 0:
            # Add fetched tiles to the source
            tiles.extend(result.tiles)
            print(f"    Added {result.fetched_count} tiles to '{source_name}'", flush=True)
```

---

## Part 3: Update Function Calls

Make sure all calls to `analyze_coverage()` pass the `expand_zoom` parameter:

```python
# Before:
report = analyze_coverage(tiles, bounds, source_name)

# After:
report = analyze_coverage(tiles, bounds, source_name, expand_zoom=expand_zoom)
```

---

## Testing

After making these changes:

1. **Deploy to Modal:**
   ```bash
   modal deploy cli/src/webmap_archiver/modal_app.py
   ```

2. **Test with extension** - capture parkingregulations.nyc

3. **Expected logs:**
   ```
   Processing source 'maptiler' (23 tiles)
       Zoom range: z12-z14
       Target zoom range: z12-z15
       Missing tiles: 45
       Fetching 45 tiles...
       Fetched 42 tiles, 3 failed
       Added 42 tiles to 'maptiler'
   ```

4. **Verify archive** - should complete in <30 seconds, not timeout

---

## Summary of Changes

| File | Change |
|------|--------|
| `tiles/fetcher.py` | Fix `analyze_coverage()` to use captured zoom range only |
| `tiles/fetcher.py` | Fix `expand_coverage_async()` to respect zoom bounds and add `max_tiles` limit |
| `tiles/fetcher.py` | Add/verify `tiles_in_bounds()` helper function |
| `api.py` | Add safety check for unreasonable tile counts (>10000) |
| `api.py` | Pass `max_tiles` parameter to expansion function |

The key insight: **only expand within the captured zoom range + 1 level**, never calculate tiles for all possible zoom levels.