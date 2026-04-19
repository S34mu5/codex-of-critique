"""MCP server for Codex of Critique — exposes code review data to LLM clients."""

import json
from datetime import datetime
from typing import List, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy import text

from app.db import SessionLocal

mcp = FastMCP(
    "codex-of-critique",
    instructions=(
        "You have access to a database of GitHub pull request review comments enriched with "
        "code snippets and authorship information. Use the search_reviews tool to find past "
        "review comments by keyword, reviewer, repository, file path, or PR author. Results "
        "include the comment body, diff hunk, associated code snippets, and PR metadata."
    ),
)


def _get_setting(key: str) -> Optional[str]:
    """Read a value from the dashboard_settings table by key. Returns None if not found."""
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT value FROM dashboard_settings WHERE `key` = :key"),
            {"key": key},
        ).fetchone()
        return row[0] if row else None


def _get_mcp_default_reviewers() -> List[str]:
    """Return the MCP default reviewers list.

    Falls back to the required_reviewers setting if mcp_default_reviewers is not set.
    Returns an empty list if neither setting exists.
    """
    raw = _get_setting("mcp_default_reviewers")
    if raw is None:
        raw = _get_setting("required_reviewers")
    if raw is None:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _handle_search_reviews(
    comment_q: Optional[str] = None,
    snippet_q: Optional[str] = None,
    file_path: Optional[str] = None,
    reviewers: Optional[List[str]] = None,
    repositories: Optional[List[str]] = None,
    exclude_repositories: Optional[List[str]] = None,
    pr_author: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Internal handler — builds SQL conditions and returns search results."""
    if reviewers is None:
        reviewers = _get_mcp_default_reviewers()

    conditions: List[str] = []
    params: dict = {}

    # Comment text search
    if comment_q:
        conditions.append("rc.body LIKE :comment_q")
        params["comment_q"] = f"%{comment_q}%"

    # Snippet text search
    if snippet_q:
        conditions.append(
            "EXISTS ("
            "SELECT 1 FROM code_snippets cs2 "
            "WHERE cs2.review_comment_id = rc.id "
            "AND cs2.snippet_text LIKE :snippet_q"
            ")"
        )
        params["snippet_q"] = f"%{snippet_q}%"

    # File path filter
    if file_path:
        conditions.append("rc.path LIKE :file_path")
        params["file_path"] = f"%{file_path}%"

    # Reviewer (comment author) filter
    if reviewers:
        reviewer_clauses: List[str] = []
        for idx, login in enumerate(reviewers):
            key = f"reviewer_{idx}"
            params[key] = login
            reviewer_clauses.append(f"rc.comment_author_login = :{key}")
        conditions.append("(" + " OR ".join(reviewer_clauses) + ")")

    # Repository include filter (list of "owner/repo" strings)
    if repositories:
        include_clauses: List[str] = []
        for idx, repo_id in enumerate(repositories):
            parts = repo_id.split("/", 1)
            if len(parts) != 2:
                continue
            owner_key = f"repo_include_owner_{idx}"
            name_key = f"repo_include_name_{idx}"
            params[owner_key] = parts[0].strip()
            params[name_key] = parts[1].strip()
            include_clauses.append(
                f"(rp.owner = :{owner_key} AND rp.name = :{name_key})"
            )
        if include_clauses:
            conditions.append("(" + " OR ".join(include_clauses) + ")")

    # Repository exclude filter (list of "owner/repo" strings)
    if exclude_repositories:
        exclude_clauses: List[str] = []
        for idx, repo_id in enumerate(exclude_repositories):
            parts = repo_id.split("/", 1)
            if len(parts) != 2:
                continue
            owner_key = f"repo_exclude_owner_{idx}"
            name_key = f"repo_exclude_name_{idx}"
            params[owner_key] = parts[0].strip()
            params[name_key] = parts[1].strip()
            exclude_clauses.append(
                f"(rp.owner = :{owner_key} AND rp.name = :{name_key})"
            )
        if exclude_clauses:
            conditions.append("NOT (" + " OR ".join(exclude_clauses) + ")")

    # PR author filter
    if pr_author:
        conditions.append("pr.author_login = :pr_author")
        params["pr_author"] = pr_author

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    with SessionLocal() as session:
        count_sql = f"""
            SELECT COUNT(*) FROM review_comments rc
            JOIN pull_requests pr ON pr.id = rc.pull_request_id
            JOIN repositories rp ON rp.id = rc.repository_id
            {where}
        """
        total = session.execute(text(count_sql), params).scalar()

        data_sql = f"""
            SELECT rc.id, rc.github_node_id, rc.path, rc.file_extension,
                   rc.comment_author_login, rc.comment_author_avatar_url, rc.body, rc.diff_hunk,
                   rc.line, rc.start_line, rc.comment_created_at, rc.comment_commit_oid,
                   pr.number AS pr_number, pr.title AS pr_title,
                   pr.author_login AS pr_author, pr.author_avatar_url AS pr_author_avatar_url,
                   rp.name AS repo_name, rp.owner AS repo_owner
            FROM review_comments rc
            JOIN pull_requests pr ON pr.id = rc.pull_request_id
            JOIN repositories rp ON rp.id = rc.repository_id
            {where}
            ORDER BY rc.comment_created_at DESC
            LIMIT :limit OFFSET :offset
        """
        rows = session.execute(text(data_sql), params).fetchall()

        results = []
        row_dicts = [dict(row._mapping) for row in rows]
        comment_ids = [r["id"] for r in row_dicts]

        snippets_by_comment: dict = {}
        if comment_ids:
            placeholders = ",".join(f":cid_{i}" for i in range(len(comment_ids)))
            snippet_params = {f"cid_{i}": cid for i, cid in enumerate(comment_ids)}
            snippet_rows = session.execute(
                text(
                    f"SELECT review_comment_id, snippet_type, snippet_text, start_line, end_line "
                    f"FROM code_snippets WHERE review_comment_id IN ({placeholders})"
                ),
                snippet_params,
            ).fetchall()
            for s in snippet_rows:
                sm = dict(s._mapping)
                cid = sm.pop("review_comment_id")
                snippets_by_comment.setdefault(cid, []).append(sm)

        for r in row_dicts:
            r["snippets"] = snippets_by_comment.get(r["id"], [])
            for k, v in r.items():
                if isinstance(v, datetime):
                    r[k] = v.isoformat()
            results.append(r)

    return {"total": total, "page": page, "per_page": per_page, "results": results}


def _build_repo_where(
    repositories: Optional[List[str]],
    exclude_repositories: Optional[List[str]],
    params: dict,
    prefix: str = " AND ",
) -> str:
    """Build a repo include/exclude WHERE fragment (with leading prefix if non-empty)."""
    clauses: List[str] = []

    if repositories:
        include_clauses: List[str] = []
        for idx, repo_id in enumerate(repositories):
            parts = repo_id.split("/", 1)
            if len(parts) != 2:
                continue
            owner_key = f"repo_include_owner_{idx}"
            name_key = f"repo_include_name_{idx}"
            params[owner_key] = parts[0].strip()
            params[name_key] = parts[1].strip()
            include_clauses.append(f"(rp.owner = :{owner_key} AND rp.name = :{name_key})")
        if include_clauses:
            clauses.append("(" + " OR ".join(include_clauses) + ")")

    if exclude_repositories:
        exclude_clauses: List[str] = []
        for idx, repo_id in enumerate(exclude_repositories):
            parts = repo_id.split("/", 1)
            if len(parts) != 2:
                continue
            owner_key = f"repo_exclude_owner_{idx}"
            name_key = f"repo_exclude_name_{idx}"
            params[owner_key] = parts[0].strip()
            params[name_key] = parts[1].strip()
            exclude_clauses.append(f"(rp.owner = :{owner_key} AND rp.name = :{name_key})")
        if exclude_clauses:
            clauses.append("NOT (" + " OR ".join(exclude_clauses) + ")")

    if not clauses:
        return ""
    return prefix + " AND ".join(clauses)


def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy row to a plain dict, serializing datetimes to ISO strings."""
    d = dict(row._mapping)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _handle_get_activity(
    username: Optional[str] = None,
    category: Optional[str] = None,
    repositories: Optional[List[str]] = None,
    exclude_repositories: Optional[List[str]] = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Internal handler — queries activity sections from the DB."""
    username = username or ""
    offset = (page - 1) * per_page

    all_categories = ["pending_reviews", "changes_not_addressed", "changes_merged", "comments"]
    requested = [category] if category else all_categories

    result: dict = {"page": page, "per_page": per_page}

    for cat in requested:
        params: dict = {"username": username, "limit": per_page, "offset": offset}
        repo_where = _build_repo_where(repositories, exclude_repositories, params, prefix=" AND ")

        if cat == "pending_reviews":
            sql = f"""
                SELECT pr.number AS pr_number, pr.title AS pr_title,
                       pr.updated_at_github, pr.author_login AS pr_author,
                       pr.author_avatar_url AS pr_author_avatar_url,
                       rp.name AS repo_name, rp.owner AS repo_owner,
                       rr.requested_reviewer_login, rr.requested_reviewer_avatar_url,
                       rr.created_at AS requested_at
                FROM review_requests rr
                JOIN pull_requests pr ON pr.id = rr.pull_request_id
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'OPEN' AND rr.status = 'pending'
                  AND (:username = '' OR rr.requested_reviewer_login = :username)
                  {repo_where}
                ORDER BY pr.updated_at_github DESC
                LIMIT :limit OFFSET :offset
            """
        elif cat == "changes_not_addressed":
            sql = f"""
                SELECT DISTINCT pr.number AS pr_number, pr.title AS pr_title,
                       pr.updated_at_github,
                       pr.author_login AS pr_author, pr.author_avatar_url AS pr_author_avatar_url,
                       rp.name AS repo_name, rp.owner AS repo_owner,
                       rev.author_login AS reviewer, rev.author_avatar_url AS reviewer_avatar_url,
                       rev.submitted_at AS review_date
                FROM pr_reviews rev
                JOIN pull_requests pr ON pr.id = rev.pull_request_id
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'OPEN'
                  AND (:username = '' OR pr.author_login = :username)
                  AND rev.state = 'CHANGES_REQUESTED'
                  {repo_where}
                  AND rev.submitted_at = (
                    SELECT MAX(r2.submitted_at) FROM pr_reviews r2
                    WHERE r2.pull_request_id = rev.pull_request_id AND r2.author_login = rev.author_login
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM pr_reviews r3
                    WHERE r3.pull_request_id = rev.pull_request_id AND r3.author_login = rev.author_login
                      AND r3.state = 'APPROVED' AND r3.submitted_at > rev.submitted_at
                  )
                ORDER BY rev.submitted_at DESC
                LIMIT :limit OFFSET :offset
            """
        elif cat == "changes_merged":
            sql = f"""
                SELECT pr.number AS pr_number, pr.title AS pr_title,
                       pr.author_login AS pr_author, pr.author_avatar_url AS pr_author_avatar_url,
                       rp.name AS repo_name, rp.owner AS repo_owner,
                       pr.merged_at_github AS merged_at
                FROM pull_requests pr
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'MERGED'
                  AND (:username = '' OR pr.author_login = :username)
                  {repo_where}
                ORDER BY pr.merged_at_github DESC
                LIMIT :limit OFFSET :offset
            """
        elif cat == "comments":
            sql = f"""
                SELECT rc.body, rc.comment_author_login, rc.comment_author_avatar_url,
                       rc.path, rc.comment_created_at,
                       pr.number AS pr_number, pr.title AS pr_title,
                       pr.author_login AS pr_author, pr.author_avatar_url AS pr_author_avatar_url,
                       rp.name AS repo_name, rp.owner AS repo_owner
                FROM review_comments rc
                JOIN pull_requests pr ON pr.id = rc.pull_request_id
                JOIN repositories rp ON rp.id = rc.repository_id
                WHERE (:username = '' OR rc.comment_author_login = :username)
                  {repo_where}
                ORDER BY rc.comment_created_at DESC
                LIMIT :limit OFFSET :offset
            """
        else:
            continue

        with SessionLocal() as session:
            rows = session.execute(text(sql), params).fetchall()
            result[cat] = [_row_to_dict(r) for r in rows]

    return result


def _handle_get_stats() -> dict:
    """Internal handler — returns table counts and latest sync info."""
    with SessionLocal() as session:
        counts = {}
        for table in ["repositories", "pull_requests", "review_comments", "code_snippets", "code_authorship"]:
            counts[table] = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        sync_row = session.execute(text(
            "SELECT last_success_at, last_error_at, last_error_message FROM sync_state ORDER BY last_success_at DESC LIMIT 1"
        )).fetchone()
        sync_info = {}
        if sync_row:
            sync_info["last_success_at"] = sync_row[0].isoformat() if sync_row[0] else None
            sync_info["last_error_at"] = sync_row[1].isoformat() if sync_row[1] else None
            sync_info["last_error_message"] = sync_row[2]
    return {"counts": counts, "sync": sync_info}


def _handle_get_filters() -> dict:
    """Internal handler — returns available filter values (repos, authors, reviewers)."""
    with SessionLocal() as session:
        repos = [
            f"{r.owner}/{r.name}"
            for r in session.execute(
                text("SELECT DISTINCT owner, name FROM repositories ORDER BY owner, name")
            ).fetchall()
        ]
        pr_authors = [
            {"login": r.author_login, "avatar_url": r.author_avatar_url}
            for r in session.execute(text("""
                SELECT author_login, author_avatar_url
                FROM (
                    SELECT author_login, author_avatar_url,
                           ROW_NUMBER() OVER (PARTITION BY author_login ORDER BY updated_at_github DESC) AS rn
                    FROM pull_requests WHERE author_login IS NOT NULL
                ) ranked WHERE rn = 1 ORDER BY author_login
            """)).fetchall()
        ]
        reviewers = [
            {"login": r.comment_author_login, "avatar_url": r.comment_author_avatar_url}
            for r in session.execute(text("""
                SELECT comment_author_login, comment_author_avatar_url
                FROM (
                    SELECT comment_author_login, comment_author_avatar_url,
                           ROW_NUMBER() OVER (PARTITION BY comment_author_login ORDER BY comment_created_at DESC) AS rn
                    FROM review_comments WHERE comment_author_login IS NOT NULL
                ) ranked WHERE rn = 1 ORDER BY comment_author_login
            """)).fetchall()
        ]
    return {"repositories": repos, "pr_authors": pr_authors, "reviewers": reviewers}


@mcp.tool()
def get_activity(
    username=None,
    category=None,
    repositories=None,
    exclude_repositories=None,
    page=1,
    per_page=20,
) -> str:
    """Get PR activity status — pending reviews, unaddressed changes, merge conflicts, etc.
    Use this to understand the current state of code reviews."""
    result = _handle_get_activity(
        username=username,
        category=category,
        repositories=repositories,
        exclude_repositories=exclude_repositories,
        page=page,
        per_page=per_page,
    )
    return json.dumps(result)


@mcp.tool()
def get_stats() -> str:
    """Get database statistics — row counts per table and latest sync info.
    Use this to understand how much data has been ingested and when it was last synced."""
    return json.dumps(_handle_get_stats())


@mcp.tool()
def get_filters() -> str:
    """Get available filter values — repositories, PR authors, and reviewers present in the DB.
    Use this to discover valid values before calling search_reviews or get_activity."""
    return json.dumps(_handle_get_filters())


@mcp.tool()
def search_reviews(
    comment_q: Optional[str] = None,
    snippet_q: Optional[str] = None,
    file_path: Optional[str] = None,
    reviewers: Optional[List[str]] = None,
    repositories: Optional[List[str]] = None,
    exclude_repositories: Optional[List[str]] = None,
    pr_author: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> str:
    """Search past code review comments stored in the Codex of Critique database.

    Args:
        comment_q: Keyword to search within comment bodies (LIKE match).
        snippet_q: Keyword to search within associated code snippets (LIKE match).
        file_path: Partial file path filter (LIKE match on rc.path).
        reviewers: List of reviewer GitHub logins to filter by. Defaults to configured
            default reviewers when not provided.
        repositories: List of repositories to include, formatted as "owner/repo".
        exclude_repositories: List of repositories to exclude, formatted as "owner/repo".
        pr_author: Filter by PR author GitHub login (exact match).
        page: Page number for pagination (1-based, default 1).
        per_page: Number of results per page (default 20, max 100).

    Returns:
        JSON string with keys: total, page, per_page, results.
        Each result contains the review comment, PR metadata, repository info, and snippets.
    """
    result = _handle_search_reviews(
        comment_q=comment_q,
        snippet_q=snippet_q,
        file_path=file_path,
        reviewers=reviewers,
        repositories=repositories,
        exclude_repositories=exclude_repositories,
        pr_author=pr_author,
        page=page,
        per_page=per_page,
    )
    return json.dumps(result)
