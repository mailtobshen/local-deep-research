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
        sa.Column(
            "research_id",
            sa.String(length=36),
            sa.ForeignKey("research_history.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("local_route", sa.Text(), nullable=False),
        sa.Column("alt", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True, index=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "search_results", sa.Column("html_content", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("search_results", "html_content")
    op.drop_table("research_images")
