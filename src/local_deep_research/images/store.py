"""Download chosen images to a local mirror, record in DB, rewrite markdown URLs."""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)]+)\)")


class ImageStore:
    def __init__(
        self, research_id: str, db_session, base_dir: Path = Path("/data/images")
    ) -> None:
        # Original id is stored in the DB (FK -> research_history.id, a UUID).
        self.research_id = research_id
        # Sanitized id is used ONLY for the filesystem path + served route,
        # so a malicious/odd id can never escape base_dir.
        self._safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", research_id)
        self.db_session = db_session
        self.base_dir = Path(base_dir)

    def _download(self, url: str) -> Optional[Tuple[bytes, str]]:
        from ..security.safe_requests import safe_get

        resp = safe_get(url, timeout=30, allow_private_ips=False)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        return resp.content, ctype

    @staticmethod
    def _ext_for(content_type: str) -> str:
        mapping = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        return mapping.get(content_type, ".bin")

    def persist(self, urls: List[str]) -> Dict[str, str]:
        url_to_route: Dict[str, str] = {}
        for url in urls:
            try:
                result = self._download(url)
                if result is None:
                    continue
                data, ctype = result
                digest = hashlib.sha1(data).hexdigest()
                ext = self._ext_for(ctype)
                rel = f"{self._safe_id}/{digest}{ext}"
                local_path = self.base_dir / self._safe_id / f"{digest}{ext}"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)
                route = f"/images/{rel}"
                self._record(url, str(local_path), route, digest)
                url_to_route[url] = route
            except Exception:
                logger.debug("Image persist failed for %s", url, exc_info=True)
        return url_to_route

    def _record(self, url, local_path, route, digest) -> None:
        try:
            from ..database.models import Image

            self.db_session.add(
                Image(
                    research_id=self.research_id,
                    original_url=url,
                    local_path=local_path,
                    local_route=route,
                    alt=None,
                    source_url=None,
                    source_title=None,
                    content_hash=digest,
                )
            )
            self.db_session.commit()
        except Exception:
            logger.debug("Image DB record failed for %s", url, exc_info=True)
            self.db_session.rollback()

    def rewrite_markdown(self, markdown: str, url_to_route: Dict[str, str]) -> str:
        def repl(m: re.Match) -> str:
            alt, url = m.group(1), m.group(2)
            route = url_to_route.get(url)
            return f"![{alt}]({route})" if route else m.group(0)

        return _IMG_RE.sub(repl, markdown)
