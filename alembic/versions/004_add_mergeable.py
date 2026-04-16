"""Add mergeable columns to pull_requests

Revision ID: 004
Revises: 003
Create Date: 2026-04-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pull_requests", sa.Column("mergeable", sa.String(32), nullable=True))
    op.add_column("pull_requests", sa.Column("mergeable_updated_at", sa.DateTime(), nullable=True))
    op.create_index("idx_pull_requests_state_mergeable", "pull_requests", ["state", "mergeable"])


def downgrade() -> None:
    op.drop_index("idx_pull_requests_state_mergeable", table_name="pull_requests")
    op.drop_column("pull_requests", "mergeable_updated_at")
    op.drop_column("pull_requests", "mergeable")
