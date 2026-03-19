"""Activity tables: pr_reviews, pr_comments, review_requests

Revision ID: 002
Revises: 001
Create Date: 2026-03-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pr_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("github_node_id", sa.String(255), nullable=False),
        sa.Column("author_login", sa.String(255), nullable=True),
        sa.Column("state", sa.String(64), nullable=True),
        sa.Column("body", mysql.MEDIUMTEXT(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_node_id"),
    )
    op.create_index("idx_pr_reviews_pr", "pr_reviews", ["pull_request_id"])
    op.create_index("idx_pr_reviews_author", "pr_reviews", ["author_login"])
    op.create_index("idx_pr_reviews_state", "pr_reviews", ["state"])

    op.create_table(
        "pr_comments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("github_node_id", sa.String(255), nullable=False),
        sa.Column("github_database_id", sa.BigInteger(), nullable=True),
        sa.Column("author_login", sa.String(255), nullable=True),
        sa.Column("author_association", sa.String(64), nullable=True),
        sa.Column("body", mysql.MEDIUMTEXT(), nullable=True),
        sa.Column("comment_created_at", sa.DateTime(), nullable=False),
        sa.Column("comment_edited_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_node_id"),
        sa.UniqueConstraint("github_database_id"),
    )
    op.create_index("idx_pr_comments_pr", "pr_comments", ["pull_request_id"])
    op.create_index("idx_pr_comments_author", "pr_comments", ["author_login"])
    op.create_index("idx_pr_comments_created", "pr_comments", ["comment_created_at"])

    op.create_table(
        "review_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_reviewer_login", sa.String(255), nullable=True),
        sa.Column("requested_team_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pull_request_id", "requested_reviewer_login",
            name="uq_review_requests_pr_reviewer",
        ),
    )
    op.create_index("idx_review_requests_reviewer", "review_requests", ["requested_reviewer_login"])
    op.create_index("idx_review_requests_status", "review_requests", ["status"])


def downgrade() -> None:
    op.drop_table("review_requests")
    op.drop_table("pr_comments")
    op.drop_table("pr_reviews")