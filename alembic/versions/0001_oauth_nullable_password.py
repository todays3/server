"""Allow null password_hash for Kakao-only OAuth users.

Revision ID: 0001_oauth_nullable_password
Revises:
Create Date: 2026-08-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_oauth_nullable_password"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite: batch alter to loosen NOT NULL on password_hash
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "password_hash",
            existing_type=sa.String(length=255),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "password_hash",
            existing_type=sa.String(length=255),
            nullable=False,
        )
