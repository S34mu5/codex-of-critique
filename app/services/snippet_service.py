import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.github_rest import GitHubRESTClient
from app.config import settings
from app.repos.authorship_repo import upsert_code_snippet
from app.utils.snippets import extract_blob_excerpt

logger = logging.getLogger(__name__)


class ContentCache:
    """In-memory cache keyed by (owner, repo, commit_oid, path)."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str, str], str] = {}

    def get(self, owner: str, repo: str, oid: str, path: str) -> str | None:
        return self._store.get((owner, repo, oid, path))

    def put(self, owner: str, repo: str, oid: str, path: str, content: str) -> None:
        self._store[(owner, repo, oid, path)] = content


def persist_diff_hunk(
    session: Session,
    comment_db_id: int,
    commit_oid: str | None,
    path: str,
    diff_hunk: str | None,
) -> None:
    if not diff_hunk:
        return

    upsert_code_snippet(
        session=session,
        review_comment_id=comment_db_id,
        snippet_type="diff_hunk",
        commit_oid=commit_oid,
        path=path,
        start_line=None,
        end_line=None,
        snippet_text=diff_hunk,
    )


def fetch_and_persist_blob_excerpt(
    session: Session,
    rest: GitHubRESTClient,
    cache: ContentCache,
    owner: str,
    repo: str,
    comment_db_id: int,
    commit_oid: str | None,
    path: str | None,
    line: int | None,
    start_line: int | None = None,
) -> None:
    if not commit_oid or not path or not line:
        return

    file_text = cache.get(owner, repo, commit_oid, path)
    if file_text is None:
        try:
            file_text = rest.get_file_contents(owner, repo, path, ref=commit_oid)
            cache.put(owner, repo, commit_oid, path, file_text)
        except Exception:
            logger.exception(
                "blob_fetch_error",
                extra={"comment_id": comment_db_id, "oid": commit_oid, "path": path},
            )
            return

    excerpt = extract_blob_excerpt(
        file_text,
        target_line=line,
        context_lines=settings.snippet_context_lines,
        start_line=start_line,
    )

    upsert_code_snippet(
        session=session,
        review_comment_id=comment_db_id,
        snippet_type="blob_excerpt",
        commit_oid=commit_oid,
        path=path,
        start_line=excerpt["start_line"],
        end_line=excerpt["end_line"],
        snippet_text=excerpt["snippet_text"],
    )
