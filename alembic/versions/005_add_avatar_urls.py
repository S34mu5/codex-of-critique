"""Add avatar_url columns to user-related tables

Revision ID: 005
Revises: 004
Create Date: 2026-04-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pull_requests", sa.Column("author_avatar_url", sa.String(512), nullable=True))
    op.add_column("review_comments", sa.Column("comment_author_avatar_url", sa.String(512), nullable=True))
    op.add_column("pr_reviews", sa.Column("author_avatar_url", sa.String(512), nullable=True))
    op.add_column("pr_comments", sa.Column("author_avatar_url", sa.String(512), nullable=True))
    op.add_column("review_requests", sa.Column("requested_reviewer_avatar_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("review_requests", "requested_reviewer_avatar_url")
    op.drop_column("pr_comments", "author_avatar_url")
    op.drop_column("pr_reviews", "author_avatar_url")
    op.drop_column("review_comments", "comment_author_avatar_url")
    op.drop_column("pull_requests", "author_avatar_url")
