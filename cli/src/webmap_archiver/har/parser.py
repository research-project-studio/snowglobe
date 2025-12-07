"""
HAR file parsing with content extraction.

Key requirements:
- Handle both plain text and base64-encoded content
- Extract response body as bytes
- Parse timestamps
- Filter to successful responses (2xx status)
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import base64


@dataclass
class HAREntry:
    """A single request/response from a HAR file."""
    url: str
    method: str
    status: int
    mime_type: str
    content: bytes | None
    timestamp: datetime

    @property
    def is_successful(self) -> bool:
        return 200 <= self.status < 300

    @property
    def has_content(self) -> bool:
        return self.content is not None and len(self.content) > 0


class HARParser:
    """Parse HAR files and extract entries with content."""

    def __init__(self, har_path: Path | None):
        self.har_path = Path(har_path) if har_path else None

    def parse(self) -> list[HAREntry]:
        """Parse HAR file and return all entries."""
        if not self.har_path:
            raise ValueError("No HAR path provided")

        with open(self.har_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return self.parse_har_data(data)

    def parse_har_data(self, data: dict) -> list[HAREntry]:
        """Parse HAR data from a dictionary."""
        entries = []
        for entry in data['log']['entries']:
            parsed = self._parse_entry(entry)
            if parsed:
                entries.append(parsed)

        return entries

    def _parse_entry(self, entry: dict) -> HAREntry | None:
        """Parse a single HAR entry."""
        request = entry.get('request', {})
        response = entry.get('response', {})
        content_info = response.get('content', {})

        # Extract and decode content
        content = self._decode_content(content_info)

        return HAREntry(
            url=request.get('url', ''),
            method=request.get('method', 'GET'),
            status=response.get('status', 0),
            mime_type=content_info.get('mimeType', ''),
            content=content,
            timestamp=self._parse_timestamp(entry.get('startedDateTime'))
        )

    def _decode_content(self, content_info: dict) -> bytes | None:
        """Decode content from HAR format to bytes."""
        text = content_info.get('text')
        if text is None:
            return None

        encoding = content_info.get('encoding', '')

        if encoding == 'base64':
            try:
                return base64.b64decode(text)
            except Exception:
                return None
        else:
            # Plain text - encode to bytes
            return text.encode('utf-8')

    def _parse_timestamp(self, ts: str | None) -> datetime:
        """Parse ISO 8601 timestamp from HAR."""
        if not ts:
            return datetime.now()
        # Handle 'Z' suffix and various formats
        ts = ts.replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return datetime.now()
