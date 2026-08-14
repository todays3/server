"""Add digests.items_json for structured digest payloads.

Revision ID: 0004_digest_items_json
Revises: 0003_insight_questions
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_digest_items_json"
down_revision: str | None = "0003_insight_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("digests") as batch:
        batch.add_column(sa.Column("items_json", sa.Text(), nullable=True))
    op.execute("UPDATE digests SET items_json = '[]' WHERE items_json IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("digests") as batch:
        batch.drop_column("items_json")
