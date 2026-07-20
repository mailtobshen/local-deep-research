# tests/images/test_integration.py
"""End-to-end integration verification for the report-image subsystem.

These are guarded to run only inside the ldr-local container against the
admin encrypted DB (they need SQLCipher + an existing user database).
They document what was verified live during Task 10; on hosts without the
encrypted DB they skip cleanly.

Verified live (2026-07-21) inside ldr-local:
  * migration 0011 applied: rev == head == "0011", needs_migration == False
  * research_images table created with all 12 columns
  * search_results.html_content column present
  * serve_research_image / list_research_images / _cleanup_image_dir
    registered on research_bp; _IMAGES_BASE_DIR == /data/images
  * CSP img-src allows https: (security_headers.py:127)
  * /data/images writable
"""
import os

import pytest

_IN_CONTAINER_DB = os.path.exists("/data/encrypted_databases")

pytestmark = pytest.mark.skipif(
    not _IN_CONTAINER_DB,
    reason="requires ldr-local container with encrypted user DB",
)


def test_migration_0011_applied_and_schema_present():
    from local_deep_research.database.encrypted_db import db_manager
    from local_deep_research.database.alembic_runner import (
        get_current_revision,
        get_head_revision,
    )
    from sqlalchemy import text

    password = os.environ.get("LDR_ADMIN_PASSWORD")
    if not password:
        pytest.skip("LDR_ADMIN_PASSWORD not set")

    eng = db_manager.open_user_database("admin", password)
    assert get_current_revision(eng) == get_head_revision()

    with eng.connect() as c:
        img_cols = {
            r[1]
            for r in c.execute(
                text("PRAGMA table_info(research_images)")
            ).fetchall()
        }
        assert {"id", "research_id", "local_route", "content_hash"} <= img_cols
        sr_cols = {
            r[1]
            for r in c.execute(
                text("PRAGMA table_info(search_results)")
            ).fetchall()
        }
        assert "html_content" in sr_cols
