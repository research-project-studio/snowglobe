"""
Fetch external GeoJSON sources.
"""

import aiohttp
import asyncio
from dataclasses import dataclass
import json


@dataclass
class FetchedGeoJSON:
    source_name: str
    data: dict
    size_bytes: int
    original_url: str


class GeoJSONFetcher:
    """Fetch external GeoJSON files."""

    def __init__(
        self,
        timeout: float = 30.0,
        max_size_mb: float = 50.0
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)

    async def fetch(self, url: str, source_name: str) -> FetchedGeoJSON | None:
        """Fetch a single GeoJSON file."""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        print(f"[GeoJSON] Failed to fetch {url}: {response.status}", flush=True)
                        return None

                    # Check content length
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > self.max_size_bytes:
                        print(f"[GeoJSON] {url} exceeds max size ({content_length} bytes)", flush=True)
                        return None

                    content = await response.read()

                    # Parse JSON
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError as e:
                        print(f"[GeoJSON] Invalid JSON from {url}: {e}", flush=True)
                        return None

                    return FetchedGeoJSON(
                        source_name=source_name,
                        data=data,
                        size_bytes=len(content),
                        original_url=url
                    )
        except asyncio.TimeoutError:
            print(f"[GeoJSON] Timeout fetching {url}", flush=True)
            return None
        except Exception as e:
            print(f"[GeoJSON] Error fetching {url}: {e}", flush=True)
            return None

    async def fetch_all(
        self,
        sources: list[tuple[str, str]]  # (source_name, url)
    ) -> list[FetchedGeoJSON]:
        """Fetch multiple GeoJSON files concurrently."""
        tasks = [self.fetch(url, name) for name, url in sources]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]
