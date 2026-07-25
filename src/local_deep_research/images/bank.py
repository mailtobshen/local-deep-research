"""In-memory aggregator for extracted images during one research run."""
from __future__ import annotations

from typing import Dict, Iterable, List

from loguru import logger

from .extractor import ExtractedImage


class ImageBank:
    """Holds extracted images for a single research, keyed by URL.

    Lifetime: created in the post-processing step of run_research_process;
    not persisted (persistence is the DB Image table, written by ImageStore).
    """

    def __init__(self) -> None:
        self._by_url: Dict[str, ExtractedImage] = {}

    def add(self, images: List[ExtractedImage]) -> None:
        added = 0
        skipped = 0
        for img in images:
            if img.url not in self._by_url:
                self._by_url[img.url] = img
                added += 1
            else:
                skipped += 1
        if images:
            logger.debug(
                f"[IMG-TRACE] bank.add +{added} dedup_skipped={skipped}"
            )

    def candidates_with_alt(self) -> List[ExtractedImage]:
        return [i for i in self._by_url.values() if i.alt]

    def candidates_without_alt(self, limit: int = 20) -> List[ExtractedImage]:
        no_alt = [i for i in self._by_url.values() if not i.alt]
        return no_alt[:limit]

    def set_alt(self, url: str, alt: str) -> None:
        img = self._by_url.get(url)
        if img is not None:
            self._by_url[url] = ExtractedImage(
                url=img.url,
                alt=alt,
                source_url=img.source_url,
                source_title=img.source_title,
                width=img.width,
                height=img.height,
            )

    def all_urls(self) -> List[str]:
        return list(self._by_url.keys())

    def subset(self, urls: Iterable[str]) -> "ImageBank":
        selected = ImageBank()
        selected.add(
            [self._by_url[url] for url in urls if url in self._by_url]
        )
        return selected
