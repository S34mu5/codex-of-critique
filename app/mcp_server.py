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
        for row in rows:
            r = dict(row._mapping)
            snippet_rows = session.execute(
                text(
                    "SELECT snippet_type, snippet_text, start_line, end_line "
                    "FROM code_snippets WHERE review_comment_id = :cid"
                ),
                {"cid": r["id"]},
            ).fetchall()
            r["snippets"] = [dict(s._mapping) for s in snippet_rows]
            for k, v in r.items():
                if isinstance(v, datetime):
                    r[k] = v.isoformat()
            results.append(r)

    return {"total": total, "page": page, "per_page": per_page, "results": results}


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
