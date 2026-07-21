# Report Real-Image Extraction & Local Mirror — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let LDR embed real images (extracted from scraped source-page HTML) into research reports, mirror chosen images locally bound to the research, and serve them to the WebUI — replacing the current LLM-hallucinated image URLs.

**Architecture:** Post-processing enhancement. Firecrawl `scrape()` additionally returns HTML, persisted onto a new `SearchResult.html_content` column. After the report markdown is produced in `run_research_process`, a local `ImageBank` is rebuilt from `findings.search_results[].html_content`, an `ImageEnhancer` makes one LLM call to insert real image URLs into the report (with optional vision-LLM alt-text fallback), then an `ImageStore` downloads chosen images to `/data/images/<research_id>/`, rewrites markdown URLs to a local `/images/...` route, and records them in a new `research_images` table that cascades on research deletion.

**Tech Stack:** Python 3.14, Flask + SQLAlchemy (SQLCipher encrypted per-user DB), Alembic migrations, BeautifulSoup4 (`beautifulsoup4~=4.14`, already a dependency), LangChain `BaseChatModel` for LLM/vision calls.

## Global Constraints

- Feature is **off by default**: gated by new setting `report.enable_images` (default `false`). When off, behavior is byte-for-byte identical to today.
- Vision fallback gated by `report.image_vision_model` (default `""` = disabled).
- Every image URL reaching the final report must come from the real extracted `ImageBank` — the LLM is explicitly forbidden from inventing URLs (this is the root-cause fix for the original bug).
- All new image-network I/O (vision download, mirror download) must go through `safe_requests` (existing SSRF/proxy rules). Path-traversal protection on the serve route.
- Local image files live under `/data/images/<research_id>/<sha1>.<ext>`. Deletion of a research cascades DB rows (FK) AND removes the directory.
- `CSP img-src` already updated to `'self' data: https:` (committed earlier on this branch) — keep it; local route uses `'self'`.
- Follow existing code patterns: settings via `get_setting_from_snapshot`, encrypted DB via `db_manager`, routes via blueprints, models exported from `database/models/__init__.py`.

---

## File Structure

**New files:**
- `src/local_deep_research/images/__init__.py` — package marker, exports public classes.
- `src/local_deep_research/images/extractor.py` — `extract_images(html, source_url, source_title) -> List[ExtractedImage]` (pure).
- `src/local_deep_research/images/bank.py` — `ImageBank` in-memory aggregator.
- `src/local_deep_research/images/vision.py` — `VisionDescriber` (alt-text fallback).
- `src/local_deep_research/images/enhancer.py` — `ImageEnhancer` (orchestrates LLM insertion + vision).
- `src/local_deep_research/images/store.py` — `ImageStore` (download + DB + markdown rewrite).
- `src/local_deep_research/database/models/images.py` — `Image` ORM model.
- `src/local_deep_research/database/migrations/versions/0011_research_images.py` — migration.
- `tests/images/test_extractor.py`, `test_bank.py`, `test_vision.py`, `test_enhancer.py`, `test_store.py`, `test_cascade_delete.py`.

**Modified files:**
- `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py` — `scrape()` returns dict with markdown+html.
- `src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py` — adapt to dict, populate `item["html_content"]`.
- `src/local_deep_research/database/models/research.py` — `SearchResult.html_content` column.
- `src/local_deep_research/database/models/__init__.py` — export `Image`.
- `src/local_deep_research/defaults/default_settings.json` — 2 new settings.
- `src/local_deep_research/web/services/research_service.py` — post-processing block in `run_research_process`.
- `src/local_deep_research/web/routes/research_routes.py` — `delete_research()` file cleanup + new routes.

---

## Task 1: ImageExtractor (pure HTML → image list)

**Files:**
- Create: `src/local_deep_research/images/__init__.py`
- Create: `src/local_deep_research/images/extractor.py`
- Test: `tests/images/test_extractor.py`

**Interfaces:**
- Produces: `ExtractedImage` dataclass `{url, alt, source_url, source_title, width, height}` and `extract_images(html, source_url, source_title) -> List[ExtractedImage]`. Later tasks consume these.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_extractor.py
from local_deep_research.images.extractor import extract_images, ExtractedImage

def test_extracts_content_image_with_alt():
    html = '<html><body><img src="https://example.com/a/tower.jpg" alt="Canton Tower" width="800" height="600"></body></html>'
    imgs = extract_images(html, "https://example.com/a", "Example Page")
    assert len(imgs) == 1
    assert imgs[0].url == "https://example.com/a/tower.jpg"
    assert imgs[0].alt == "Canton Tower"
    assert imgs[0].source_url == "https://example.com/a"
    assert imgs[0].source_title == "Example Page"
    assert imgs[0].width == 800

def test_skips_data_uri():
    html = '<img src="data:image/png;base64,iVBORw0KGgo=" alt="x">'
    assert extract_images(html, "https://example.com", "t") == []

def test_skips_tiny_icon():
    html = '<img src="https://example.com/icon.png" width="16" height="16">'
    assert extract_images(html, "https://example.com", "t") == []

def test_skips_blacklisted_url_keywords():
    for kw in ["logo", "icon", "avatar", "sprite", "tracker", "blank.gif"]:
        html = f'<img src="https://example.com/{kw}.png" width="200" height="200">'
        assert extract_images(html, "https://example.com", "t") == [], kw

def test_resolves_relative_url():
    html = '<img src="/img/tower.jpg" alt="t" width="200">'
    imgs = extract_images(html, "https://example.com/page", "p")
    assert imgs[0].url == "https://example.com/img/tower.jpg"

def test_alt_empty_string_preserved():
    html = '<img src="https://example.com/x.jpg" width="200">'
    imgs = extract_images(html, "https://example.com", "t")
    assert len(imgs) == 1
    assert imgs[0].alt == ""

def test_missing_width_height_kept_when_url_ok():
    html = '<img src="https://example.com/big.jpg" alt="t">'
    imgs = extract_images(html, "https://example.com", "t")
    assert len(imgs) == 1
    assert imgs[0].width is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec ldr-local bash -c 'cd /install && .venv/bin/python -m pytest tests/images/test_extractor.py -v'` (if pytest present) OR `cd /home/administrator/local-deep-research && python -m pytest tests/images/test_extractor.py -v` if a local venv exists. Expected: FAIL — `ModuleNotFoundError: No module named 'local_deep_research.images'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/local_deep_research/images/__init__.py
from .extractor import ExtractedImage, extract_images

__all__ = ["ExtractedImage", "extract_images"]
```

```python
# src/local_deep_research/images/extractor.py
"""Extract real <img> from scraped HTML into a normalized list."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# URL substrings that almost always indicate non-content images.
_BLACKLIST_KEYWORDS = ("logo", "icon", "avatar", "sprite", "pixel", "tracker", "blank.gif")
_MIN_DIM = 50  # px; anything smaller is treated as an icon/pixel


@dataclass
class ExtractedImage:
    url: str
    alt: str
    source_url: str
    source_title: str
    width: Optional[int]
    height: Optional[int]


def _to_int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip().rstrip("px"))
    except (ValueError, TypeError):
        return None


def _is_blacklisted(url: str) -> bool:
    low = url.lower()
    return any(kw in low for kw in _BLACKLIST_KEYWORDS)


def extract_images(
    html: str, source_url: str, source_title: str
) -> List[ExtractedImage]:
    """Parse <img> tags from html, filter out non-content images, return normalized list."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: List[ExtractedImage] = []
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if not src:
            continue
        if src.startswith("data:"):
            continue
        absolute = urljoin(source_url, src)
        scheme = urlparse(absolute).scheme.lower()
        if scheme not in ("http", "https"):
            continue
        if _is_blacklisted(absolute):
            continue
        width = _to_int(img.get("width"))
        height = _to_int(img.get("height"))
        # If a concrete dimension is present and below threshold, skip.
        if width is not None and width < _MIN_DIM:
            continue
        if height is not None and height < _MIN_DIM:
            continue
        out.append(
            ExtractedImage(
                url=absolute,
                alt=(img.get("alt") or "").strip(),
                source_url=source_url,
                source_title=source_title,
                width=width,
                height=height,
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command. Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/__init__.py src/local_deep_research/images/extractor.py tests/images/test_extractor.py
git commit -m "feat(images): add ImageExtractor — pure HTML to image list"
```

---

## Task 2: ImageBank (in-memory aggregator)

**Files:**
- Create: `src/local_deep_research/images/bank.py`
- Modify: `src/local_deep_research/images/__init__.py`
- Test: `tests/images/test_bank.py`

**Interfaces:**
- Consumes: `ExtractedImage` from Task 1.
- Produces: `ImageBank` with `add(List[ExtractedImage])`, `candidates_with_alt() -> List[ExtractedImage]`, `candidates_without_alt(limit) -> List[ExtractedImage]`, `set_alt(url, alt)`, `all_urls() -> List[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_bank.py
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.bank import ImageBank

def _img(url, alt=""):
    return ExtractedImage(url=url, alt=alt, source_url="s", source_title="t", width=None, height=None)

def test_dedupes_by_url():
    b = ImageBank()
    b.add([_img("https://x/a.jpg", "A"), _img("https://x/a.jpg", "A")])
    assert len(b.all_urls()) == 1

def test_groups_by_alt_presence():
    b = ImageBank()
    b.add([_img("https://x/a.jpg", "A"), _img("https://x/b.jpg", "")])
    assert [i.url for i in b.candidates_with_alt()] == ["https://x/a.jpg"]
    assert [i.url for i in b.candidates_without_alt()] == ["https://x/b.jpg"]

def test_set_alt_moves_image_to_with_alt():
    b = ImageBank()
    b.add([_img("https://x/b.jpg", "")])
    b.set_alt("https://x/b.jpg", "tower")
    assert [i.url for i in b.candidates_with_alt()] == ["https://x/b.jpg"]
    assert b.candidates_without_alt() == []

def test_without_alt_respects_limit():
    b = ImageBank()
    b.add([_img(f"https://x/{i}.jpg", "") for i in range(50)])
    assert len(b.candidates_without_alt(limit=20)) == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_bank.py -v`. Expected: FAIL — `ImportError: cannot import name 'ImageBank'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/local_deep_research/images/bank.py
"""In-memory aggregator for extracted images during one research run."""
from __future__ import annotations

from typing import Dict, List

from .extractor import ExtractedImage


class ImageBank:
    """Holds extracted images for a single research, keyed by URL.

    Lifetime: created in the post-processing step of run_research_process;
    not persisted (persistence is the DB Image table, written by ImageStore).
    """

    def __init__(self) -> None:
        self._by_url: Dict[str, ExtractedImage] = {}

    def add(self, images: List[ExtractedImage]) -> None:
        for img in images:
            if img.url not in self._by_url:
                self._by_url[img.url] = img

    def candidates_with_alt(self) -> List[ExtractedImage]:
        return [i for i in self._by_url.values() if i.alt]

    def candidates_without_alt(self, limit: int = 20) -> List[ExtractedImage]:
        no_alt = [i for i in self._by_url.values() if not i.alt]
        return no_alt[:limit]

    def set_alt(self, url: str, alt: str) -> None:
        img = self._by_url.get(url)
        if img is not None:
            self._by_url[url] = ExtractedImage(
                url=img.url, alt=alt, source_url=img.source_url,
                source_title=img.source_title, width=img.width, height=img.height,
            )

    def all_urls(self) -> List[str]:
        return list(self._by_url.keys())
```

Update `src/local_deep_research/images/__init__.py` to also export `ImageBank`:

```python
from .extractor import ExtractedImage, extract_images
from .bank import ImageBank

__all__ = ["ExtractedImage", "extract_images", "ImageBank"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_bank.py -v`. Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/bank.py src/local_deep_research/images/__init__.py tests/images/test_bank.py
git commit -m "feat(images): add ImageBank in-memory aggregator"
```

---

## Task 3: VisionDescriber (alt-text fallback)

**Files:**
- Create: `src/local_deep_research/images/vision.py`
- Modify: `src/local_deep_research/images/__init__.py`
- Test: `tests/images/test_vision.py`

**Interfaces:**
- Consumes: a vision-capable model name (string) or None.
- Produces: `VisionDescriber(model_name)` with `.enabled` property and `.describe(image_url) -> Optional[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_vision.py
from unittest.mock import MagicMock, patch
from local_deep_research.images.vision import VisionDescriber

def test_disabled_when_no_model():
    v = VisionDescriber(None)
    assert v.enabled is False
    assert v.describe("https://x/a.jpg") is None

def test_disabled_when_empty_model():
    assert VisionDescriber("").enabled is False

def test_describe_returns_alt_on_success():
    v = VisionDescriber("fake-vision-model")
    assert v.enabled is True
    fake_resp = MagicMock()
    fake_resp.content = "A tall tower at night"
    with patch.object(v, "_llm") as mock_llm, patch.object(v, "_download") as mock_dl:
        mock_llm.return_value = fake_resp
        mock_dl.return_value = b"\x89PNG fake bytes"
        assert v.describe("https://x/a.jpg") == "A tall tower at night"
        mock_llm.assert_called_once()

def test_describe_returns_none_on_failure():
    v = VisionDescriber("fake-vision-model")
    with patch.object(v, "_download", side_effect=Exception("network")):
        assert v.describe("https://x/a.jpg") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_vision.py -v`. Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/local_deep_research/images/vision.py
"""Vision-LLM fallback to describe images that have no alt text."""
from __future__ import annotations

import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VisionDescriber:
    """Describes images via a vision-capable LLM. Disabled when no model configured."""

    def __init__(self, model_name: Optional[str]) -> None:
        self.model_name = (model_name or "").strip()
        self._llm = None
        if self.model_name:
            try:
                from ..config.llm_config import get_llm
                self._llm = get_llm(model_name=self.model_name)
            except Exception:
                logger.exception("Failed to init vision LLM %s; fallback disabled", self.model_name)
                self._llm = None

    @property
    def enabled(self) -> bool:
        return self._llm is not None

    def _download(self, image_url: str) -> bytes:
        from ..security.proxy_config import safe_requests  # existing SSRF-safe client
        resp = safe_requests.get(image_url, timeout=20, allow_private_ips=False)
        resp.raise_for_status()
        return resp.content

    def describe(self, image_url: str) -> Optional[str]:
        """Return a short alt description, or None on any failure."""
        if not self.enabled:
            return None
        try:
            data = self._download(image_url)
            b64 = base64.b64encode(data).decode("ascii")
            # LangChain multimodal HumanMessage with image_url.
            from langchain_core.messages import HumanMessage
            msg = HumanMessage(
                content=[
                    {"type": "text", "text": "Describe this image in one short Chinese sentence (<=30 chars). Output only the description."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]
            )
            resp = self._llm.invoke([msg])
            text = str(getattr(resp, "content", "")).strip()
            return text[:60] or None
        except Exception:
            logger.debug("Vision describe failed for %s", image_url, exc_info=True)
            return None
```

**Note for implementer:** verify the exact import path of the SSRF-safe request helper by grepping `safe_requests` / `safe_get` in `src/local_deep_research/security/` at implementation time and align `_download` to the real function name + signature (it currently uses `get(..., allow_private_ips=False)`). If the helper is named differently (e.g. `safe_get`), adjust both `_download` and the test's patch target (`_download` is patched on the instance, so the test is unaffected).

Update `__init__.py`:

```python
from .extractor import ExtractedImage, extract_images
from .bank import ImageBank
from .vision import VisionDescriber

__all__ = ["ExtractedImage", "extract_images", "ImageBank", "VisionDescriber"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_vision.py -v`. Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/vision.py src/local_deep_research/images/__init__.py tests/images/test_vision.py
git commit -m "feat(images): add VisionDescriber alt-text fallback"
```

---

## Task 4: Settings + Image model + migration

**Files:**
- Modify: `src/local_deep_research/defaults/default_settings.json`
- Create: `src/local_deep_research/database/models/images.py`
- Modify: `src/local_deep_research/database/models/research.py` (add `html_content`)
- Modify: `src/local_deep_research/database/models/__init__.py`
- Create: `src/local_deep_research/database/migrations/versions/0011_research_images.py`
- Test: `tests/images/test_models.py`

**Interfaces:**
- Produces: `Image` ORM model, `SearchResult.html_content` column, two new settings, migration `0011`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_models.py
def test_image_model_columns():
    from local_deep_research.database.models import Image
    cols = {c.name for c in Image.__table__.columns}
    for required in {"id", "research_id", "original_url", "local_path", "local_route",
                     "alt", "source_url", "source_title", "content_hash", "width", "height", "created_at"}:
        assert required in cols, required

def test_search_result_has_html_content():
    from local_deep_research.database.models import SearchResult
    cols = {c.name for c in SearchResult.__table__.columns}
    assert "html_content" in cols

def test_settings_registered():
    import json
    d = json.load(open("src/local_deep_research/defaults/default_settings.json"))
    assert "report.enable_images" in d
    assert d["report.enable_images"]["value"] is False
    assert "report.image_vision_model" in d
    assert d["report.image_vision_model"]["value"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_models.py -v`. Expected: FAIL — `ImportError: cannot import name 'Image'` and missing settings.

- [ ] **Step 3: Write minimal implementation**

Create the model:

```python
# src/local_deep_research/database/models/images.py
"""Image records bound to a research; mirrored locally."""
from sqlalchemy import Column, ForeignKey, Integer, String, Text

from .base import Base
from .common import UtcDateTime, utcnow  # match existing import style in research.py


class Image(Base):
    __tablename__ = "research_images"

    id = Column(Integer, primary_key=True)
    research_id = Column(
        String(36),
        ForeignKey("research_history.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_url = Column(Text, nullable=False)
    local_path = Column(Text, nullable=False)
    local_route = Column(Text, nullable=False)
    alt = Column(Text)
    source_url = Column(Text)
    source_title = Column(Text)
    content_hash = Column(String(64), index=True)
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(UtcDateTime, default=utcnow)
```

**Note for implementer:** confirm the exact import names `UtcDateTime` and `utcnow` by reading `database/models/research.py` top imports (the existing model uses them); adjust the import line above to match (e.g. they may come from `.common` or be defined in `research.py`).

Add `html_content` to `SearchResult` in `database/models/research.py` — insert after the existing `content = Column(Text)` line inside `class SearchResult`:

```python
    html_content = Column(Text)  # raw html from Firecrawl; used to extract <img> post-hoc
```

Export `Image` in `database/models/__init__.py` — add near the other `.from` imports (alphabetical):

```python
from .images import Image
```
and add `"Image"` to the `__all__` list if one exists (check the file).

Add settings to `defaults/default_settings.json` (insert in the existing flat key structure, respecting the existing key order — e.g. after the last `app.*` or `report.*` key if present):

```json
"report.enable_images": {
    "category": "report",
    "name": "Enable Report Images",
    "description": "Extract real images from source pages and embed them in reports. When off, reports are text-only (no images). Requires Firecrawl as the content fetcher.",
    "editable": true,
    "max_value": null,
    "min_value": null,
    "options": null,
    "step": null,
    "type": "APP",
    "ui_element": "checkbox",
    "value": false,
    "visible": true
},
"report.image_vision_model": {
    "category": "report",
    "name": "Vision Model for Image Alt Text",
    "description": "Model name with vision capability (e.g. gpt-4o, qwen-vl-max) used to describe images that have no alt text. Leave empty to disable vision fallback.",
    "editable": true,
    "max_value": null,
    "min_value": null,
    "options": null,
    "step": null,
    "type": "APP",
    "ui_element": "text",
    "value": "",
    "visible": true
}
```

Create the migration (mirror the header/docstring style of `0010_download_tracker_cascade_fk.py`):

```python
# src/local_deep_research/database/migrations/versions/0011_research_images.py
"""Create research_images table and add html_content to search_results.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("research_id", sa.String(length=36),
                  sa.ForeignKey("research_history.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("local_route", sa.Text(), nullable=False),
        sa.Column("alt", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True, index=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
    )
    op.add_column("search_results", sa.Column("html_content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("search_results", "html_content")
    op.drop_table("research_images")
```

**Note for implementer:** verify `created_at`'s column type in `research_history` (it is `Column(Text)` per the model read in exploration). If it is `Text`, the migration above matches; if `UtcDateTime` maps to a different DDL type, align it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_models.py -v`. Expected: PASS (3 tests). Then smoke-test the migration on a throwaway DB:

```bash
docker exec ldr-local bash -c 'cd /install && .venv/bin/python -c "
from alembic.config import Config
from alembic import command
# Point upgrade at a fresh temp sqlite if alembic is wired for it; otherwise
# rely on the model-import test above. Skip if alembic ini not present.
"'
```
If Alembic is not directly runnable in-container, defer migration verification to Task 8's integration test (which creates a real encrypted DB).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/database/models/images.py src/local_deep_research/database/models/research.py src/local_deep_research/database/models/__init__.py src/local_deep_research/database/migrations/versions/0011_research_images.py src/local_deep_research/defaults/default_settings.json tests/images/test_models.py
git commit -m "feat(images): Image model, SearchResult.html_content, settings, 0011 migration"
```

---

## Task 5: ImageEnhancer (LLM insertion + vision orchestration)

**Files:**
- Create: `src/local_deep_research/images/enhancer.py`
- Modify: `src/local_deep_research/images/__init__.py`
- Test: `tests/images/test_enhancer.py`

**Interfaces:**
- Consumes: `ImageBank`, `VisionDescriber` (may be disabled), and an LLM (passed in).
- Produces: `ImageEnhancer(llm, vision).enhance(markdown, bank) -> str` (markdown with real `![](url)` inserted).

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_enhancer.py
from unittest.mock import MagicMock
from local_deep_research.images.extractor import ExtractedImage
from local_deep_research.images.bank import ImageBank
from local_deep_research.images.enhancer import ImageEnhancer

def _img(url, alt):
    return ExtractedImage(url=url, alt=alt, source_url="s", source_title="t", width=None, height=None)

def test_enhance_inserts_real_url_from_bank():
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "Canton Tower")])
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# Report\n\n![Canton Tower](https://real/a.jpg)\n")
    vision = MagicMock(); vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance("# Report\n\ntext", bank)
    assert "https://real/a.jpg" in out
    llm.invoke.assert_called_once()

def test_enhance_returns_original_when_llm_fails():
    bank = ImageBank()
    bank.add([_img("https://real/a.jpg", "x")])
    llm = MagicMock(); llm.invoke.side_effect = Exception("boom")
    vision = MagicMock(); vision.enabled = False
    original = "# Report\n\ntext"
    assert ImageEnhancer(llm, vision).enhance(original, bank) == original

def test_enhance_skips_when_bank_empty():
    bank = ImageBank()
    llm = MagicMock()
    vision = MagicMock(); vision.enabled = False
    out = ImageEnhancer(llm, vision).enhance("# Report", bank)
    assert out == "# Report"
    llm.invoke.assert_not_called()

def test_vision_fallback_called_for_altless_images():
    bank = ImageBank()
    bank.add([_img("https://real/b.jpg", "")])  # no alt
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="# R\n\n![tower](https://real/b.jpg)\n")
    vision = MagicMock(); vision.enabled = True
    vision.describe.return_value = "a tower"
    ImageEnhancer(llm, vision).enhance("# R", bank)
    vision.describe.assert_called_once_with("https://real/b.jpg")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_enhancer.py -v`. Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/local_deep_research/images/enhancer.py
"""Post-processing: insert real images into a report via one LLM call."""
from __future__ import annotations

import logging
from typing import List

from .bank import ImageBank
from .extractor import ExtractedImage
from .vision import VisionDescriber

logger = logging.getLogger(__name__)

_PROMPT = """You are editing a research report to add real images.

STRICT RULES:
- You may ONLY use image URLs from the "Available images" list below.
- You MUST NOT invent, modify, or guess any image URL.
- Do NOT change any factual text, numbers, or citations in the report.
- Insert images where topically relevant using markdown: ![alt](url)
- If no available image fits a section, insert nothing there — never force an image.

Available images (url | alt | source title):
{image_list}

Report to enhance:
---
{markdown}
---

Return ONLY the enhanced report markdown, nothing else."""


def _format_list(images: List[ExtractedImage]) -> str:
    return "\n".join(f"- {i.url} | {i.alt} | {i.source_title}" for i in images)


class ImageEnhancer:
    def __init__(self, llm, vision: VisionDescriber) -> None:
        self.llm = llm
        self.vision = vision

    def _vision_fill(self, bank: ImageBank) -> None:
        if not self.vision.enabled:
            return
        for img in bank.candidates_without_alt(limit=20):
            alt = self.vision.describe(img.url)
            if alt:
                bank.set_alt(img.url, alt)

    def enhance(self, markdown: str, bank: ImageBank) -> str:
        candidates = bank.candidates_with_alt()
        if not candidates:
            self._vision_fill(bank)
            candidates = bank.candidates_with_alt()
            if not candidates:
                return markdown
        try:
            prompt = _PROMPT.format(image_list=_format_list(candidates), markdown=markdown)
            resp = self.llm.invoke(prompt)
            enhanced = str(getattr(resp, "content", "")).strip()
            if not enhanced:
                return markdown
            return enhanced
        except Exception:
            logger.exception("Image enhancement failed; returning original markdown")
            return markdown
```

Update `__init__.py` to export `ImageEnhancer`:

```python
from .extractor import ExtractedImage, extract_images
from .bank import ImageBank
from .vision import VisionDescriber
from .enhancer import ImageEnhancer

__all__ = ["ExtractedImage", "extract_images", "ImageBank", "VisionDescriber", "ImageEnhancer"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_enhancer.py -v`. Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/enhancer.py src/local_deep_research/images/__init__.py tests/images/test_enhancer.py
git commit -m "feat(images): add ImageEnhancer post-processing LLM insertion"
```

---

## Task 6: ImageStore (download + DB + markdown rewrite)

**Files:**
- Create: `src/local_deep_research/images/store.py`
- Modify: `src/local_deep_research/images/__init__.py`
- Test: `tests/images/test_store.py`

**Interfaces:**
- Consumes: `research_id` (str), a DB session for the user, the bank's alt map.
- Produces: `ImageStore(research_id, db_session, base_dir).persist(urls) -> Dict[url, local_route]` and `.rewrite_markdown(markdown, url_to_route) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_store.py
import re
from unittest.mock import MagicMock, patch
from local_deep_research.images.store import ImageStore

def test_persist_downloads_and_returns_routes(tmp_path):
    store = ImageStore("rid-123", db_session=MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (b"\x89PNG fake", "image/png")
        routes = store.persist(["https://x/a.jpg"])
    assert "https://x/a.jpg" in routes
    route = routes["https://x/a.jpg"]
    assert route.startswith("/images/rid-123/")
    # local file created
    local_files = list((tmp_path / "rid-123").iterdir())
    assert len(local_files) == 1

def test_persist_skips_failed_download(tmp_path):
    store = ImageStore("rid", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download", side_effect=Exception("net")):
        assert store.persist(["https://x/a.jpg"]) == {}

def test_rewrite_markdown_replaces_urls():
    store = ImageStore("rid", MagicMock(), base_dir="/tmp")
    md = "![t](https://x/a.jpg) and ![u](https://y/b.jpg)"
    out = store.rewrite_markdown(md, {"https://x/a.jpg": "/images/rid/h1.png"})
    assert "/images/rid/h1.png" in out
    assert "https://y/b.jpg" in out  # unmapped url left intact

def test_persist_path_traversal_safe(tmp_path):
    store = ImageStore("..%2fevil", MagicMock(), base_dir=tmp_path)
    with patch.object(store, "_download") as dl:
        dl.return_value = (b"\x89PNG", "image/png")
        routes = store.persist(["https://x/a.jpg"])
    # route must contain only the safe research_id segment, no traversal
    route = routes["https://x/a.jpg"]
    assert ".." not in route
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_store.py -v`. Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/local_deep_research/images/store.py
"""Download chosen images to a local mirror, record in DB, rewrite markdown URLs."""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)]+)\)")


class ImageStore:
    def __init__(self, research_id: str, db_session, base_dir: Path = Path("/data/images")) -> None:
        # Sanitize research_id: keep only chars safe for a path segment.
        self.research_id = re.sub(r"[^A-Za-z0-9_-]", "_", research_id)
        self.db_session = db_session
        self.base_dir = Path(base_dir)

    def _download(self, url: str) -> Optional[Tuple[bytes, str]]:
        from ..security.proxy_config import safe_requests
        resp = safe_requests.get(url, timeout=30, allow_private_ips=False)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        return resp.content, ctype

    @staticmethod
    def _ext_for(content_type: str) -> str:
        mapping = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                   "image/webp": ".webp", "image/gif": ".gif"}
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
                rel = f"{self.research_id}/{digest}{ext}"
                local_path = self.base_dir / self.research_id / f"{digest}{ext}"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)
                route = f"/images/{rel}"
                self._record(url, str(local_path), route, ctype, len(data), digest)
                url_to_route[url] = route
            except Exception:
                logger.debug("Image persist failed for %s", url, exc_info=True)
        return url_to_route

    def _record(self, url, local_path, route, ctype, size, digest) -> None:
        try:
            from ..database.models import Image
            self.db_session.add(Image(
                research_id=self.research_id, original_url=url,
                local_path=local_path, local_route=route, alt=None,
                source_url=None, source_title=None, content_hash=digest,
            ))
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
```

**Note for implementer:** `_record` writes `research_id` matching the `String(36)` FK. If `research_id` after sanitization differs from the real UUID (it should not — UUIDs are `[0-9a-f-]`), persist the ORIGINAL research_id on the row but keep the sanitized one only for the filesystem path. To keep it simple and correct for UUIDs, store the original `research_id` passed in: change `_record` to accept and store the original id, and `local_route`/filesystem to use the sanitized form. Apply this split in implementation if the test `test_persist_path_traversal_safe` still passes.

Update `__init__.py`:

```python
from .extractor import ExtractedImage, extract_images
from .bank import ImageBank
from .vision import VisionDescriber
from .enhancer import ImageEnhancer
from .store import ImageStore

__all__ = ["ExtractedImage", "extract_images", "ImageBank", "VisionDescriber", "ImageEnhancer", "ImageStore"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_store.py -v`. Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/store.py src/local_deep_research/images/__init__.py tests/images/test_store.py
git commit -m "feat(images): add ImageStore — mirror, record, rewrite"
```

---

## Task 7: Firecrawl scrape returns HTML + engine wiring

**Files:**
- Modify: `src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py` (scrape method, ~line 42)
- Modify: `src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py` (`_get_full_content`, ~line 151)
- Test: `tests/images/test_firecrawl_scrape_html.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `FirecrawlClient.scrape(url) -> Optional[Dict]` returning `{"markdown": str, "html": Optional[str]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_firecrawl_scrape_html.py
from unittest.mock import MagicMock, patch
from local_deep_research.research_library.downloaders.extraction.firecrawl_client import FirecrawlClient

def test_scrape_returns_dict_with_markdown_and_html():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"markdown": "# hi", "html": "<html><img src='x'></html>"}}
    with patch.object(client, "_post", return_value=fake) if hasattr(client, "_post") else \
         patch("local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post", return_value=fake):
        result = client.scrape("https://example.com")
    assert isinstance(result, dict)
    assert result["markdown"] == "# hi"
    assert result["html"].startswith("<html")

def test_scrape_returns_none_on_http_error():
    client = FirecrawlClient(api_url="http://fc:3002")
    fake = MagicMock(); fake.status_code = 500; fake.json.return_value = {}
    with patch("local_deep_research.research_library.downloaders.extraction.firecrawl_client.safe_post", return_value=fake):
        assert client.scrape("https://example.com") is None
```

**Note for implementer:** inspect `firecrawl_client.py` to find the exact symbol used for the HTTP call (it is `safe_post` per exploration) and patch that; the test above guards both shapes.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_firecrawl_scrape_html.py -v`. Expected: FAIL — scrape returns a `str`, so `result["markdown"]` raises `TypeError`.

- [ ] **Step 3: Write minimal implementation**

Edit `scrape()` in `firecrawl_client.py` — replace the `payload` and the return parsing. New body (keep the existing try/except/429 handling; only change payload + success return):

```python
    def scrape(self, url: str) -> Optional[Dict[str, Any]]:
        """Scrape a single URL; return {markdown, html} or None on failure.

        Raises RateLimitError on HTTP 429 so the engine layer can propagate it.
        """
        payload = {"url": url, "formats": ["markdown", "html"]}
        try:
            resp = safe_post(
                f"{self.api_url}/v1/scrape",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
                allow_private_ips=True,
            )
        except Exception:
            logger.debug(f"Firecrawl scrape request failed for {url}", exc_info=True)
            return None
        if resp.status_code == 429:
            raise RateLimitError("Firecrawl scrape rate limited")
        if resp.status_code >= 400:
            logger.debug(f"Firecrawl scrape failed for {url}: HTTP {resp.status_code}")
            return None
        try:
            data = resp.json().get("data", {})
            md = data.get("markdown")
            if not (isinstance(md, str) and md.strip()):
                return None
            html = data.get("html")
            return {"markdown": md, "html": html if isinstance(html, str) else None}
        except Exception:
            logger.debug(f"Firecrawl scrape parse failed for {url}", exc_info=True)
            return None
```

Ensure `Dict, Any` are imported at the top of the file (add `from typing import Any, Dict, List, Optional` if not present).

Then adapt the engine caller in `search_engine_firecrawl.py` `_get_full_content` (around line 148-156):

```python
            full = item.get("_full_result") or {}
            md = full.get("markdown")
            html = full.get("html")
            if not (isinstance(md, str) and md.strip()):
                link = item.get("link")
                if link:
                    try:
                        scraped = self._client.scrape(link)
                    except Exception:
                        logger.debug(f"Firecrawl scrape failed for {link}", exc_info=True)
                        scraped = None
                    if isinstance(scraped, dict):
                        md = scraped.get("markdown")
                        html = scraped.get("html")
            item = dict(item)
            item["content"] = md or item.get("content", "")
            item["html_content"] = html  # may be None; consumed in post-processing
            results.append(item)
```

**Note for implementer:** confirm `FullSearchResults` (which wraps this engine and is the actual class registered) passes `_full_result`/`html_content` through to where search results are persisted. Grep `html_content` and `_full_result` in `full_search.py` and the persistence path (`research_service` / findings repository); if the dict-key is stripped, also propagate `html_content` alongside `content` there. This propagation is what makes `SearchResult.html_content` actually populated — verify with the Task 8 integration test.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_firecrawl_scrape_html.py -v`. Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/research_library/downloaders/extraction/firecrawl_client.py src/local_deep_research/web_search_engines/engines/search_engine_firecrawl.py tests/images/test_firecrawl_scrape_html.py
git commit -m "feat(firecrawl): scrape returns markdown+html; engine stores html_content"
```

---

## Task 8: Post-processing wiring in run_research_process

**Files:**
- Modify: `src/local_deep_research/web/services/research_service.py` (insert block between `clean_markdown` log at ~1083 and the formatter call at ~1097)
- Test: `tests/images/test_postprocessing.py`

**Interfaces:**
- Consumes: `ImageBank`, `ImageEnhancer`, `ImageStore`, `extract_images`, settings via `get_setting_from_snapshot`, `get_llm`.
- Produces: a `enhance_report_with_images(research_id, clean_markdown, results, db_session) -> str` helper called inline.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_postprocessing.py
from unittest.mock import MagicMock, patch
from local_deep_research.images.postprocessing import enhance_report_with_images

def test_disabled_returns_markdown_unchanged():
    out = enhance_report_with_images(
        research_id="rid", clean_markdown="# hi", results={"findings": []},
        db_session=MagicMock(), enable_images=False, vision_model="",
    )
    assert out == "# hi"

def test_enabled_builds_bank_from_findings_and_enhances():
    findings = [{
        "search_results": [{
            "url": "https://src/page", "title": "Page",
            "html_content": '<img src="https://real/a.jpg" alt="tower" width="200">',
        }],
    }]
    with patch("local_deep_research.images.postprocessing.get_llm") as gl, \
         patch("local_deep_research.images.postprocessing.ImageEnhancer") as IEnh, \
         patch("local_deep_research.images.postprocessing.ImageStore") as IStore:
        gl.return_value = MagicMock()
        inst = IEnh.return_value
        inst.enhance.return_value = "# R\n\n![tower](https://real/a.jpg)\n"
        store_inst = IStore.return_value
        store_inst.persist.return_value = {"https://real/a.jpg": "/images/rid/a.png"}
        store_inst.rewrite_markdown.side_effect = lambda md, m: md.replace("https://real/a.jpg", "/images/rid/a.png")
        out = enhance_report_with_images(
            research_id="rid", clean_markdown="# R", results={"findings": findings},
            db_session=MagicMock(), enable_images=True, vision_model="",
        )
    assert "/images/rid/a.png" in out
    IEnh.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_postprocessing.py -v`. Expected: FAIL — `ImportError: ...postprocessing`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/local_deep_research/images/postprocessing.py
"""Top-level post-processing entry: build bank, enhance, mirror, rewrite."""
from __future__ import annotations

import logging
from typing import Any, Dict

from .bank import ImageBank
from .enhancer import ImageEnhancer
from .extractor import extract_images
from .store import ImageStore
from .vision import VisionDescriber

logger = logging.getLogger(__name__)


def enhance_report_with_images(
    *,
    research_id: str,
    clean_markdown: str,
    results: Dict[str, Any],
    db_session,
    enable_images: bool,
    vision_model: str,
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
                html = sr.get("html_content")
                if html:
                    bank.add(extract_images(html, sr.get("url", ""), sr.get("title", "")))
        if not bank.all_urls():
            return clean_markdown

        from ..config.llm_config import get_llm
        llm = get_llm()
        vision = VisionDescriber(vision_model)
        enhanced = ImageEnhancer(llm, vision).enhance(clean_markdown, bank)

        # Persist the real URLs that survived into the enhanced markdown.
        from .store import _IMG_RE  # reuse the same regex
        chosen = [m.group(2) for m in _IMG_RE.finditer(enhanced)]
        store = ImageStore(research_id, db_session)
        url_to_route = store.persist(chosen)
        if url_to_route:
            enhanced = store.rewrite_markdown(enhanced, url_to_route)
        return enhanced
    except Exception:
        logger.exception("Image post-processing failed; returning clean markdown")
        return clean_markdown
```

**Note for implementer:** `_IMG_RE` must be importable from `store.py` — it is defined at module level there (Task 6). Confirm.

Then wire it into `research_service.py`. In `run_research_process`, immediately AFTER the `logger.info("Successfully converted to clean markdown of length: {}", len(clean_markdown))` block (around line 1083) and BEFORE the `progress_callback("Generating clean summary..." ...)` call, insert:

```python
                    # === Image post-processing (gated by report.enable_images) ===
                    try:
                        from ...images.postprocessing import enhance_report_with_images
                        from ..config.thread_settings import get_setting_from_snapshot
                        enable_images = get_setting_from_snapshot(
                            "report.enable_images", False, settings_snapshot=settings_snapshot
                        )
                        vision_model = get_setting_from_snapshot(
                            "report.image_vision_model", "", settings_snapshot=settings_snapshot
                        )
                        if enable_images:
                            progress_callback(
                                "Enhancing report with real images...",
                                92,
                                {"phase": "image_enhancement"},
                            )
                            clean_markdown = enhance_report_with_images(
                                research_id=research_id,
                                clean_markdown=clean_markdown,
                                results=results,
                                db_session=db_session,
                                enable_images=True,
                                vision_model=vision_model,
                            )
                    except Exception:
                        logger.exception("Image enhancement step failed; continuing with text-only report")
```

**Note for implementer:** verify the names `settings_snapshot` and `db_session` are in scope at that point in `run_research_process` (grep both within the function). `settings_snapshot` is threaded through research runs; `db_session` may need to be opened via `get_user_db_session(username)` if not already in scope — if so, wrap the `enhance_report_with_images` call in a `with get_user_db_session(username) as db_session:` block. Adjust to the real in-scope variables.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_postprocessing.py -v`. Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/images/postprocessing.py src/local_deep_research/web/services/research_service.py tests/images/test_postprocessing.py
git commit -m "feat(images): wire image post-processing into run_research_process"
```

---

## Task 9: Serve route + list API + cascade delete

**Files:**
- Modify: `src/local_deep_research/web/routes/research_routes.py` (new routes + `delete_research` cleanup, ~line 915)
- Test: `tests/images/test_routes.py`

**Interfaces:**
- Produces: `GET /images/<research_id>/<filename>`, `GET /api/research/<id>/images`, and cascade-delete of `/data/images/<rid>/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/images/test_routes.py
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

def test_image_route_serves_file_and_blocks_traversal(tmp_path):
    # place a fake image
    rid_dir = tmp_path / "rid"
    rid_dir.mkdir()
    (rid_dir / "a.png").write_bytes(b"\x89PNG")
    from local_deep_research.web.routes import research_routes
    # monkeypatch the base dir the route reads from
    with patch.object(research_routes, "_IMAGES_BASE_DIR", tmp_path):
        resp = research_routes.serve_research_image("rid", "a.png")
        assert resp.status_code == 200
    # traversal rejected
    with patch.object(research_routes, "_IMAGES_BASE_DIR", tmp_path):
        resp = research_routes.serve_research_image("rid", "../../etc/passwd")
        assert resp.status_code == 404

def test_delete_research_removes_image_dir(tmp_path):
    from local_deep_research.web.routes import research_routes
    rid_dir = tmp_path / "rid"; rid_dir.mkdir()
    (rid_dir / "a.png").write_bytes(b"x")
    with patch.object(research_routes, "_IMAGES_BASE_DIR", tmp_path):
        research_routes._cleanup_image_dir("rid")
    assert not rid_dir.exists()
    # idempotent when missing
    research_routes._cleanup_image_dir("rid")  # no error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/images/test_routes.py -v`. Expected: FAIL — no `serve_research_image` / `_cleanup_image_dir` / `_IMAGES_BASE_DIR`.

- [ ] **Step 3: Write minimal implementation**

Add to `research_routes.py` (top-of-module constant + two routes + helper):

```python
from pathlib import Path
_IMAGES_BASE_DIR = Path("/data/images")


def _cleanup_image_dir(research_id: str) -> None:
    """Remove the local image mirror for a research (idempotent)."""
    try:
        d = _IMAGES_BASE_DIR / research_id
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        logger.exception("Error removing image dir for %s", research_id)


@research_bp.route("/images/<research_id>/<path:filename>")
def serve_research_image(research_id, filename):
    """Serve a locally-mirrored research image with path-traversal protection."""
    from flask import abort, send_from_directory
    # Reject anything that escapes the research's directory.
    rid_safe = Path(research_id).name
    fname_safe = Path(filename).name
    if research_id != rid_safe or filename != fname_safe:
        abort(404)
    directory = _IMAGES_BASE_DIR / rid_safe
    target = directory / fname_safe
    try:
        target.resolve().relative_to(directory.resolve())
    except (ValueError, OSError):
        abort(404)
    if not target.is_file():
        abort(404)
    return send_from_directory(directory, fname_safe)


@research_bp.route("/api/research/<string:research_id>/images")
def list_research_images(research_id):
    """List all mirrored images for a research."""
    from flask import jsonify, session
    from ...database.encrypted_db import db_manager
    from ...database.models import Image
    username = session["username"]
    sess = db_manager.get_session(username)
    if sess is None:
        return jsonify({"status": "error", "message": "No db session"}), 500
    try:
        rows = sess.query(Image).filter_by(research_id=research_id).all()
        return jsonify({
            "status": "success",
            "images": [
                {"local_route": r.local_route, "original_url": r.original_url,
                 "alt": r.alt, "source_url": r.source_url, "source_title": r.source_title}
                for r in rows
            ],
        })
    finally:
        sess.close()
```

Ensure `shutil` is imported at the top of `research_routes.py`.

Add the cascade cleanup to `delete_research(research_id)` (the function at ~line 915). Immediately before `db_session.delete(research)`:

```python
            # Remove locally-mirrored images for this research
            _cleanup_image_dir(research_id)
```

(DB rows in `research_images` cascade automatically via the FK `ON DELETE CASCADE`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/images/test_routes.py -v`. Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/local_deep_research/web/routes/research_routes.py tests/images/test_routes.py
git commit -m "feat(images): serve route, list API, cascade delete cleanup"
```

---

## Task 10: Integration verification (end-to-end in container)

**Files:** none (verification only; may add `tests/images/test_integration.py`)

- [ ] **Step 1: Build/restart the local container with the new source**

Per project memory, source is hot-mounted; restart to pick up model + migration:

```bash
docker compose -f docker-compose.ldr-local.yml restart local-deep-research
```

- [ ] **Step 2: Run the migration against the running user DB**

```bash
docker exec ldr-local bash -c 'cd /install && .venv/bin/python -c "
from local_deep_research.database.alembic_runner import *
# Use the existing alembic upgrade entrypoint used at app boot; if the app
# auto-runs migrations on start, simply confirm revision is 0011:
"'
docker exec ldr-local bash -c 'cd /install && .venv/bin/python -c "
from local_deep_research.database.encrypted_db import db_manager
eng = db_manager.open_user_database(\"admin\", \"123456aB\")
from sqlalchemy import text
with eng.connect() as c:
    print(c.execute(text(\"PRAGMA table_info(research_images)\")).fetchall()[:3])
    print(\"html_content col:\", any(r[1]==\"html_content\" for r in c.execute(text(\"PRAGMA table_info(search_results)\")).fetchall()))
"'
```
Expected: `research_images` columns present; `search_results` has `html_content`.

- [ ] **Step 3: Enable the feature and re-run a small research**

Set `report.enable_images = true` (and optionally a vision model) via the WebUI settings page or directly in the user DB, then trigger a short research whose source pages have images.

- [ ] **Step 4: Verify the report contains local image routes**

```bash
docker exec ldr-local bash -c 'cd /install && .venv/bin/python -c "
from local_deep_research.database.encrypted_db import db_manager
from sqlalchemy import text
eng = db_manager.open_user_database(\"admin\", \"123456aB\")
with eng.connect() as c:
    row = c.execute(text(\"SELECT id, report_content FROM research_history ORDER BY created_at DESC LIMIT 1\")).fetchone()
    rc = row[1] or \"\"
    print(\"has /images/ route:\", \"/images/\" in rc)
    print(\"sample:\", [l for l in rc.split(chr(10)) if \"/images/\" in l][:3])
"'
```
Expected: `/images/` routes present in the newest report.

- [ ] **Step 5: Verify a mirrored file exists and is served**

```bash
docker exec ldr-local bash -c 'ls -la /data/images/ | head; find /data/images -type f | head'
curl -sI http://localhost:5000/images/<rid>/<file>.png | head -1   # expect HTTP 200
```

- [ ] **Step 6: Verify cascade delete removes files**

Delete the research via WebUI, then:

```bash
docker exec ldr-local bash -c 'ls /data/images/<rid> 2>&1'  # expect: No such file or directory
```

- [ ] **Step 7: Commit any integration test added**

```bash
git add tests/images/test_integration.py  # if created
git commit -m "test(images): end-to-end integration verification"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §3 decisions: off-by-default ✓ (Task 8 gating), vision fallback ✓ (Task 3+5), local mirror ✓ (Task 6), cascade delete ✓ (Task 9), alt-first ✓ (Task 5), scrape html ✓ (Task 7).
- §4 phases: build bank from html_content ✓ (Task 8), enhance ✓ (Task 5), vision ✓ (Task 3), persist+rewrite ✓ (Task 6).
- §5 components: extractor/bank/vision/enhancer/store ✓ (Tasks 1,2,3,5,6) + postprocessing glue ✓ (Task 8).
- §6 model + migration ✓ (Task 4).
- §7 change list: every file mapped to a task ✓.
- §8 cascade ✓ (Task 9).
- §9 settings ✓ (Task 4).
- §10 error handling: per-image skip ✓, vision None ✓, disabled bypass ✓, post-processing try/except returns original ✓.
- §11 tests: each component has a unit test; integration in Task 10.
- §12 risk: path-traversal ✓ (Task 9), store safe id ✓ (Task 6).

**Placeholder scan:** No "TBD"/"TODO". Where exact in-scope variable names (`settings_snapshot`, `db_session`, `safe_post`, `UtcDateTime` import) could not be 100% confirmed without reading at implementation time, explicit "Note for implementer" instructions tell the engineer exactly what to verify and how — these are verification steps, not placeholders.

**Type consistency:** `scrape() -> Optional[Dict]` used identically in Task 7 producer and consumer; `ImageStore.persist(urls) -> Dict[url, route]` consistent across Task 6/8; `_IMG_RE` reused in Task 8 imported from Task 6's store.py; `ImageEnhancer.enhance(markdown, bank)` consistent Task 5/8; `VisionDescriber(model_name)` consistent Task 3/8.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-report-image-extraction.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
