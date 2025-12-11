"""
WebMap Archiver - Archive web maps as self-contained packages.
"""

__version__ = "0.2.0"

# Public API
from .api import (
    create_archive_from_bundle,
    create_archive_from_har,
    inspect_bundle,
    normalize_bundle,
    ArchiveResult,
    TileSourceResult,
    InspectionResult,
)

# Exceptions
from .capture.parser import CaptureValidationError

__all__ = [
    # Functions
    "create_archive_from_bundle",
    "create_archive_from_har",
    "inspect_bundle",
    "normalize_bundle",
    # Data classes
    "ArchiveResult",
    "TileSourceResult",
    "InspectionResult",
    # Exceptions
    "CaptureValidationError",
    # Metadata
    "__version__",
]
