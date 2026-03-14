from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.pull_request import PullRequest


def upsert_pull_request(
    session: Session,
    repository_id: int,
    github_node_id: str,
    number: int,
    title: str,
    author_login: str | None,
    review_decision: str | None,
    state: str | None,
    created_at_github: datetime,
    updated_at_github: datetime,
    merged_at_github: datetime | None,
    raw_payload: dict[str, Any] | None,
) -> int:
    """Upsert a pull request and return its database id."""
    stmt = insert(PullRequest).values(
        repository_id=repository_id,
        github_node_id=github_node_id,
        number=number,
        title=title,
        author_login=author_login,
        review_decision=review_decision,
        state=state,
        created_at_github=created_at_github,
        updated_at_github=updated_at_github,
        merged_at_github=merged_at_github,
        raw_payload=raw_payload,
    )
    stmt = stmt.on_duplicate_key_update(
        title=stmt.inserted.title,
        author_login=stmt.inserted.author_login,
        review_decision=stmt.inserted.review_decision,
        state=stmt.inserted.state,
        updated_at_github=stmt.inserted.updated_at_github,
        merged_at_github=stmt.inserted.merged_at_github,
        raw_payload=stmt.inserted.raw_payload,
        updated_at=func.now(),
    )
    session.execute(stmt)
    session.flush()

    row = (
        session.query(PullRequest.id)
        .filter_by(repository_id=repository_id, number=number)
        .one()
    )
    return row.id
