"""Tile detection, coverage calculation, PMTiles building, and coverage expansion."""

from .detector import TileDetector
from .coverage import CoverageCalculator, GeoBounds, TileCoord
from .pmtiles import PMTilesBuilder, PMTilesMetadata
from .layer_inspector import discover_layers_from_tiles, get_primary_layer_name

# Optional: fetcher requires aiohttp
try:
    from .fetcher import (
        TileFetcher, CoverageAnalyzer, TileMath,
        expand_coverage, analyze_coverage, ExpansionResult, CoverageReport
    )
    FETCHER_AVAILABLE = True
except ImportError:
    FETCHER_AVAILABLE = False

__all__ = [
    'TileDetector', 
    'CoverageCalculator', 
    'GeoBounds',
    'TileCoord',
    'PMTilesBuilder', 
    'PMTilesMetadata',
    'discover_layers_from_tiles',
    'get_primary_layer_name',
    'FETCHER_AVAILABLE',
]

if FETCHER_AVAILABLE:
    __all__.extend([
        'TileFetcher',
        'CoverageAnalyzer', 
        'TileMath',
        'expand_coverage',
        'analyze_coverage',
        'ExpansionResult',
        'CoverageReport',
    ])
