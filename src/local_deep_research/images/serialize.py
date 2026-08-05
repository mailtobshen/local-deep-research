"""Serialize ExtractedImage lists to/from JSON for storage in html_content."""
from __future__ import annotations

import json
from typing import List, Optional

from loguru import logger

from .extractor import ExtractedImage

_FIELDS = ("url", "alt", "source_url", "source_title", "width", "height")


def dumps_images(
    images: List[ExtractedImage], drop_empty_alt: bool = False
) -> str:
    payload = []
    skipped_empty = 0
    for i in images:
        if drop_empty_alt and not (i.alt and i.alt.strip()):
            skipped_empty += 1
            continue
        payload.append(
            {
                "url": i.url,
                "alt": i.alt,
                "source_url": i.source_url,
                "source_title": i.source_title,
                "width": i.width,
                "height": i.height,
            }
        )
    if skipped_empty:
        logger.debug(
            f"[IMG-TRACE] DUMPS_FILTER dropped_empty_alt={skipped_empty}"
        )
    try:
        return json.dumps(payload)
    except Exception as e:
        logger.warning(
            f"[IMG-TRACE] DUMPS_FAIL reason={type(e).__name__}: {e}"
        )
        return "[]"


def loads_images(raw: Optional[str]) -> List[ExtractedImage]:
    """Deserialize; tolerant of None, empty, legacy HTML, or malformed JSON."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.warning(
            f"[IMG-TRACE] LOADS_FAIL reason={type(e).__name__}: {e} "
            f"raw_first_100={raw[:100]!r}"
        )
        return []
    if not isinstance(data, list):
        logger.warning(
            f"[IMG-TRACE] LOADS_FAIL reason=not_list "
            f"raw_first_100={raw[:100]!r}"
        )
        return []
    out: List[ExtractedImage] = []
    for entry in data:
        if not isinstance(entry, dict) or "url" not in entry:
            logger.debug(
                f"[IMG-TRACE] LOADS_SKIP entry={entry!r}"
            )
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
