from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.code_authorship import CodeAuthorship
from app.models.code_snippet import CodeSnippet


def upsert_code_authorship(
    session: Session,
    review_comment_id: int,
    method: str,
    blame_commit_oid: str | None,
    blame_starting_line: int | None,
    blame_ending_line: int | None,
    code_author_login: str | None,
    code_author_name: str | None,
    code_author_email: str | None,
    raw_payload: dict[str, Any] | None,
) -> None:
    stmt = insert(CodeAuthorship).values(
        review_comment_id=review_comment_id,
        method=method,
        blame_commit_oid=blame_commit_oid,
        blame_starting_line=blame_starting_line,
        blame_ending_line=blame_ending_line,
        code_author_login=code_author_login,
        code_author_name=code_author_name,
        code_author_email=code_author_email,
        raw_payload=raw_payload,
    )
    stmt = stmt.on_duplicate_key_update(
        method=stmt.inserted.method,
        blame_commit_oid=stmt.inserted.blame_commit_oid,
        blame_starting_line=stmt.inserted.blame_starting_line,
        blame_ending_line=stmt.inserted.blame_ending_line,
        code_author_login=stmt.inserted.code_author_login,
        code_author_name=stmt.inserted.code_author_name,
        code_author_email=stmt.inserted.code_author_email,
        raw_payload=stmt.inserted.raw_payload,
        updated_at=func.now(),
    )
    session.execute(stmt)


def upsert_code_snippet(
    session: Session,
    review_comment_id: int,
    snippet_type: str,
    commit_oid: str | None,
    path: str,
    start_line: int | None,
    end_line: int | None,
    snippet_text: str,
    raw_payload: dict[str, Any] | None = None,
) -> None:
    existing = (
        session.query(CodeSnippet)
        .filter_by(review_comment_id=review_comment_id, snippet_type=snippet_type)
        .first()
    )
    if existing:
        existing.commit_oid = commit_oid
        existing.path = path
        existing.start_line = start_line
        existing.end_line = end_line
        existing.snippet_text = snippet_text
        existing.raw_payload = raw_payload
    else:
        snippet = CodeSnippet(
            review_comment_id=review_comment_id,
            snippet_type=snippet_type,
            commit_oid=commit_oid,
            path=path,
            start_line=start_line,
            end_line=end_line,
            snippet_text=snippet_text,
            raw_payload=raw_payload,
        )
        session.add(snippet)
    session.flush()
