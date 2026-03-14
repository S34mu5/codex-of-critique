from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CodeAuthorship(Base):
    __tablename__ = "code_authorship"
    __table_args__ = (
        Index("idx_code_authorship_author", "code_author_login"),
        Index("idx_code_authorship_commit", "blame_commit_oid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_comment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("review_comments.id"), nullable=False, unique=True
    )
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    blame_commit_oid: Mapped[str | None] = mapped_column(String(40), nullable=True)
    blame_starting_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blame_ending_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code_author_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code_author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code_author_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    review_comment = relationship("ReviewComment", back_populates="code_authorship")
