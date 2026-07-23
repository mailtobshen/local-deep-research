"""Serialize ExtractedImage lists to/from JSON for storage in html_content."""
from __future__ import annotations

import json
from typing import List, Optional

from loguru import logger

from .extractor import ExtractedImage

_FIELDS = ("url", "alt", "source_url", "source_title", "width", "height")


def dumps_images(images: List[ExtractedImage]) -> str:
    try:
        return json.dumps(
            [
                {
                    "url": i.url,
                    "alt": i.alt,
                    "source_url": i.source_url,
                    "source_title": i.source_title,
                    "width": i.width,
                    "height": i.height,
                }
                for i in images
            ]
        )
    except Exception:
        logger.debug("dumps_images failed")
        return "[]"


def loads_images(raw: Optional[str]) -> List[ExtractedImage]:
    """Deserialize; tolerant of None, empty, legacy HTML, or malformed JSON."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[ExtractedImage] = []
    for entry in data:
        if not isinstance(entry, dict) or "url" not in entry:
            continue
        out.append(
            ExtractedImage(
                url=entry.get("url"),
                alt=entry.get("alt", ""),
                source_url=entry.get("source_url", ""),
                source_title=entry.get("source_title", ""),
                width=entry.get("width"),
                height=entry.get("height"),
            )
        )
    return out
