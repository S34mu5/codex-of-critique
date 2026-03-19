from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_pull_requests_repo_number"),
        Index("idx_pull_requests_updated", "repository_id", "updated_at_github"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id"), nullable=False
    )
    github_node_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    author_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    review_decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at_github: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at_github: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    merged_at_github: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    repository = relationship("Repository", back_populates="pull_requests")
    review_threads = relationship("ReviewThread", back_populates="pull_request")
    review_comments = relationship("ReviewComment", back_populates="pull_request")
    pr_reviews = relationship("PrReview", back_populates="pull_request")
    pr_comments = relationship("PrComment", back_populates="pull_request")
    review_requests = relationship("ReviewRequest", back_populates="pull_request")
