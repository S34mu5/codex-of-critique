from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.review_request import ReviewRequest


def sync_review_requests(
    session: Session,
    repository_id: int,
    pull_request_id: int,
    current_requests: list[dict],
) -> None:
    """Sync review requests for a PR.

    Compares current GitHub requests with DB state:
    - Insert new requests as pending
    - Mark requests no longer present as completed
    """
    # Build set of current reviewer logins/team names from GitHub
    current_logins: set[str] = set()
    current_teams: set[str] = set()
    for req in current_requests:
        reviewer = req.get("requestedReviewer") or {}
        login = reviewer.get("login")
        team = reviewer.get("name")
        if login:
            current_logins.add(login)
        elif team:
            current_teams.add(team)

    # Upsert current requests as pending
    for login in current_logins:
        stmt = insert(ReviewRequest).values(
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            requested_reviewer_login=login,
            requested_team_name=None,
            status="pending",
            completed_at=None,
        )
        stmt = stmt.on_duplicate_key_update(
            status="pending",
            completed_at=None,
            updated_at=func.now(),
        )
        session.execute(stmt)

    for team in current_teams:
        # Teams don't hit the unique constraint on login, so use a select+insert
        existing = (
            session.query(ReviewRequest.id)
            .filter_by(
                pull_request_id=pull_request_id,
                requested_team_name=team,
            )
            .first()
        )
        if existing:
            session.query(ReviewRequest).filter_by(id=existing.id).update(
                {"status": "pending", "completed_at": None}
            )
        else:
            session.execute(
                insert(ReviewRequest).values(
                    repository_id=repository_id,
                    pull_request_id=pull_request_id,
                    requested_reviewer_login=None,
                    requested_team_name=team,
                    status="pending",
                    completed_at=None,
                )
            )

    # Mark old requests as completed
    pending_rows = (
        session.query(ReviewRequest)
        .filter_by(pull_request_id=pull_request_id, status="pending")
        .all()
    )
    now = datetime.utcnow()
    for row in pending_rows:
        if row.requested_reviewer_login and row.requested_reviewer_login not in current_logins:
            row.status = "completed"
            row.completed_at = now
        elif row.requested_team_name and row.requested_team_name not in current_teams:
            row.status = "completed"
            row.completed_at = now

    session.flush()