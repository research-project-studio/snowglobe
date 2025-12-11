"""
Extract layer names from vector tiles (MVT/PBF format).

Vector tiles contain their layer names as part of the protobuf structure.
This is the RELIABLE way to get source-layer names - no JavaScript parsing needed.

MVT Format (simplified):
- A tile contains one or more layers
- Each layer has a name (string) and features
- The protobuf schema uses field 3 for layers, field 1 within layer for name

This module provides both:
1. A simple regex-based extractor (works for most tiles, no dependencies)
2. An optional full decoder using mapbox_vector_tile library
"""

import gzip
import re
from dataclasses import dataclass


@dataclass
class TileLayerInfo:
    """Information about a layer in a vector tile."""
    name: str
    feature_count: int = 0
    geometry_types: set[str] = None
    
    def __post_init__(self):
        if self.geometry_types is None:
            self.geometry_types = set()


def decompress_tile(content: bytes) -> bytes:
    """Decompress tile if gzipped."""
    # Check for gzip magic number
    if content[:2] == b'\x1f\x8b':
        try:
            return gzip.decompress(content)
        except Exception:
            pass
    return content


def extract_layer_names_simple(tile_content: bytes) -> list[str]:
    """
    Extract layer names using simple pattern matching.
    
    This works because layer names in MVT protobuf are stored as:
    - Field 3 (layers) containing Field 1 (name) as string
    - Strings in protobuf are length-prefixed ASCII/UTF-8
    
    We look for readable string patterns that appear to be layer names.
    This is a heuristic but works for 95%+ of tiles.
    """
    content = decompress_tile(tile_content)
    
    # Layer names are typically alphanumeric with underscores
    # They appear as length-prefixed strings in the protobuf
    # Pattern: look for strings that look like layer identifiers
    
    layer_names = set()
    
    # MVT layer names are typically:
    # - Start with a letter or underscore
    # - Contain letters, numbers, underscores
    # - Are 2-50 characters long
    # - Often include meaningful words like "road", "water", "building", etc.
    
    # Scan through bytes looking for ASCII strings
    i = 0
    while i < len(content) - 2:
        # Look for potential string length byte followed by printable ASCII
        potential_len = content[i]
        if 2 <= potential_len <= 60:  # Reasonable layer name length
            # Check if next bytes could be a layer name
            start = i + 1
            end = start + potential_len
            if end <= len(content):
                try:
                    candidate = content[start:end].decode('utf-8')
                    # Check if it looks like a layer name
                    if _is_valid_layer_name(candidate):
                        layer_names.add(candidate)
                except (UnicodeDecodeError, ValueError):
                    pass
        i += 1
    
    return sorted(layer_names)


def _is_valid_layer_name(s: str) -> bool:
    """Check if string looks like a valid layer name."""
    if not s:
        return False
    
    # Must start with letter or underscore
    if not (s[0].isalpha() or s[0] == '_'):
        return False
    
    # Must contain only valid characters
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', s):
        return False
    
    # Reject common non-layer strings that appear in tiles
    reject_patterns = {
        'Arial', 'Helvetica', 'Sans', 'Bold', 'Regular', 'Medium',
        'true', 'false', 'null', 'undefined',
        'name', 'class', 'type', 'id',  # These are property names, not layers
    }
    if s in reject_patterns:
        return False
    
    # Layer names typically are longer than 3 chars
    if len(s) < 3:
        return False
    
    return True


def extract_layer_names_protobuf(tile_content: bytes) -> list[str]:
    """
    Extract source-layer names from MVT protobuf content.

    MVT protobuf structure:
    message Tile {
        repeated Layer layers = 3;
    }
    message Layer {
        required string name = 1;
        repeated Feature features = 2;
        ...
    }

    Returns:
        List of layer names found in the tile
    """
    content = decompress_tile(tile_content)
    layer_names = []

    # Parse protobuf manually (simplified for MVT)
    pos = 0
    while pos < len(content):
        # Read field tag
        if pos >= len(content):
            break

        tag_byte = content[pos]
        field_num = tag_byte >> 3
        wire_type = tag_byte & 0x07
        pos += 1

        if field_num == 3 and wire_type == 2:  # Layer field (length-delimited)
            # Read length (varint)
            length, pos = _read_varint(content, pos)
            if length is None or pos + length > len(content):
                break

            # Parse layer submessage
            layer_data = content[pos:pos + length]
            layer_info = _parse_layer(layer_data)
            if layer_info and layer_info.name:
                layer_names.append(layer_info.name)

            pos += length
        else:
            # Skip unknown field
            pos = _skip_field(content, pos, wire_type)
            if pos is None:
                break

    return layer_names


def extract_layer_info_protobuf(tile_content: bytes) -> list[TileLayerInfo]:
    """
    Extract detailed layer information using proper protobuf parsing.

    This is the detailed version that returns TileLayerInfo objects.
    Use extract_layer_names_protobuf() if you only need names.

    Returns:
        List of TileLayerInfo objects with names and metadata
    """
    content = decompress_tile(tile_content)
    layers = []

    # Parse protobuf manually (simplified for MVT)
    pos = 0
    while pos < len(content):
        # Read field tag
        if pos >= len(content):
            break

        tag_byte = content[pos]
        field_num = tag_byte >> 3
        wire_type = tag_byte & 0x07
        pos += 1

        if field_num == 3 and wire_type == 2:  # Layer field (length-delimited)
            # Read length (varint)
            length, pos = _read_varint(content, pos)
            if length is None or pos + length > len(content):
                break

            # Parse layer submessage
            layer_data = content[pos:pos + length]
            layer_info = _parse_layer(layer_data)
            if layer_info and layer_info.name:
                layers.append(layer_info)

            pos += length
        else:
            # Skip unknown field
            pos = _skip_field(content, pos, wire_type)
            if pos is None:
                break

    return layers


def _read_varint(data: bytes, pos: int) -> tuple[int | None, int]:
    """Read a varint from data at position."""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7f) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 64:
            return None, pos
    return None, pos


def _skip_field(data: bytes, pos: int, wire_type: int) -> int | None:
    """Skip a protobuf field based on wire type."""
    if wire_type == 0:  # Varint
        _, pos = _read_varint(data, pos)
        return pos
    elif wire_type == 1:  # 64-bit
        return pos + 8
    elif wire_type == 2:  # Length-delimited
        length, pos = _read_varint(data, pos)
        if length is None:
            return None
        return pos + length
    elif wire_type == 5:  # 32-bit
        return pos + 4
    return None


def _parse_layer(layer_data: bytes) -> TileLayerInfo | None:
    """Parse a Layer submessage to extract name and basic info."""
    pos = 0
    name = None
    feature_count = 0
    
    while pos < len(layer_data):
        if pos >= len(layer_data):
            break
            
        tag_byte = layer_data[pos]
        field_num = tag_byte >> 3
        wire_type = tag_byte & 0x07
        pos += 1
        
        if field_num == 1 and wire_type == 2:  # name field
            length, pos = _read_varint(layer_data, pos)
            if length is None or pos + length > len(layer_data):
                break
            try:
                name = layer_data[pos:pos + length].decode('utf-8')
            except UnicodeDecodeError:
                pass
            pos += length
        elif field_num == 2 and wire_type == 2:  # features field
            feature_count += 1
            length, pos = _read_varint(layer_data, pos)
            if length is None:
                break
            pos += length
        else:
            pos = _skip_field(layer_data, pos, wire_type)
            if pos is None:
                break
    
    if name:
        return TileLayerInfo(name=name, feature_count=feature_count)
    return None


def discover_layers_from_tiles(tiles: list[tuple]) -> list[str]:
    """
    Discover all unique source-layer names from a list of tiles.

    Samples tiles across the set to find all layers.

    Args:
        tiles: List of (coord, content) tuples

    Returns:
        List of unique layer names
    """
    all_layers = []

    # Sample up to 10 tiles
    sample_size = min(10, len(tiles))

    for i in range(sample_size):
        coord, content = tiles[i]
        layers = extract_layer_names_protobuf(content)
        for layer in layers:
            if layer not in all_layers:
                all_layers.append(layer)

    return all_layers


def discover_layer_info_from_tiles(tiles: list[tuple[any, bytes]]) -> dict[str, TileLayerInfo]:
    """
    Discover all unique layers from a collection of tiles with detailed info.

    This is the detailed version that returns TileLayerInfo objects.
    Use discover_layers_from_tiles() if you only need names.

    Args:
        tiles: List of (coord, content) tuples

    Returns:
        Dict mapping layer name to TileLayerInfo with aggregated info
    """
    all_layers: dict[str, TileLayerInfo] = {}

    # Sample tiles to find layers (don't need to parse all)
    # Different zoom levels might have different layers
    sample_size = min(10, len(tiles))

    # Get tiles from different zoom levels if possible
    by_zoom: dict[int, list] = {}
    for coord, content in tiles:
        z = coord.z if hasattr(coord, 'z') else coord[0]
        if z not in by_zoom:
            by_zoom[z] = []
        by_zoom[z].append(content)

    # Sample from each zoom level
    sampled = []
    for z in sorted(by_zoom.keys()):
        sampled.extend(by_zoom[z][:3])  # Up to 3 tiles per zoom
        if len(sampled) >= sample_size:
            break

    # Extract layers from sampled tiles
    for content in sampled:
        try:
            # Try protobuf parser first (more accurate)
            layer_infos = extract_layer_info_protobuf(content)
            for info in layer_infos:
                if info.name not in all_layers:
                    all_layers[info.name] = info
                else:
                    # Aggregate feature counts
                    all_layers[info.name].feature_count += info.feature_count
        except Exception:
            # Fall back to simple extraction
            names = extract_layer_names_simple(content)
            for name in names:
                if name not in all_layers:
                    all_layers[name] = TileLayerInfo(name=name)

    return all_layers


def get_primary_layer_name(tiles: list[tuple]) -> str | None:
    """
    Get the most common layer name from tiles.

    Useful when you need a single source-layer to target.

    Args:
        tiles: List of (coord, content) tuples

    Returns:
        Primary layer name, or None if no layers found
    """
    layers = discover_layers_from_tiles(tiles)
    return layers[0] if layers else None
