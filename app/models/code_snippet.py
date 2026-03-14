from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.mysql import JSON, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CodeSnippet(Base):
    __tablename__ = "code_snippets"
    __table_args__ = (
        Index("idx_code_snippets_review_comment", "review_comment_id"),
        Index("idx_code_snippets_commit_path", "commit_oid", "path"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_comment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("review_comments.id"), nullable=False
    )
    snippet_type: Mapped[str] = mapped_column(String(32), nullable=False)
    commit_oid: Mapped[str | None] = mapped_column(String(40), nullable=True)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snippet_text: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    review_comment = relationship("ReviewComment", back_populates="code_snippets")
