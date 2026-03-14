"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("github_node_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner", "name", name="uq_repositories_owner_name"),
    )

    op.create_table(
        "pull_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("github_node_id", sa.String(255), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("author_login", sa.String(255), nullable=True),
        sa.Column("review_decision", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=True),
        sa.Column("created_at_github", sa.DateTime(), nullable=False),
        sa.Column("updated_at_github", sa.DateTime(), nullable=False),
        sa.Column("merged_at_github", sa.DateTime(), nullable=True),
        sa.Column("raw_payload", mysql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_node_id"),
        sa.UniqueConstraint("repository_id", "number", name="uq_pull_requests_repo_number"),
    )
    op.create_index("idx_pull_requests_updated", "pull_requests", ["repository_id", "updated_at_github"])

    op.create_table(
        "review_threads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("github_node_id", sa.String(255), nullable=False),
        sa.Column("path", sa.String(1024), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("original_line", sa.Integer(), nullable=True),
        sa.Column("original_start_line", sa.Integer(), nullable=True),
        sa.Column("diff_side", sa.String(16), nullable=True),
        sa.Column("start_diff_side", sa.String(16), nullable=True),
        sa.Column("is_resolved", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_outdated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("raw_payload", mysql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_node_id"),
    )
    op.create_index("idx_review_threads_pr", "review_threads", ["pull_request_id"])
    op.execute(text("CREATE INDEX idx_review_threads_path ON review_threads (repository_id, path(255))"))

    op.create_table(
        "review_comments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("review_thread_id", sa.BigInteger(), nullable=False),
        sa.Column("github_node_id", sa.String(255), nullable=False),
        sa.Column("github_database_id", sa.BigInteger(), nullable=True),
        sa.Column("review_node_id", sa.String(255), nullable=True),
        sa.Column("review_state", sa.String(64), nullable=True),
        sa.Column("review_author_login", sa.String(255), nullable=True),
        sa.Column("review_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("comment_author_login", sa.String(255), nullable=True),
        sa.Column("author_association", sa.String(64), nullable=True),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("file_extension", sa.String(32), nullable=True),
        sa.Column("body", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("body_text", mysql.MEDIUMTEXT(), nullable=True),
        sa.Column("diff_hunk", mysql.MEDIUMTEXT(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("original_line", sa.Integer(), nullable=True),
        sa.Column("original_start_line", sa.Integer(), nullable=True),
        sa.Column("comment_commit_oid", sa.String(40), nullable=True),
        sa.Column("comment_created_at", sa.DateTime(), nullable=False),
        sa.Column("comment_edited_at", sa.DateTime(), nullable=True),
        sa.Column("raw_payload", mysql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_requests.id"]),
        sa.ForeignKeyConstraint(["review_thread_id"], ["review_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_node_id"),
        sa.UniqueConstraint("github_database_id"),
    )
    op.create_index("idx_review_comments_pr", "review_comments", ["pull_request_id"])
    op.execute(text("CREATE INDEX idx_review_comments_path ON review_comments (repository_id, path(255))"))
    op.create_index("idx_review_comments_extension", "review_comments", ["repository_id", "file_extension"])
    op.create_index("idx_review_comments_author", "review_comments", ["comment_author_login"])
    op.create_index("idx_review_comments_commit", "review_comments", ["comment_commit_oid"])
    op.create_index("idx_review_comments_created", "review_comments", ["comment_created_at"])

    op.create_table(
        "code_authorship",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("review_comment_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("blame_commit_oid", sa.String(40), nullable=True),
        sa.Column("blame_starting_line", sa.Integer(), nullable=True),
        sa.Column("blame_ending_line", sa.Integer(), nullable=True),
        sa.Column("code_author_login", sa.String(255), nullable=True),
        sa.Column("code_author_name", sa.String(255), nullable=True),
        sa.Column("code_author_email", sa.String(255), nullable=True),
        sa.Column("raw_payload", mysql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["review_comment_id"], ["review_comments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_comment_id"),
    )
    op.create_index("idx_code_authorship_author", "code_authorship", ["code_author_login"])
    op.create_index("idx_code_authorship_commit", "code_authorship", ["blame_commit_oid"])

    op.create_table(
        "code_snippets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("review_comment_id", sa.BigInteger(), nullable=False),
        sa.Column("snippet_type", sa.String(32), nullable=False),
        sa.Column("commit_oid", sa.String(40), nullable=True),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("snippet_text", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("raw_payload", mysql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["review_comment_id"], ["review_comments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_code_snippets_review_comment", "code_snippets", ["review_comment_id"])
    op.execute(text("CREATE INDEX idx_code_snippets_commit_path ON code_snippets (commit_oid, path(255))"))

    op.create_table(
        "sync_state",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("sync_name", sa.String(64), nullable=False),
        sa.Column("last_pr_updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("metadata", mysql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "sync_name", name="uq_sync_state_repo_name"),
    )


def downgrade() -> None:
    op.drop_table("sync_state")
    op.drop_table("code_snippets")
    op.drop_table("code_authorship")
    op.drop_table("review_comments")
    op.drop_table("review_threads")
    op.drop_table("pull_requests")
    op.drop_table("repositories")
