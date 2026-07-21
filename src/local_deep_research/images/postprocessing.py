"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .bank import ImageBank
from .enhancer import ImageEnhancer
from .serialize import loads_images
from .store import ImageStore, _IMG_RE
from .vision import VisionDescriber
from ..config.llm_config import get_llm

logger = logging.getLogger(__name__)


def enhance_report_with_images(
    *,
    research_id: str,
    clean_markdown: str,
    results: Dict[str, Any],
    db_session,
    enable_images: bool,
    vision_model: str,
    vision_url: Optional[str] = None,
    vision_api_key: Optional[str] = None,
) -> str:
    """Return markdown with real images inserted + mirrored locally.

    When enable_images is False, returns clean_markdown unchanged.
    """
    if not enable_images:
        return clean_markdown
    try:
        bank = ImageBank()
        for finding in results.get("findings", []):
            for sr in finding.get("search_results", []) or []:
                raw = sr.get("html_content")
                if raw:
                    bank.add(loads_images(raw))
        if not bank.all_urls():
            return clean_markdown

        llm = get_llm()
        vision = VisionDescriber(
            model_name=vision_model,
            base_url=vision_url,
            api_key=vision_api_key,
        )
        enhanced = ImageEnhancer(llm, vision).enhance(clean_markdown, bank)

        # Persist the real URLs that survived into the enhanced markdown.
        chosen = [m.group(2) for m in _IMG_RE.finditer(enhanced)]
        store = ImageStore(research_id, db_session)
        url_to_route = store.persist(chosen)
        if url_to_route:
            enhanced = store.rewrite_markdown(enhanced, url_to_route)
        return enhanced
    except Exception:
        logger.exception(
            "Image post-processing failed; returning clean markdown"
        )
        return clean_markdown
