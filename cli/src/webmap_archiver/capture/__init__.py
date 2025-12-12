"""
Capture module for WebMap Archiver.

Provides browser-based capture capabilities using Pyppeteer.
"""

# Bundle parsing (always available)
from .parser import (
    CaptureParser,
    CaptureBundle,
    CaptureMetadata,
    CaptureViewport,
    CaptureTile,
    CaptureResource,
    CaptureValidationError,
    validate_capture_bundle,
)

# Style extractor (requires pyppeteer, gracefully degrades)
try:
    from .style_extractor import (
        extract_style_from_url,
        extract_style_with_retry,
        ExtractedStyle,
    )
    _style_extractor_available = True
except ImportError:
    _style_extractor_available = False

__all__ = [
    'CaptureParser',
    'CaptureBundle',
    'CaptureMetadata',
    'CaptureViewport',
    'CaptureTile',
    'CaptureResource',
    'CaptureValidationError',
    'validate_capture_bundle',
]

if _style_extractor_available:
    __all__.extend([
        'extract_style_from_url',
        'extract_style_with_retry',
        'ExtractedStyle',
    ])
