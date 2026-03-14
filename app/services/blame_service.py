import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient
from app.repos.authorship_repo import upsert_code_authorship

logger = logging.getLogger(__name__)


class BlameCache:
    """In-memory cache keyed by (owner, repo, commit_oid, path)."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}

    def get(self, owner: str, repo: str, oid: str, path: str) -> list[dict[str, Any]] | None:
        return self._store.get((owner, repo, oid, path))

    def put(self, owner: str, repo: str, oid: str, path: str, ranges: list[dict[str, Any]]) -> None:
        self._store[(owner, repo, oid, path)] = ranges

    @property
    def size(self) -> int:
        return len(self._store)


def _fetch_blame_ranges(
    gql: GitHubGraphQLClient,
    owner: str,
    repo: str,
    commit_oid: str,
    path: str,
    cache: BlameCache,
) -> list[dict[str, Any]]:
    cached = cache.get(owner, repo, commit_oid, path)
    if cached is not None:
        return cached

    data = gql.execute_query_file(
        "blame_for_file",
        {"owner": owner, "name": repo, "expr": commit_oid, "path": path},
    )

    obj = data.get("repository", {}).get("object")
    if not obj or "blame" not in obj:
        cache.put(owner, repo, commit_oid, path, [])
        return []

    ranges = obj["blame"]["ranges"]
    cache.put(owner, repo, commit_oid, path, ranges)
    return ranges


def _find_author_for_line(ranges: list[dict[str, Any]], line: int) -> dict[str, Any] | None:
    for r in ranges:
        if r["startingLine"] <= line <= r["endingLine"]:
            commit = r.get("commit", {})
            author = commit.get("author") or {}
            user = author.get("user") or {}
            return {
                "method": "graphql_blame",
                "blame_commit_oid": commit.get("oid"),
                "blame_starting_line": r["startingLine"],
                "blame_ending_line": r["endingLine"],
                "code_author_login": user.get("login"),
                "code_author_name": author.get("name"),
                "code_author_email": author.get("email"),
                "raw_payload": r,
            }
    return None


def resolve_and_persist_blame(
    session: Session,
    gql: GitHubGraphQLClient,
    owner: str,
    repo: str,
    cache: BlameCache,
    comment_db_id: int,
    commit_oid: str | None,
    path: str | None,
    line: int | None,
) -> None:
    if not commit_oid or not path or not line:
        logger.debug("blame_skip", extra={"comment_id": comment_db_id, "reason": "missing commit/path/line"})
        return

    try:
        ranges = _fetch_blame_ranges(gql, owner, repo, commit_oid, path, cache)
    except Exception:
        logger.exception("blame_fetch_error", extra={"comment_id": comment_db_id, "oid": commit_oid, "path": path})
        return

    result = _find_author_for_line(ranges, line)
    if result is None:
        logger.warning("blame_no_match", extra={"comment_id": comment_db_id, "line": line, "ranges_count": len(ranges)})
        return

    upsert_code_authorship(
        session=session,
        review_comment_id=comment_db_id,
        **result,
    )
