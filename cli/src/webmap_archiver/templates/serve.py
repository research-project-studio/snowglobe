#!/usr/bin/env python3
"""
Local server for archived web maps.

Serves the original site files while intercepting tile requests
and serving them from local PMTiles archives.

Usage:
    python serve.py [--port 8080] [--no-open]

Requirements:
    Python 3.10+ (no additional packages needed)
"""

import argparse
import gzip
import json
import os
import re
import socketserver
import struct
import webbrowser
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from functools import lru_cache


# =============================================================================
# PMTiles Reader (minimal implementation, no dependencies)
# =============================================================================

class PMTilesReader:
    """Minimal PMTiles v3 reader for serving tiles."""
    
    def __init__(self, path: Path):
        self.path = path
        self.file = open(path, 'rb')
        self._read_header()
        self._directory_cache = {}
    
    def _read_header(self):
        """Read PMTiles v3 header."""
        self.file.seek(0)
        magic = self.file.read(7)
        if magic != b'PMTiles':
            raise ValueError(f"Not a PMTiles file: {self.path}")
        
        version = struct.unpack('B', self.file.read(1))[0]
        if version != 3:
            raise ValueError(f"Unsupported PMTiles version: {version}")
        
        # Read header fields (120 bytes after magic + version)
        header_data = self.file.read(119)
        
        (
            self.root_dir_offset,
            self.root_dir_length,
            self.json_metadata_offset,
            self.json_metadata_length,
            self.leaf_dirs_offset,
            self.leaf_dirs_length,
            self.tile_data_offset,
            self.tile_data_length,
            self.num_addressed_tiles,
            self.num_tile_entries,
            self.num_tile_contents,
            self.clustered,
            self.internal_compression,
            self.tile_compression,
            self.tile_type,
            self.min_zoom,
            self.max_zoom,
            self.min_lon_e7,
            self.min_lat_e7,
            self.max_lon_e7,
            self.max_lat_e7,
            self.center_zoom,
            self.center_lon_e7,
            self.center_lat_e7,
        ) = struct.unpack('<QQQQQQQQQQQBBBBBB3xiiiiiBii', header_data)
    
    def _decompress(self, data: bytes, compression: int) -> bytes:
        """Decompress data based on compression type."""
        if compression == 0 or compression == 0:  # None or Unknown
            return data
        elif compression == 1:  # Gzip
            try:
                return gzip.decompress(data)
            except:
                return data
        elif compression == 2:  # Brotli
            try:
                import brotli
                return brotli.decompress(data)
            except ImportError:
                raise ValueError("Brotli compression requires 'brotli' package")
        elif compression == 3:  # Zstd
            try:
                import zstandard
                return zstandard.decompress(data)
            except ImportError:
                raise ValueError("Zstd compression requires 'zstandard' package")
        else:
            return data
    
    def _read_varint(self, data: bytes, pos: int) -> tuple:
        """Read a varint from data at position."""
        result = 0
        shift = 0
        while pos < len(data):
            byte = data[pos]
            pos += 1
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result, pos
    
    def _read_directory(self, offset: int, length: int) -> list:
        """Read and parse a directory."""
        cache_key = (offset, length)
        if cache_key in self._directory_cache:
            return self._directory_cache[cache_key]
        
        self.file.seek(offset)
        raw_data = self.file.read(length)
        data = self._decompress(raw_data, self.internal_compression)
        
        entries = []
        num_entries, pos = self._read_varint(data, 0)
        
        # Read tile IDs
        tile_id = 0
        tile_ids = []
        for _ in range(num_entries):
            delta, pos = self._read_varint(data, pos)
            tile_id += delta
            tile_ids.append(tile_id)
        
        # Read run lengths
        run_lengths = []
        for _ in range(num_entries):
            run_length, pos = self._read_varint(data, pos)
            run_lengths.append(run_length)
        
        # Read lengths
        lengths = []
        for _ in range(num_entries):
            length, pos = self._read_varint(data, pos)
            lengths.append(length)
        
        # Read offsets (delta encoded)
        offsets = []
        offset_acc = 0
        for i in range(num_entries):
            if lengths[i] == 0:
                # Run-length encoded - same offset as previous
                offsets.append(offset_acc)
            else:
                delta, pos = self._read_varint(data, pos)
                offset_acc += delta
                offsets.append(offset_acc)
        
        # Build entry list
        for i in range(num_entries):
            if run_lengths[i] == 0:
                # Leaf directory reference
                entries.append({
                    'type': 'leaf',
                    'tile_id': tile_ids[i],
                    'offset': offsets[i],
                    'length': lengths[i]
                })
            else:
                # Tile entry
                entries.append({
                    'type': 'tile',
                    'tile_id': tile_ids[i],
                    'run_length': run_lengths[i],
                    'offset': offsets[i],
                    'length': lengths[i]
                })
        
        self._directory_cache[cache_key] = entries
        return entries
    
    def _zxy_to_tile_id(self, z: int, x: int, y: int) -> int:
        """Convert z/x/y to Hilbert tile ID."""
        if z == 0:
            return 0
        
        # Calculate base offset for zoom level
        acc = 0
        for i in range(z):
            acc += (1 << i) * (1 << i)
        
        # Hilbert curve position within zoom level
        n = 1 << z
        rx = ry = s = 0
        d = 0
        s = n // 2
        while s > 0:
            rx = 1 if (x & s) > 0 else 0
            ry = 1 if (y & s) > 0 else 0
            d += s * s * ((3 * rx) ^ ry)
            
            # Rotate
            if ry == 0:
                if rx == 1:
                    x = s - 1 - x
                    y = s - 1 - y
                x, y = y, x
            s //= 2
        
        return acc + d
    
    def _find_tile(self, tile_id: int, entries: list, depth: int = 0) -> tuple:
        """Find tile in directory entries, following leaf directories if needed."""
        if depth > 10:  # Prevent infinite recursion
            return None, None
        
        for entry in entries:
            if entry['type'] == 'tile':
                if entry['tile_id'] <= tile_id < entry['tile_id'] + entry['run_length']:
                    # Found it - calculate actual offset for run-length entries
                    idx_in_run = tile_id - entry['tile_id']
                    return entry['offset'], entry['length']
            elif entry['type'] == 'leaf':
                if entry['tile_id'] <= tile_id:
                    # This leaf directory might contain our tile
                    leaf_entries = self._read_directory(
                        self.leaf_dirs_offset + entry['offset'],
                        entry['length']
                    )
                    result = self._find_tile(tile_id, leaf_entries, depth + 1)
                    if result[0] is not None:
                        return result
        
        return None, None
    
    def get_tile(self, z: int, x: int, y: int) -> bytes | None:
        """Get tile data for z/x/y coordinates."""
        tile_id = self._zxy_to_tile_id(z, x, y)
        
        # Search in root directory
        entries = self._read_directory(self.root_dir_offset, self.root_dir_length)
        offset, length = self._find_tile(tile_id, entries)
        
        if offset is None:
            return None
        
        # Read tile data
        self.file.seek(self.tile_data_offset + offset)
        tile_data = self.file.read(length)
        
        return tile_data
    
    def get_tile_type(self) -> str:
        """Get the tile type as string."""
        types = {0: 'unknown', 1: 'mvt', 2: 'png', 3: 'jpeg', 4: 'webp', 5: 'avif'}
        return types.get(self.tile_type, 'unknown')
    
    def get_compression(self) -> str:
        """Get the tile compression as string."""
        types = {0: 'unknown', 1: 'none', 2: 'gzip', 3: 'brotli', 4: 'zstd'}
        return types.get(self.tile_compression, 'unknown')
    
    def close(self):
        self.file.close()


# =============================================================================
# Tile URL Pattern Matching
# =============================================================================

class TilePattern:
    """Compiled pattern for matching tile URLs to PMTiles archives."""
    
    def __init__(self, original_url: str, pmtiles_name: str):
        self.pmtiles_name = pmtiles_name
        self.original_url = original_url
        
        # Build regex from URL template
        # Escape special chars, then replace placeholders
        pattern = re.escape(original_url)
        pattern = pattern.replace(r'\{z\}', r'(?P<z>\d+)')
        pattern = pattern.replace(r'\{x\}', r'(?P<x>\d+)')
        pattern = pattern.replace(r'\{y\}', r'(?P<y>\d+)')
        
        # Remove query string from pattern (we'll match path only)
        pattern = pattern.split(r'\?')[0]
        
        # Extract just the path portion for matching
        from urllib.parse import urlparse
        parsed = urlparse(original_url)
        path_pattern = re.escape(parsed.path)
        path_pattern = path_pattern.replace(r'\{z\}', r'(?P<z>\d+)')
        path_pattern = path_pattern.replace(r'\{x\}', r'(?P<x>\d+)')
        path_pattern = path_pattern.replace(r'\{y\}', r'(?P<y>\d+)')
        
        self.regex = re.compile(path_pattern)
        self.full_regex = re.compile(pattern)
    
    def match(self, path: str) -> dict | None:
        """Match URL path and return z/x/y if matched."""
        # Try path-only match first
        m = self.regex.search(path)
        if m:
            try:
                return {
                    'z': int(m.group('z')),
                    'x': int(m.group('x')),
                    'y': int(m.group('y'))
                }
            except (IndexError, ValueError):
                pass
        return None


# =============================================================================
# HTTP Request Handler
# =============================================================================

class ArchiveHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves original site with tile interception."""
    
    # Class-level configuration (set before server starts)
    archive_root: Path = Path('.')
    tile_patterns: list = []
    pmtiles_readers: dict = {}
    tile_content_types: dict = {}
    
    def __init__(self, *args, **kwargs):
        # Set directory to original/ subdirectory
        kwargs['directory'] = str(self.archive_root / 'original')
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests with tile interception."""
        # Check if this is a tile request
        for pattern in self.tile_patterns:
            coords = pattern.match(self.path)
            if coords:
                self.serve_tile(pattern.pmtiles_name, coords)
                return
        
        # Handle root path
        if self.path == '/' or self.path == '':
            self.path = '/index.html'
        
        # Serve static file from original/
        super().do_GET()
    
    def serve_tile(self, pmtiles_name: str, coords: dict):
        """Serve a tile from PMTiles archive."""
        try:
            z, x, y = coords['z'], coords['x'], coords['y']
            
            # Get or open PMTiles reader
            if pmtiles_name not in self.pmtiles_readers:
                pmtiles_path = self.archive_root / 'tiles' / f'{pmtiles_name}.pmtiles'
                if not pmtiles_path.exists():
                    self.send_error(404, f'PMTiles archive not found: {pmtiles_name}')
                    return
                reader = PMTilesReader(pmtiles_path)
                self.pmtiles_readers[pmtiles_name] = reader
                
                # Cache content type info
                tile_type = reader.get_tile_type()
                compression = reader.get_compression()
                self.tile_content_types[pmtiles_name] = (tile_type, compression)
            
            reader = self.pmtiles_readers[pmtiles_name]
            tile_data = reader.get_tile(z, x, y)
            
            if tile_data:
                self.send_response(200)
                
                # Set content type based on tile type
                tile_type, compression = self.tile_content_types.get(
                    pmtiles_name, ('mvt', 'gzip')
                )
                
                content_types = {
                    'mvt': 'application/x-protobuf',
                    'png': 'image/png',
                    'jpeg': 'image/jpeg',
                    'webp': 'image/webp',
                    'avif': 'image/avif',
                }
                self.send_header('Content-Type', content_types.get(tile_type, 'application/octet-stream'))
                
                # Set content encoding if compressed
                if compression == 'gzip':
                    self.send_header('Content-Encoding', 'gzip')
                
                self.send_header('Content-Length', len(tile_data))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.end_headers()
                self.wfile.write(tile_data)
            else:
                self.send_error(404, f'Tile not found: {z}/{x}/{y}')
                
        except Exception as e:
            self.send_error(500, str(e))
    
    def end_headers(self):
        """Add CORS headers to all responses."""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        super().end_headers()
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.end_headers()
    
    def log_message(self, format, *args):
        """Custom log format with tile indicator."""
        if args:
            request = args[0] if args else ''
            is_tile = any(p.match(request.split()[1]) for p in self.tile_patterns) if ' ' in request else False
            prefix = "🗺️ " if is_tile else "📄 "
            print(f"{prefix} {request}")


# =============================================================================
# Server Setup
# =============================================================================

def load_manifest(archive_root: Path) -> dict:
    """Load archive manifest."""
    manifest_path = archive_root / 'manifest.json'
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    
    with open(manifest_path) as f:
        return json.load(f)


def build_tile_patterns(manifest: dict) -> list:
    """Build tile URL patterns from manifest."""
    patterns = []
    
    for source in manifest.get('tile_sources', []):
        original_url = source.get('original_url', '')
        name = source.get('name', '')
        
        if original_url and name:
            try:
                patterns.append(TilePattern(original_url, name))
            except Exception as e:
                print(f"Warning: Could not create pattern for {name}: {e}")
    
    return patterns


def main():
    parser = argparse.ArgumentParser(
        description='Serve archived web map with tile interception',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python serve.py                    # Serve on port 8080, open browser
    python serve.py --port 3000        # Serve on port 3000
    python serve.py --no-open          # Don't open browser automatically
    python serve.py --archive ./mymap  # Serve from specific directory
        """
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=8080,
        help='Port to serve on (default: 8080)'
    )
    parser.add_argument(
        '--no-open',
        action='store_true',
        help="Don't open browser automatically"
    )
    parser.add_argument(
        '--archive',
        type=Path,
        default=None,
        help='Path to archive directory (default: directory containing this script)'
    )
    args = parser.parse_args()
    
    # Determine archive root
    if args.archive:
        archive_root = args.archive.resolve()
    else:
        # Default to directory containing this script
        archive_root = Path(__file__).parent.resolve()
    
    # Verify archive structure
    if not (archive_root / 'manifest.json').exists():
        print(f"❌ Error: No manifest.json found in {archive_root}")
        print("   Make sure you're running from an extracted archive directory,")
        print("   or use --archive to specify the archive path.")
        return 1
    
    if not (archive_root / 'original').exists():
        print(f"❌ Error: No original/ directory found in {archive_root}")
        print("   This archive may not include original site files.")
        print("   Use viewer.html for standalone viewing instead.")
        return 1
    
    # Load configuration
    try:
        manifest = load_manifest(archive_root)
        tile_patterns = build_tile_patterns(manifest)
    except Exception as e:
        print(f"❌ Error loading manifest: {e}")
        return 1
    
    # Configure handler
    ArchiveHandler.archive_root = archive_root
    ArchiveHandler.tile_patterns = tile_patterns
    
    # Get archive name
    archive_name = manifest.get('name', archive_root.name)
    
    # Start server
    try:
        with socketserver.TCPServer(("", args.port), ArchiveHandler) as httpd:
            url = f"http://localhost:{args.port}"
            
            print()
            print("🗺️  WebMap Archive Server")
            print(f"   Archive: {archive_name}")
            print(f"   URL: {url}")
            print(f"   Tile sources: {len(tile_patterns)}")
            for p in tile_patterns:
                print(f"      • {p.pmtiles_name}")
            print()
            print("   Press Ctrl+C to stop")
            print()
            
            if not args.no_open:
                webbrowser.open(url)
            
            httpd.serve_forever()
            
    except KeyboardInterrupt:
        print("\n\n   Shutting down...")
    except OSError as e:
        if e.errno == 98 or e.errno == 48:  # Address already in use
            print(f"❌ Error: Port {args.port} is already in use.")
            print(f"   Try a different port: python serve.py --port {args.port + 1}")
        else:
            raise
    
    return 0


if __name__ == '__main__':
    exit(main())
