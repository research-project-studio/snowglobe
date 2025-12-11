"""
WebMap Archiver - Archive web maps as self-contained packages.

Usage:
    from webmap_archiver import create_archive_from_bundle, ArchiveResult

    result = create_archive_from_bundle(bundle_dict, Path("output.zip"))
"""

__version__ = "0.2.0"

# Public API exports
from .api import (
    create_archive_from_bundle,
    create_archive_from_har,
    inspect_bundle,
    normalize_bundle,
    ArchiveResult,
    TileSourceResult,
    InspectionResult,
)

from .capture.parser import CaptureValidationError

__all__ = [
    # Version
    "__version__",
    # Main functions
    "create_archive_from_bundle",
    "create_archive_from_har",
    "inspect_bundle",
    "normalize_bundle",
    # Result types
    "ArchiveResult",
    "TileSourceResult",
    "InspectionResult",
    # Exceptions
    "CaptureValidationError",
]
