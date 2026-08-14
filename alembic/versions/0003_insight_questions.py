"""Add preferences.insight_questions flag.

Revision ID: 0003_insight_questions
Revises: 0002_send_times
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_insight_questions"
down_revision: str | None = "0002_send_times"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("preferences") as batch:
        batch.add_column(
            sa.Column("insight_questions", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("preferences") as batch:
        batch.drop_column("insight_questions")
