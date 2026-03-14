from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.review_thread import ReviewThread
from app.models.review_comment import ReviewComment


def upsert_review_thread(
    session: Session,
    repository_id: int,
    pull_request_id: int,
    github_node_id: str,
    path: str | None,
    line: int | None,
    start_line: int | None,
    original_line: int | None,
    original_start_line: int | None,
    diff_side: str | None,
    start_diff_side: str | None,
    is_resolved: bool,
    is_outdated: bool,
    raw_payload: dict[str, Any] | None,
) -> int:
    """Upsert a review thread and return its database id."""
    stmt = insert(ReviewThread).values(
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        github_node_id=github_node_id,
        path=path,
        line=line,
        start_line=start_line,
        original_line=original_line,
        original_start_line=original_start_line,
        diff_side=diff_side,
        start_diff_side=start_diff_side,
        is_resolved=is_resolved,
        is_outdated=is_outdated,
        raw_payload=raw_payload,
    )
    stmt = stmt.on_duplicate_key_update(
        path=stmt.inserted.path,
        line=stmt.inserted.line,
        start_line=stmt.inserted.start_line,
        is_resolved=stmt.inserted.is_resolved,
        is_outdated=stmt.inserted.is_outdated,
        raw_payload=stmt.inserted.raw_payload,
        updated_at=func.now(),
    )
    session.execute(stmt)
    session.flush()

    row = (
        session.query(ReviewThread.id)
        .filter_by(github_node_id=github_node_id)
        .one()
    )
    return row.id


def upsert_review_comment(
    session: Session,
    repository_id: int,
    pull_request_id: int,
    review_thread_id: int,
    github_node_id: str,
    github_database_id: int | None,
    review_node_id: str | None,
    review_state: str | None,
    review_author_login: str | None,
    review_submitted_at: datetime | None,
    comment_author_login: str | None,
    author_association: str | None,
    path: str,
    file_extension: str | None,
    body: str,
    body_text: str | None,
    diff_hunk: str | None,
    line: int | None,
    start_line: int | None,
    original_line: int | None,
    original_start_line: int | None,
    comment_commit_oid: str | None,
    comment_created_at: datetime,
    comment_edited_at: datetime | None,
    raw_payload: dict[str, Any] | None,
) -> int:
    """Upsert a review comment and return its database id."""
    stmt = insert(ReviewComment).values(
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        review_thread_id=review_thread_id,
        github_node_id=github_node_id,
        github_database_id=github_database_id,
        review_node_id=review_node_id,
        review_state=review_state,
        review_author_login=review_author_login,
        review_submitted_at=review_submitted_at,
        comment_author_login=comment_author_login,
        author_association=author_association,
        path=path,
        file_extension=file_extension,
        body=body,
        body_text=body_text,
        diff_hunk=diff_hunk,
        line=line,
        start_line=start_line,
        original_line=original_line,
        original_start_line=original_start_line,
        comment_commit_oid=comment_commit_oid,
        comment_created_at=comment_created_at,
        comment_edited_at=comment_edited_at,
        raw_payload=raw_payload,
    )
    stmt = stmt.on_duplicate_key_update(
        body=stmt.inserted.body,
        body_text=stmt.inserted.body_text,
        diff_hunk=stmt.inserted.diff_hunk,
        line=stmt.inserted.line,
        start_line=stmt.inserted.start_line,
        original_line=stmt.inserted.original_line,
        original_start_line=stmt.inserted.original_start_line,
        comment_commit_oid=stmt.inserted.comment_commit_oid,
        comment_edited_at=stmt.inserted.comment_edited_at,
        review_state=stmt.inserted.review_state,
        review_author_login=stmt.inserted.review_author_login,
        review_submitted_at=stmt.inserted.review_submitted_at,
        raw_payload=stmt.inserted.raw_payload,
        updated_at=func.now(),
    )
    session.execute(stmt)
    session.flush()

    row = (
        session.query(ReviewComment.id)
        .filter_by(github_node_id=github_node_id)
        .one()
    )
    return row.id
