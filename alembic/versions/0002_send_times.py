"""Add preferences.send_times for multiple Seoul send slots.

Revision ID: 0002_send_times
Revises: 0001_oauth_nullable_password
Create Date: 2026-08-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_send_times"
down_revision: Union[str, None] = "0001_oauth_nullable_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("preferences") as batch:
        batch.add_column(sa.Column("send_times", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE preferences SET send_times = "
            "printf('%02d:%02d', COALESCE(send_hour, 7), COALESCE(send_minute, 30)) "
            "WHERE send_times IS NULL OR send_times = ''"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("preferences") as batch:
        batch.drop_column("send_times")
