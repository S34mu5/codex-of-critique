from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.repository import Repository


def upsert_repository(session: Session, owner: str, name: str, github_node_id: str | None = None) -> Repository:
    stmt = insert(Repository).values(
        owner=owner,
        name=name,
        github_node_id=github_node_id,
    )
    stmt = stmt.on_duplicate_key_update(
        github_node_id=stmt.inserted.github_node_id,
        updated_at=func.now(),
    )
    session.execute(stmt)
    session.flush()

    return session.query(Repository).filter_by(owner=owner, name=name).one()


def get_or_create_repository(session: Session, owner: str, name: str) -> Repository:
    repo = session.query(Repository).filter_by(owner=owner, name=name).first()
    if repo is not None:
        return repo
    return upsert_repository(session, owner, name)
