from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ReviewRequest(Base):
    __tablename__ = "review_requests"
    __table_args__ = (
        UniqueConstraint(
            "pull_request_id",
            "requested_reviewer_login",
            name="uq_review_requests_pr_reviewer",
        ),
        Index("idx_review_requests_reviewer", "requested_reviewer_login"),
        Index("idx_review_requests_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id"), nullable=False
    )
    pull_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pull_requests.id"), nullable=False
    )
    requested_reviewer_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pull_request = relationship("PullRequest", back_populates="review_requests")