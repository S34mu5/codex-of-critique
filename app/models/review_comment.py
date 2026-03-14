from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import JSON, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ReviewComment(Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        Index("idx_review_comments_pr", "pull_request_id"),
        Index("idx_review_comments_path", "repository_id", "path"),
        Index("idx_review_comments_extension", "repository_id", "file_extension"),
        Index("idx_review_comments_author", "comment_author_login"),
        Index("idx_review_comments_commit", "comment_commit_oid"),
        Index("idx_review_comments_created", "comment_created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id"), nullable=False
    )
    pull_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id"), nullable=False
    )
    review_thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("review_threads.id"), nullable=False
    )

    github_node_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    github_database_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )

    review_node_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_author_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    comment_author_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_association: Mapped[str | None] = mapped_column(String(64), nullable=True)

    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_extension: Mapped[str | None] = mapped_column(String(32), nullable=True)

    body: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    body_text: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)
    diff_hunk: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)

    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)

    comment_commit_oid: Mapped[str | None] = mapped_column(String(40), nullable=True)

    comment_created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    comment_edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pull_request = relationship("PullRequest", back_populates="review_comments")
    review_thread = relationship("ReviewThread", back_populates="comments")
    code_authorship = relationship(
        "CodeAuthorship", back_populates="review_comment", uselist=False
    )
    code_snippets = relationship("CodeSnippet", back_populates="review_comment")
