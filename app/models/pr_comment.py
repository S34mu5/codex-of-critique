from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PrComment(Base):
    __tablename__ = "pr_comments"
    __table_args__ = (
        Index("idx_pr_comments_pr", "pull_request_id"),
        Index("idx_pr_comments_author", "author_login"),
        Index("idx_pr_comments_created", "comment_created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id"), nullable=False
    )
    pull_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id"), nullable=False
    )
    github_node_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    github_database_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    author_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_association: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str | None] = mapped_column(MEDIUMTEXT, nullable=True)
    comment_created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    comment_edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pull_request = relationship("PullRequest", back_populates="pr_comments")