"""Add ON DELETE CASCADE to download_tracker / download_duplicates / download_attempts FKs.

Background
==========
``delete_research()`` deletes a ``ResearchHistory`` row via ORM
``db_session.delete()``, which — with ``passive_deletes=True`` on the
``ResearchResource -> download_queue`` relationship (added in commit
02190ed1) — delegates the cascade to the database's DDL
``ON DELETE CASCADE``. That delegation only works when *every* FK on the
delete path actually carries ``ON DELETE CASCADE``.

Three FKs did not:

    download_tracker.first_resource_id  -> research_resources.id   (NO ACTION)
    download_duplicates.resource_id      -> research_resources.id   (NO ACTION)
    download_duplicates.url_hash         -> download_tracker.url_hash (NO ACTION)
    download_attempts.url_hash           -> download_tracker.url_hash (NO ACTION)

With ``PRAGMA foreign_keys = ON`` (default since v1.6.0), deleting any
research whose resources were referenced by ``download_tracker`` raised::

    sqlcipher3.dbapi2.IntegrityError: FOREIGN KEY constraint failed

(The earlier ``NOT NULL constraint failed`` on ``download_queue`` was the
same class of bug, fixed by 02190ed1 + the DDL CASCADE on
``download_queue.resource_id``; these three FKs were the remaining gap.)

``download_tracker.first_resource_id`` is ``NOT NULL``, so it cannot be
``SET NULL`` — ``CASCADE`` is the only correct choice and matches the
``download_queue.resource_id`` semantics (a tracker/duplicate/attempt is
derived from its resource and is meaningless once the resource is gone).

SQLite cannot ALTER an existing FK in place, so each table is dropped and
recreated with the corrected FKs, preserving all columns, the UNIQUE
constraint on ``download_tracker.url_hash`` (used as an FK target by
``download_duplicates`` / ``download_attempts``), every index, and all
existing rows. ``PRAGMA foreign_key_check`` is run before and after to
guarantee no orphans are introduced or pre-existing.

Idempotent: if a table's FK is already ``CASCADE`` (e.g. a fresh DB
created from corrected models), the rebuild is skipped.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from loguru import logger
from sqlalchemy import inspect, text
from sqlalchemy_utc import UtcDateTime

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fk_on_delete(bind, table_name: str, column_name: str):
    """Return the on_delete rule for (table.column) FK, or None if absent."""
    inspector = inspect(bind)
    try:
        fks = inspector.get_foreign_keys(table_name)
    except Exception:
        return None
    for fk in fks:
        # fk["constrained_columns"] is a list; match single-column FKs.
        if fk.get("constrained_columns") == [column_name]:
            return (fk.get("ondelete") or "NO ACTION").upper()
    return None


def _has_table(bind, table_name: str) -> bool:
    return inspect(bind).has_table(table_name)


def _row_count(bind, table_name: str) -> int:
    return (
        bind.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608 — hardcoded literal
        ).scalar()
        or 0
    )


def _assert_no_orphans(bind, table_name: str) -> None:
    """Whole-DB FK integrity check. Aborts the migration if violations exist."""
    rows = bind.execute(text("PRAGMA foreign_key_check")).fetchall()
    if rows:
        details = "; ".join(f"{r[0]} rowid={r[1]} -> {r[2]}" for r in rows[:10])
        raise RuntimeError(
            f"0010: PRAGMA foreign_key_check found orphan rows in {table_name} "
            f"rebuild context: {details}. Aborting; clean up orphans first."
        )


def _fk_already_cascade(bind, table_name: str, column_name: str) -> bool:
    return _fk_on_delete(bind, table_name, column_name) == "CASCADE"


# ---------------------------------------------------------------------------
# Per-table rebuilds
# ---------------------------------------------------------------------------

def _rebuild_with_preserve(
    bind,
    table_name: str,
    create_fn,
    select_cols: str,
) -> None:
    """Drop+recreate a table preserving rows, under PRAGMA foreign_keys=OFF.

    SQLite cannot ALTER an FK in place. Idiom (FK enforcement OFF):
      1. create_fn("_ldr_new")        -- corrected FKs, temp name
      2. INSERT INTO _ldr_new SELECT ... FROM <table>
      3. DROP TABLE <table>           -- inbound FKs transiently unresolved
      4. ALTER TABLE _ldr_new RENAME TO <table>
      5. recreate indexes (caller does this after return)
    FK enforcement is OFF for steps 1-4, so the moment <table_name> is
    absent is harmless. The final whole-DB foreign_key_check in upgrade()
    runs with FKs back ON after ALL tables are rebuilt.

    ``create_fn(name)`` builds the corrected table under ``name`` via op.
    """
    _assert_no_orphans(bind, table_name)

    conn = bind
    conn.execute(text("PRAGMA foreign_keys = OFF"))
    try:
        conn.execute(text("DROP TABLE IF EXISTS _ldr_new"))
        conn.execute(text("DROP TABLE IF EXISTS _ldr_old"))

        create_fn("_ldr_new")

        conn.execute(
            text(
                f"INSERT INTO _ldr_new ({select_cols}) "
                f"SELECT {select_cols} FROM {table_name}"
            )
        )
        conn.execute(text(f"DROP TABLE {table_name}"))
        conn.execute(text("ALTER TABLE _ldr_new RENAME TO " + table_name))
    except Exception:
        # Best-effort: drop the half-built _ldr_new so the original table
        # is untouched. The caller's transaction rolls back everything.
        try:
            conn.execute(text("DROP TABLE IF EXISTS _ldr_new"))
        except Exception:
            pass
        raise
    finally:
        conn.execute(text("PRAGMA foreign_keys = ON"))


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()

    # --- download_tracker -------------------------------------------------
    if _has_table(bind, "download_tracker") and not _fk_already_cascade(
        bind, "download_tracker", "first_resource_id"
    ):
        n = _row_count(bind, "download_tracker")
        logger.info(f"0010: rebuilding download_tracker ({n} row(s))")

        def _create_tracker(name):
            op.create_table(
                name,
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("url", sa.Text(), nullable=False),
                sa.Column("url_hash", sa.String(64), nullable=False),
                sa.Column(
                    "first_resource_id",
                    sa.Integer(),
                    sa.ForeignKey("research_resources.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("file_hash", sa.String(64), nullable=True),
                sa.Column("file_path", sa.Text(), nullable=True),
                sa.Column("file_name", sa.String(255), nullable=True),
                sa.Column("file_size", sa.Integer(), nullable=True),
                sa.Column("is_downloaded", sa.Boolean(), nullable=False),
                sa.Column("is_accessible", sa.Boolean(), nullable=True),
                sa.Column("first_seen", UtcDateTime(), nullable=False),
                sa.Column("downloaded_at", UtcDateTime(), nullable=True),
                sa.Column("last_checked", UtcDateTime(), nullable=False),
                sa.Column(
                    "library_document_id",
                    sa.String(36),
                    sa.ForeignKey("documents.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.UniqueConstraint("url_hash", name="uq_download_tracker_url_hash"),
            )

        _rebuild_with_preserve(
            bind,
            "download_tracker",
            _create_tracker,
            "id, url, url_hash, first_resource_id, file_hash, file_path, "
            "file_name, file_size, is_downloaded, is_accessible, first_seen, "
            "downloaded_at, last_checked, library_document_id",
        )
        op.create_index("ix_download_tracker_file_hash", "download_tracker", ["file_hash"])
        op.create_index("ix_download_tracker_is_downloaded", "download_tracker", ["is_downloaded"])
        op.create_index("ix_download_tracker_file_name", "download_tracker", ["file_name"])
        logger.info("0010: download_tracker rebuilt with CASCADE on first_resource_id")
    else:
        logger.info("0010: download_tracker already CASCADE or absent — skipping")

    # --- download_duplicates ---------------------------------------------
    if _has_table(bind, "download_duplicates") and (
        not _fk_already_cascade(bind, "download_duplicates", "resource_id")
        or not _fk_already_cascade(bind, "download_duplicates", "url_hash")
    ):
        n = _row_count(bind, "download_duplicates")
        logger.info(f"0010: rebuilding download_duplicates ({n} row(s))")

        def _create_duplicates(name):
            op.create_table(
                name,
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column(
                    "url_hash",
                    sa.String(64),
                    sa.ForeignKey("download_tracker.url_hash", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column(
                    "resource_id",
                    sa.Integer(),
                    sa.ForeignKey("research_resources.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("research_id", sa.String(36), nullable=False),
                sa.Column("added_at", UtcDateTime(), nullable=False),
                sa.UniqueConstraint("url_hash", "resource_id", name="uix_url_resource"),
            )

        _rebuild_with_preserve(
            bind,
            "download_duplicates",
            _create_duplicates,
            "id, url_hash, resource_id, research_id, added_at",
        )
        op.create_index("ix_download_duplicates_research_id", "download_duplicates", ["research_id"])
        op.create_index("ix_download_duplicates_url_hash", "download_duplicates", ["url_hash"])
        op.create_index("idx_research_duplicates", "download_duplicates", ["research_id", "url_hash"])
        logger.info("0010: download_duplicates rebuilt with CASCADE FKs")
    else:
        logger.info("0010: download_duplicates already CASCADE or absent — skipping")

    # --- download_attempts ------------------------------------------------
    if _has_table(bind, "download_attempts") and not _fk_already_cascade(
        bind, "download_attempts", "url_hash"
    ):
        n = _row_count(bind, "download_attempts")
        logger.info(f"0010: rebuilding download_attempts ({n} row(s))")

        def _create_attempts(name):
            op.create_table(
                name,
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column(
                    "url_hash",
                    sa.String(64),
                    sa.ForeignKey("download_tracker.url_hash", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("attempt_number", sa.Integer(), nullable=False),
                sa.Column("status_code", sa.Integer(), nullable=True),
                sa.Column("error_type", sa.String(100), nullable=True),
                sa.Column("error_message", sa.Text(), nullable=True),
                sa.Column("attempted_at", UtcDateTime(), nullable=False),
                sa.Column("duration_ms", sa.Integer(), nullable=True),
                sa.Column("succeeded", sa.Boolean(), nullable=False),
                sa.Column("bytes_downloaded", sa.Integer(), nullable=True),
            )

        _rebuild_with_preserve(
            bind,
            "download_attempts",
            _create_attempts,
            "id, url_hash, attempt_number, status_code, error_type, "
            "error_message, attempted_at, duration_ms, succeeded, bytes_downloaded",
        )
        op.create_index("ix_download_attempts_url_hash", "download_attempts", ["url_hash"])
        logger.info("0010: download_attempts rebuilt with CASCADE on url_hash")
    else:
        logger.info("0010: download_attempts already CASCADE or absent — skipping")

    # Final whole-DB integrity gate.
    bad = bind.execute(text("PRAGMA foreign_key_check")).fetchall()
    if bad:
        raise RuntimeError(f"0010: final foreign_key_check failed: {bad[:5]}")
    logger.info("0010: FK rebuild complete; foreign_key_check clean")


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    """Restore the prior NO-ACTION FKs.

    Re-introduces the original ``FOREIGN KEY constraint failed`` bug on
    research deletion for any research whose resources are tracked. Only
    sensible during testing.
    """
    bind = op.get_bind()

    def _create_tracker_old(name):
        op.create_table(
            name,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("url_hash", sa.String(64), nullable=False),
            sa.Column(
                "first_resource_id",
                sa.Integer(),
                sa.ForeignKey("research_resources.id"),  # NO ACTION
                nullable=False,
            ),
            sa.Column("file_hash", sa.String(64), nullable=True),
            sa.Column("file_path", sa.Text(), nullable=True),
            sa.Column("file_name", sa.String(255), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("is_downloaded", sa.Boolean(), nullable=False),
            sa.Column("is_accessible", sa.Boolean(), nullable=True),
            sa.Column("first_seen", UtcDateTime(), nullable=False),
            sa.Column("downloaded_at", UtcDateTime(), nullable=True),
            sa.Column("last_checked", UtcDateTime(), nullable=False),
            sa.Column(
                "library_document_id",
                sa.String(36),
                sa.ForeignKey("documents.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.UniqueConstraint("url_hash", name="uq_download_tracker_url_hash"),
        )

    def _create_duplicates_old(name):
        op.create_table(
            name,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "url_hash",
                sa.String(64),
                sa.ForeignKey("download_tracker.url_hash"),  # NO ACTION
                nullable=False,
            ),
            sa.Column(
                "resource_id",
                sa.Integer(),
                sa.ForeignKey("research_resources.id"),  # NO ACTION
                nullable=False,
            ),
            sa.Column("research_id", sa.String(36), nullable=False),
            sa.Column("added_at", UtcDateTime(), nullable=False),
            sa.UniqueConstraint("url_hash", "resource_id", name="uix_url_resource"),
        )

    def _create_attempts_old(name):
        op.create_table(
            name,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "url_hash",
                sa.String(64),
                sa.ForeignKey("download_tracker.url_hash"),  # NO ACTION
                nullable=False,
            ),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("error_type", sa.String(100), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("attempted_at", UtcDateTime(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("succeeded", sa.Boolean(), nullable=False),
            sa.Column("bytes_downloaded", sa.Integer(), nullable=True),
        )

    if _has_table(bind, "download_attempts"):
        _rebuild_with_preserve(
            bind, "download_attempts", _create_attempts_old,
            "id, url_hash, attempt_number, status_code, error_type, "
            "error_message, attempted_at, duration_ms, succeeded, bytes_downloaded",
        )
        op.create_index("ix_download_attempts_url_hash", "download_attempts", ["url_hash"])
    if _has_table(bind, "download_duplicates"):
        _rebuild_with_preserve(
            bind, "download_duplicates", _create_duplicates_old,
            "id, url_hash, resource_id, research_id, added_at",
        )
        op.create_index("ix_download_duplicates_research_id", "download_duplicates", ["research_id"])
        op.create_index("ix_download_duplicates_url_hash", "download_duplicates", ["url_hash"])
        op.create_index("idx_research_duplicates", "download_duplicates", ["research_id", "url_hash"])
    if _has_table(bind, "download_tracker"):
        _rebuild_with_preserve(
            bind, "download_tracker", _create_tracker_old,
            "id, url, url_hash, first_resource_id, file_hash, file_path, "
            "file_name, file_size, is_downloaded, is_accessible, first_seen, "
            "downloaded_at, last_checked, library_document_id",
        )
        op.create_index("ix_download_tracker_file_hash", "download_tracker", ["file_hash"])
        op.create_index("ix_download_tracker_is_downloaded", "download_tracker", ["is_downloaded"])
        op.create_index("ix_download_tracker_file_name", "download_tracker", ["file_name"])
