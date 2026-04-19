"""Add dashboard_settings table

Revision ID: 006
Revises: 005
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dashboard_settings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(255), unique=True, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("dashboard_settings")
