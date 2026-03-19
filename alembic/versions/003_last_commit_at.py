"""Add last_commit_at to pull_requests

Revision ID: 003
Revises: 002
Create Date: 2026-03-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pull_requests", sa.Column("last_commit_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("pull_requests", "last_commit_at")
