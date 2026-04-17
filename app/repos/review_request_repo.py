from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.models.pr_review import PrReview
from app.models.review_request import ReviewRequest


def _get_approved_logins(session: Session, pull_request_id: int) -> set[str]:
    """Return logins whose latest review on this PR is APPROVED."""
    from sqlalchemy import desc

    latest_reviews = (
        session.query(
            PrReview.author_login,
            PrReview.state,
        )
        .filter(
            PrReview.pull_request_id == pull_request_id,
            PrReview.author_login.isnot(None),
            PrReview.state.in_(["APPROVED", "CHANGES_REQUESTED", "DISMISSED"]),
        )
        .order_by(PrReview.author_login, desc(PrReview.submitted_at))
        .all()
    )

    seen: set[str] = set()
    approved: set[str] = set()
    for login, state in latest_reviews:
        if login not in seen:
            seen.add(login)
            if state == "APPROVED":
                approved.add(login)
    return approved


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
    - Mark requests as completed if reviewer's latest review is APPROVED
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

    avatar_map: dict[str, str | None] = {}
    for req in current_requests:
        reviewer = req.get("requestedReviewer") or {}
        login = reviewer.get("login")
        if login:
            avatar_map[login] = reviewer.get("avatarUrl")

    # Upsert current requests as pending
    for login in current_logins:
        stmt = insert(ReviewRequest).values(
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            requested_reviewer_login=login,
            requested_team_name=None,
            requested_reviewer_avatar_url=avatar_map.get(login),
            status="pending",
            completed_at=None,
        )
        stmt = stmt.on_duplicate_key_update(
            status="pending",
            completed_at=None,
            requested_reviewer_avatar_url=avatar_map.get(login),
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
    approved_logins = _get_approved_logins(session, pull_request_id)

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
        elif row.requested_reviewer_login and row.requested_reviewer_login in approved_logins:
            row.status = "completed"
            row.completed_at = now
        elif row.requested_team_name and row.requested_team_name not in current_teams:
            row.status = "completed"
            row.completed_at = now

    session.flush()