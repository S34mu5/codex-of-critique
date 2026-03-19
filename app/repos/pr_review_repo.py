from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.pr_review import PrReview


def upsert_pr_review(
    session: Session,
    repository_id: int,
    pull_request_id: int,
    github_node_id: str,
    author_login: str | None,
    state: str | None,
    body: str | None,
    submitted_at: datetime | None,
) -> int:
    """Upsert a PR review and return its database id."""
    stmt = insert(PrReview).values(
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        github_node_id=github_node_id,
        author_login=author_login,
        state=state,
        body=body,
        submitted_at=submitted_at,
    )
    stmt = stmt.on_duplicate_key_update(
        author_login=stmt.inserted.author_login,
        state=stmt.inserted.state,
        body=stmt.inserted.body,
        submitted_at=stmt.inserted.submitted_at,
        updated_at=func.now(),
    )
    session.execute(stmt)
    session.flush()

    row = (
        session.query(PrReview.id)
        .filter_by(github_node_id=github_node_id)
        .one()
    )
    return row.id