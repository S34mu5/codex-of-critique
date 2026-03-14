from datetime import datetime

from sqlalchemy.orm import Session

from app.models.sync_state import SyncState


def get_sync_state(session: Session, repository_id: int, sync_name: str = "pr_scan") -> SyncState:
    state = (
        session.query(SyncState)
        .filter_by(repository_id=repository_id, sync_name=sync_name)
        .first()
    )
    if state is None:
        state = SyncState(
            repository_id=repository_id,
            sync_name=sync_name,
        )
        session.add(state)
        session.flush()
    return state


def advance_cursor(
    session: Session,
    state: SyncState,
    last_pr_updated_at: datetime,
) -> None:
    state.last_pr_updated_at = last_pr_updated_at
    state.last_success_at = datetime.utcnow()
    state.last_error_at = None
    state.last_error_message = None
    session.flush()


def record_error(session: Session, state: SyncState, message: str) -> None:
    state.last_error_at = datetime.utcnow()
    state.last_error_message = message[:65535]
    session.flush()
