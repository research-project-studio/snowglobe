"""Capture bundle parsing, validation, and browser-based capture."""

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

# Browser capture is optional - only available if pyppeteer is installed
try:
    from .browser_capture import (
        capture_map_from_url,
        capture_result_to_bundle,
        CaptureResult,
        TileCapture,
        ResourceCapture,
    )
    _browser_capture_available = True
except ImportError:
    _browser_capture_available = False

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

if _browser_capture_available:
    __all__.extend([
        'capture_map_from_url',
        'capture_result_to_bundle',
        'CaptureResult',
        'TileCapture',
        'ResourceCapture',
    ])
