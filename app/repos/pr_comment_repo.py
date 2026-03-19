from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.pr_comment import PrComment


def upsert_pr_comment(
    session: Session,
    repository_id: int,
    pull_request_id: int,
    github_node_id: str,
    github_database_id: int | None,
    author_login: str | None,
    author_association: str | None,
    body: str | None,
    comment_created_at: datetime,
    comment_edited_at: datetime | None,
) -> int:
    """Upsert a PR comment and return its database id."""
    stmt = insert(PrComment).values(
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        github_node_id=github_node_id,
        github_database_id=github_database_id,
        author_login=author_login,
        author_association=author_association,
        body=body,
        comment_created_at=comment_created_at,
        comment_edited_at=comment_edited_at,
    )
    stmt = stmt.on_duplicate_key_update(
        author_login=stmt.inserted.author_login,
        author_association=stmt.inserted.author_association,
        body=stmt.inserted.body,
        comment_edited_at=stmt.inserted.comment_edited_at,
        updated_at=func.now(),
    )
    session.execute(stmt)
    session.flush()

    row = (
        session.query(PrComment.id)
        .filter_by(github_node_id=github_node_id)
        .one()
    )
    return row.id