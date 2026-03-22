import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from croniter import croniter
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services.sync_service import run_repository_sync

app = FastAPI(title="Codex-of-Critique Dashboard")

_gh_cache: dict = {"total_prs": None, "cached_at": 0}
_GH_CACHE_TTL = 600
_sync_lock = threading.Lock()
_sync_progress: dict = {
    "total_prs": 0,
    "processed_prs": 0,
    "current_pr": None,
    "current_repo": None,
    "phase": "idle",
    "selected_repositories": [],
    "repositories": {},
}


def _repo_identifier(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


def _configured_repositories() -> List[Dict[str, str]]:
    repositories: List[Dict[str, str]] = []
    seen: set[str] = set()

    for repo_config in settings.repositories:
        owner = str(repo_config.get("owner", "")).strip()
        repo = str(repo_config.get("repo", "")).strip()
        if not owner or not repo:
            continue

        identifier = _repo_identifier(owner, repo)
        if identifier in seen:
            continue

        repositories.append({"owner": owner, "repo": repo, "id": identifier})
        seen.add(identifier)

    return repositories


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_utc(value: object) -> object:
    if not isinstance(value, datetime):
        return value
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _next_sync_at(now: datetime) -> str | None:
    try:
        next_run = croniter(settings.sync_cron, _as_utc(now)).get_next(datetime)
    except Exception:
        return None
    return _iso_utc(next_run)


def _parse_repo_identifiers(raw: Optional[str]) -> List[str]:
    identifiers: List[str] = []
    seen: set[str] = set()

    if not raw:
        return identifiers

    for value in raw.split(","):
        token = value.strip()
        if not token:
            continue

        parts = token.split("/", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError("Invalid repository format. Use owner/repo,owner2/repo2")

        identifier = _repo_identifier(parts[0].strip(), parts[1].strip())
        if identifier in seen:
            continue

        identifiers.append(identifier)
        seen.add(identifier)

    return identifiers


def _parse_filter_values(raw: Optional[str]) -> List[str]:
    values: List[str] = []
    seen: set[str] = set()

    if not raw:
        return values

    for value in raw.split(","):
        token = value.strip()
        if not token or token in seen:
            continue
        values.append(token)
        seen.add(token)

    return values


def _resolve_selected_repositories(raw: Optional[str]) -> Optional[List[Dict[str, str]]]:
    if raw is None:
        return None

    selected_ids = _parse_repo_identifiers(raw)
    if not selected_ids:
        raise ValueError("Select at least one valid repository")

    configured = _configured_repositories()
    configured_by_id = {
        repo["id"]: {"owner": repo["owner"], "repo": repo["repo"]}
        for repo in configured
    }
    invalid = [repo_id for repo_id in selected_ids if repo_id not in configured_by_id]
    if invalid:
        raise ValueError(
            "Unknown repositories requested: " + ", ".join(invalid)
        )

    return [configured_by_id[repo_id] for repo_id in selected_ids]


def _add_repo_filter_conditions(
    conditions: List[str],
    params: Dict[str, Any],
    *,
    alias: str,
    include_repositories: Optional[str] = None,
    exclude_repositories: Optional[str] = None,
    legacy_repo: Optional[str] = None,
    prefix: str = "repo",
) -> None:
    include_ids: List[str] = []
    if include_repositories:
        include_ids = _parse_repo_identifiers(include_repositories)
    elif legacy_repo:
        legacy_token = legacy_repo.strip()
        if "/" in legacy_token:
            include_ids = _parse_repo_identifiers(legacy_token)
        elif legacy_token:
            key = f"{prefix}_legacy_name"
            params[key] = legacy_token
            conditions.append(f"{alias}.name = :{key}")

    if include_ids:
        include_clauses: List[str] = []
        for idx, repo_id in enumerate(include_ids):
            owner, repo = repo_id.split("/", 1)
            owner_key = f"{prefix}_include_owner_{idx}"
            repo_key = f"{prefix}_include_name_{idx}"
            params[owner_key] = owner
            params[repo_key] = repo
            include_clauses.append(
                f"({alias}.owner = :{owner_key} AND {alias}.name = :{repo_key})"
            )
        conditions.append("(" + " OR ".join(include_clauses) + ")")

    exclude_ids = _parse_repo_identifiers(exclude_repositories)
    if exclude_ids:
        exclude_clauses: List[str] = []
        for idx, repo_id in enumerate(exclude_ids):
            owner, repo = repo_id.split("/", 1)
            owner_key = f"{prefix}_exclude_owner_{idx}"
            repo_key = f"{prefix}_exclude_name_{idx}"
            params[owner_key] = owner
            params[repo_key] = repo
            exclude_clauses.append(
                f"({alias}.owner = :{owner_key} AND {alias}.name = :{repo_key})"
            )
        conditions.append("NOT (" + " OR ".join(exclude_clauses) + ")")


def _add_value_filter_conditions(
    conditions: List[str],
    params: Dict[str, Any],
    *,
    column: str,
    include_values: Optional[str] = None,
    exclude_values: Optional[str] = None,
    legacy_value: Optional[str] = None,
    prefix: str = "value",
) -> None:
    included = _parse_filter_values(include_values)
    if not included and legacy_value:
        included = _parse_filter_values(legacy_value)

    if included:
        include_clauses: List[str] = []
        for idx, value in enumerate(included):
            key = f"{prefix}_include_{idx}"
            params[key] = value
            include_clauses.append(f"{column} = :{key}")
        conditions.append("(" + " OR ".join(include_clauses) + ")")

    excluded = _parse_filter_values(exclude_values)
    if excluded:
        exclude_clauses: List[str] = []
        for idx, value in enumerate(excluded):
            key = f"{prefix}_exclude_{idx}"
            params[key] = value
            exclude_clauses.append(f"{column} = :{key}")
        conditions.append("NOT (" + " OR ".join(exclude_clauses) + ")")


def _reset_sync_progress() -> None:
    _sync_progress.update(
        total_prs=0,
        processed_prs=0,
        current_pr=None,
        current_repo=None,
        phase="idle",
        selected_repositories=[],
        repositories={},
    )


def _initialize_sync_progress(repositories: List[Dict[str, str]]) -> None:
    repo_progress = {}
    selected_ids: List[str] = []

    for repo in repositories:
        owner = repo["owner"]
        name = repo["repo"]
        identifier = _repo_identifier(owner, name)
        selected_ids.append(identifier)
        repo_progress[identifier] = {
            "id": identifier,
            "owner": owner,
            "repo": name,
            "phase": "queued",
            "total_prs": 0,
            "processed_prs": 0,
            "current_pr": None,
            "error": None,
        }

    _sync_progress.update(
        total_prs=0,
        processed_prs=0,
        current_pr=None,
        current_repo=None,
        phase="starting",
        selected_repositories=selected_ids,
        repositories=repo_progress,
    )


def _get_repo_progress(record: logging.LogRecord) -> Optional[Dict[str, Any]]:
    owner = getattr(record, "owner", None)
    repo = getattr(record, "repo", None)
    if not owner or not repo:
        return None

    identifier = _repo_identifier(owner, repo)
    repositories = _sync_progress.setdefault("repositories", {})
    progress = repositories.get(identifier)
    if progress is None:
        progress = {
            "id": identifier,
            "owner": owner,
            "repo": repo,
            "phase": "queued",
            "total_prs": 0,
            "processed_prs": 0,
            "current_pr": None,
            "error": None,
        }
        repositories[identifier] = progress
    return progress


class _SyncProgressHandler(logging.Handler):
    """Captures sync service log messages to update progress."""

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.msg
        repo_progress = _get_repo_progress(record)

        if msg == "sync_start":
            repo_id = repo_progress["id"] if repo_progress else None
            _sync_progress.update(current_pr=None, current_repo=repo_id, phase="fetching_prs")
            if repo_progress:
                repo_progress.update(
                    phase="fetching_prs",
                    total_prs=0,
                    processed_prs=0,
                    current_pr=None,
                    error=None,
                )
        elif msg == "sync_prs_done":
            count = getattr(record, "count", 0)
            _sync_progress["total_prs"] += count
            _sync_progress["phase"] = "processing_prs"
            if repo_progress:
                repo_progress["phase"] = "processing_prs"
                repo_progress["total_prs"] = count
        elif msg == "pr_extras_synced":
            _sync_progress["current_pr"] = getattr(record, "pr", None)
            _sync_progress["processed_prs"] += 1
            if repo_progress:
                repo_progress["phase"] = "processing_prs"
                repo_progress["current_pr"] = getattr(record, "pr", None)
                repo_progress["processed_prs"] += 1
        elif msg == "sync_pr_error":
            _sync_progress["current_pr"] = getattr(record, "pr_number", None)
            _sync_progress["processed_prs"] += 1
            if repo_progress:
                repo_progress["phase"] = "processing_prs"
                repo_progress["current_pr"] = getattr(record, "pr_number", None)
                repo_progress["processed_prs"] += 1
                repo_progress["error"] = record.getMessage()
        elif msg == "sync_repo_complete":
            if repo_progress:
                repo_progress["phase"] = "complete"
                repo_progress["current_pr"] = None
                repo_progress["error"] = None
        elif msg == "sync_repo_fatal_error":
            if repo_progress:
                repo_progress["phase"] = "error"
                repo_progress["current_pr"] = None
                repo_progress["error"] = record.getMessage()
        elif msg in ("sync_complete", "sync_fatal_error", "sync_global_fatal_error"):
            _reset_sync_progress()


async def _fetch_github_total_prs() -> int | None:
    now = time.time()
    if _gh_cache["total_prs"] is not None and (now - _gh_cache["cached_at"]) < _GH_CACHE_TTL:
        return _gh_cache["total_prs"]

    repositories = _configured_repositories()
    if not repositories:
        return _gh_cache.get("total_prs")

    total = 0
    try:
        async with httpx.AsyncClient() as client:
            for repo_config in repositories:
                owner = repo_config.get("owner")
                repo_name = repo_config.get("repo")

                if not owner or not repo_name:
                    continue

                query = {
                    "query": (
                        f'{{ repository(owner: "{owner}", name: "{repo_name}") '
                        f'{{ pullRequests {{ totalCount }} }} }}'
                    )
                }

                resp = await client.post(
                    "https://api.github.com/graphql",
                    headers={
                        "Authorization": f"bearer {settings.github_token}",
                        "Content-Type": "application/json",
                    },
                    json=query,
                    timeout=10,
                )

                if resp.status_code == 200:
                    repo_total = resp.json()["data"]["repository"]["pullRequests"]["totalCount"]
                    total += repo_total
                else:
                    # If one repo fails, continue with others
                    continue

        _gh_cache["total_prs"] = total
        _gh_cache["cached_at"] = now
        return total
    except Exception:
        return _gh_cache.get("total_prs")


async def _build_stats() -> dict:
    with SessionLocal() as session:
        table_names = [
            "repositories", "pull_requests", "review_threads",
            "review_comments", "code_authorship", "code_snippets",
        ]
        counts = {
            t: session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            for t in table_names
        }

        sync_summary_row = session.execute(text("""
            SELECT
                MAX(last_success_at) AS last_success_at,
                MAX(last_error_at) AS last_error_at,
                MAX(last_pr_updated_at) AS last_pr_updated_at
            FROM sync_state
            WHERE sync_name = 'pr_scan'
        """)).fetchone()
        sync = dict(sync_summary_row._mapping) if sync_summary_row else {}

        latest_error_row = session.execute(text("""
            SELECT last_error_message
            FROM sync_state
            WHERE sync_name = 'pr_scan'
              AND last_error_at IS NOT NULL
            ORDER BY last_error_at DESC
            LIMIT 1
        """)).fetchone()
        sync["last_error_message"] = latest_error_row[0] if latest_error_row else None

        last_rc = session.execute(text("SELECT MAX(updated_at) FROM review_comments")).scalar()
        last_pr = session.execute(text("SELECT MAX(updated_at) FROM pull_requests")).scalar()

        activity_rows = session.execute(text("""
            SELECT rc.path, rc.comment_author_login AS actor,
                   pr.number AS pr_number, rc.updated_at AS ts,
                   rp.owner AS repo_owner, rp.name AS repo_name
            FROM review_comments rc
            JOIN pull_requests pr ON pr.id = rc.pull_request_id
            JOIN repositories rp ON rp.id = rc.repository_id
            ORDER BY rc.updated_at DESC
            LIMIT 8
        """)).fetchall()
        activity = [dict(r._mapping) for r in activity_rows]

        repo_rows = session.execute(text("""
            SELECT r.owner, r.name,
                   ss.last_success_at, ss.last_error_at, ss.last_error_message, ss.last_pr_updated_at,
                   COALESCE(pr_counts.pull_requests, 0) AS pull_requests,
                   COALESCE(rc_counts.review_comments, 0) AS review_comments
            FROM repositories r
            LEFT JOIN sync_state ss
              ON ss.repository_id = r.id
             AND ss.sync_name = 'pr_scan'
            LEFT JOIN (
                SELECT repository_id, COUNT(*) AS pull_requests
                FROM pull_requests
                GROUP BY repository_id
            ) pr_counts ON pr_counts.repository_id = r.id
            LEFT JOIN (
                SELECT repository_id, COUNT(*) AS review_comments
                FROM review_comments
                GROUP BY repository_id
            ) rc_counts ON rc_counts.repository_id = r.id
            ORDER BY r.owner, r.name
        """)).fetchall()

    now = datetime.now(timezone.utc)

    def secs_since(dt: datetime | None) -> float:
        return (now - _as_utc(dt)).total_seconds() if dt else 9999.0

    active_secs = min(secs_since(last_rc), secs_since(last_pr))
    is_active = active_secs < 45

    total_prs = await _fetch_github_total_prs()
    pr_count = counts["pull_requests"]
    pr_pct = round(pr_count / total_prs * 100, 1) if total_prs else None

    configured_repositories = _configured_repositories()
    runtime_selected = set(_sync_progress["selected_repositories"]) if _sync_lock.locked() else set()
    runtime_progress = _sync_progress["repositories"] if _sync_lock.locked() else {}

    repo_status_map: Dict[str, Dict[str, Any]] = {
        repo["id"]: {
            "id": repo["id"],
            "owner": repo["owner"],
            "repo": repo["repo"],
            "configured": True,
            "pull_requests": 0,
            "review_comments": 0,
            "last_success_at": None,
            "last_error_at": None,
            "last_error_message": None,
            "last_pr_updated_at": None,
            "selected_for_sync": repo["id"] in runtime_selected,
            "runtime_phase": "idle",
            "runtime_total_prs": 0,
            "runtime_processed_prs": 0,
            "runtime_current_pr": None,
            "runtime_error": None,
            "status": "excluded" if repo["id"] not in runtime_selected and runtime_selected else "idle",
        }
        for repo in configured_repositories
    }

    for row in repo_rows:
        owner = row.owner
        repo_name = row.name
        repo_id = _repo_identifier(owner, repo_name)
        item = repo_status_map.setdefault(
            repo_id,
            {
                "id": repo_id,
                "owner": owner,
                "repo": repo_name,
                "configured": False,
                "selected_for_sync": repo_id in runtime_selected,
            },
        )
        item.update(
            configured=item.get("configured", False),
            pull_requests=row.pull_requests,
            review_comments=row.review_comments,
            last_success_at=_iso_utc(row.last_success_at),
            last_error_at=_iso_utc(row.last_error_at),
            last_error_message=row.last_error_message,
            last_pr_updated_at=_iso_utc(row.last_pr_updated_at),
        )

    for repo_id, item in repo_status_map.items():
        runtime = runtime_progress.get(repo_id)
        selected_for_sync = item.get("selected_for_sync", False)
        status = "idle"

        item.update(
            runtime_phase=runtime["phase"] if runtime else "idle",
            runtime_total_prs=runtime["total_prs"] if runtime else 0,
            runtime_processed_prs=runtime["processed_prs"] if runtime else 0,
            runtime_current_pr=runtime["current_pr"] if runtime else None,
            runtime_error=runtime["error"] if runtime else None,
            selected_for_sync=selected_for_sync,
        )

        if runtime_selected and not selected_for_sync:
            status = "excluded"
        elif runtime:
            status = runtime["phase"]
        elif item.get("last_error_at") and (
            not item.get("last_success_at") or item["last_error_at"] >= item["last_success_at"]
        ):
            status = "error"
        elif item.get("last_success_at"):
            status = "synced"

        item["status"] = status

    repo_statuses = sorted(
        repo_status_map.values(),
        key=lambda item: (
            0 if item.get("configured") else 1,
            item["owner"].lower(),
            item["repo"].lower(),
        ),
    )

    if _sync_lock.locked() and _sync_progress["phase"] != "idle":
        phase = _sync_progress["phase"]
        if phase == "starting":
            phase_label = "Starting sync"
        elif phase == "fetching_prs":
            phase_label = "Phase 1 — Fetching Pull Requests"
        elif phase == "processing_prs":
            phase_label = "Phase 2 — Processing threads & comments"
        else:
            phase_label = phase.replace("_", " ").title()
    elif total_prs and pr_count < total_prs:
        phase = "fetching_prs"
        phase_label = "Phase 1 — Fetching Pull Requests"
    elif is_active:
        phase = "fetching_threads"
        phase_label = "Phase 2 — Processing threads & comments"
    elif sync.get("last_success_at"):
        phase = "idle"
        phase_label = "Sync complete"
    else:
        phase = "waiting"
        phase_label = "Waiting..."

    for item in activity:
        for k, v in item.items():
            if isinstance(v, datetime):
                item[k] = _iso_utc(v)

    return {
        "ts": _iso_utc(now),
        "tables": counts,
        "sync": {
            "last_success_at": _iso_utc(sync.get("last_success_at")),
            "last_error_at": _iso_utc(sync.get("last_error_at")),
            "last_error_message": sync.get("last_error_message"),
            "last_pr_updated_at": _iso_utc(sync.get("last_pr_updated_at")),
            "next_sync_at": _next_sync_at(now),
            "cron": settings.sync_cron,
            "manual_running": _sync_lock.locked(),
            "progress_phase": _sync_progress["phase"],
            "progress_total": _sync_progress["total_prs"],
            "progress_done": _sync_progress["processed_prs"],
            "progress_current_pr": _sync_progress["current_pr"],
            "progress_current_repo": _sync_progress["current_repo"],
            "selected_repositories": list(_sync_progress["selected_repositories"]),
        },
        "github": {
            "total_prs": total_prs,
            "repositories": configured_repositories,
            "repo_count": len(configured_repositories),
        },
        "progress": {
            "phase": phase,
            "phase_label": phase_label,
            "pr_pct": pr_pct,
            "is_active": is_active or _sync_lock.locked(),
            "active_secs_ago": round(active_secs),
        },
        "repo_statuses": repo_statuses,
        "activity": activity,
    }


@app.get("/api/stats")
async def stats() -> dict:
    return await _build_stats()


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    async def event_generator():
        while True:
            try:
                data = await _build_stats()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/repositories")
def get_repositories() -> dict:
    """Get list of configured repositories."""
    repositories = _configured_repositories()
    return {"repositories": repositories, "count": len(repositories)}


@app.post("/api/sync/trigger")
def trigger_sync(repositories: Optional[str] = None) -> dict:
    if not _sync_lock.acquire(blocking=False):
        return {"status": "already_running"}

    try:
        selected_repos = _resolve_selected_repositories(repositories)
    except ValueError as exc:
        _sync_lock.release()
        return {"status": "error", "message": str(exc)}

    sync_targets = selected_repos if selected_repos is not None else [
        {"owner": repo["owner"], "repo": repo["repo"]}
        for repo in _configured_repositories()
    ]
    if not sync_targets:
        _sync_lock.release()
        return {"status": "error", "message": "No repositories configured for sync"}

    _initialize_sync_progress(sync_targets)
    handler = _SyncProgressHandler()
    handler.setLevel(logging.DEBUG)
    logging.getLogger().setLevel(logging.INFO)
    sync_logger = logging.getLogger("app.services.sync_service")
    extras_logger = logging.getLogger("app.services.pr_extras_service")
    sync_logger.setLevel(logging.DEBUG)
    extras_logger.setLevel(logging.DEBUG)
    sync_logger.addHandler(handler)
    extras_logger.addHandler(handler)

    def _run():
        try:
            run_repository_sync(selected_repos)
        finally:
            sync_logger.removeHandler(handler)
            extras_logger.removeHandler(handler)
            _reset_sync_progress()
            _sync_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return {
        "status": "started",
        "repositories": [_repo_identifier(repo["owner"], repo["repo"]) for repo in sync_targets],
    }


@app.get("/api/sync/status")
def sync_status() -> dict:
    return {"running": _sync_lock.locked()}


@app.get("/api/filters")
def filters() -> dict:
    with SessionLocal() as session:
        repos = [
            _repo_identifier(r.owner, r.name)
            for r in session.execute(
                text("SELECT DISTINCT owner, name FROM repositories ORDER BY owner, name")
            ).fetchall()
        ]
        pr_authors = [r[0] for r in session.execute(
            text("SELECT DISTINCT author_login FROM pull_requests WHERE author_login IS NOT NULL ORDER BY author_login")
        ).fetchall()]
        reviewers = [r[0] for r in session.execute(
            text("SELECT DISTINCT comment_author_login FROM review_comments WHERE comment_author_login IS NOT NULL ORDER BY comment_author_login")
        ).fetchall()]
    return {"repositories": repos, "pr_authors": pr_authors, "reviewers": reviewers}


@app.get("/api/search")
def search(
    repo: Optional[str] = Query(None),
    repositories: Optional[str] = Query(None),
    exclude_repositories: Optional[str] = Query(None),
    pr_author: Optional[str] = Query(None),
    pr_authors: Optional[str] = Query(None),
    exclude_pr_authors: Optional[str] = Query(None),
    reviewer: Optional[str] = Query(None),
    reviewers: Optional[str] = Query(None),
    exclude_reviewers: Optional[str] = Query(None),
    comment_q: Optional[str] = Query(None),
    snippet_q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict:
    conditions = []
    params: dict = {}

    try:
        _add_repo_filter_conditions(
            conditions,
            params,
            alias="rp",
            include_repositories=repositories,
            exclude_repositories=exclude_repositories,
            legacy_repo=repo,
            prefix="search_repo",
        )
    except ValueError as exc:
        return {
            "error": str(exc),
            "total": 0,
            "page": page,
            "per_page": per_page,
            "results": [],
        }

    _add_value_filter_conditions(
        conditions,
        params,
        column="pr.author_login",
        include_values=pr_authors,
        exclude_values=exclude_pr_authors,
        legacy_value=pr_author,
        prefix="search_pr_author",
    )
    _add_value_filter_conditions(
        conditions,
        params,
        column="rc.comment_author_login",
        include_values=reviewers,
        exclude_values=exclude_reviewers,
        legacy_value=reviewer,
        prefix="search_reviewer",
    )
    if comment_q:
        conditions.append("rc.body LIKE :comment_q")
        params["comment_q"] = f"%{comment_q}%"
    if snippet_q:
        conditions.append("EXISTS (SELECT 1 FROM code_snippets cs2 WHERE cs2.review_comment_id = rc.id AND cs2.snippet_text LIKE :snippet_q)")
        params["snippet_q"] = f"%{snippet_q}%"

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
                   rc.comment_author_login, rc.body, rc.diff_hunk,
                   rc.line, rc.start_line, rc.comment_created_at,
                   rc.comment_commit_oid,
                   pr.number AS pr_number, pr.title AS pr_title,
                   pr.author_login AS pr_author,
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
            snippet_rows = session.execute(text(
                "SELECT snippet_type, snippet_text, start_line, end_line "
                "FROM code_snippets WHERE review_comment_id = :cid"
            ), {"cid": r["id"]}).fetchall()
            r["snippets"] = [dict(s._mapping) for s in snippet_rows]
            for k, v in r.items():
                if isinstance(v, datetime):
                    r[k] = v.isoformat()
            results.append(r)

    return {"total": total, "page": page, "per_page": per_page, "results": results}


@app.get("/api/activity")
def activity(
    username: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    repositories: Optional[str] = Query(None),
    exclude_repositories: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict:
    offset = (page - 1) * per_page
    params: dict = {"username": username or "", "limit": per_page, "offset": offset}
    result: dict = {}
    repo_conditions: List[str] = []

    try:
        _add_repo_filter_conditions(
            repo_conditions,
            params,
            alias="rp",
            include_repositories=repositories,
            exclude_repositories=exclude_repositories,
            prefix="activity_repo",
        )
    except ValueError as exc:
        return {
            "error": str(exc),
            "username": username,
            "sections": {},
            "page": page,
            "per_page": per_page,
            "category": category,
        }

    repo_where = ""
    if repo_conditions:
        repo_where = " AND " + " AND ".join(repo_conditions)

    with SessionLocal() as session:
        # --- Pending reviews ---
        if not category or category == "pending_reviews":
            if category == "pending_reviews":
                result["total"] = session.execute(text(f"""
                    SELECT COUNT(*) FROM review_requests rr
                    JOIN pull_requests pr ON pr.id = rr.pull_request_id
                    JOIN repositories rp ON rp.id = pr.repository_id
                    WHERE pr.state = 'OPEN' AND rr.status = 'pending'
                      AND (:username = '' OR rr.requested_reviewer_login = :username)
                      {repo_where}
                """), params).scalar()
            rows = session.execute(text(f"""
                SELECT pr.number AS pr_number, pr.title AS pr_title,
                       pr.updated_at_github, pr.author_login AS pr_author,
                       rp.name AS repo_name, rp.owner AS repo_owner,
                       rr.requested_reviewer_login, rr.created_at AS requested_at
                FROM review_requests rr
                JOIN pull_requests pr ON pr.id = rr.pull_request_id
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'OPEN'
                  AND rr.status = 'pending'
                  AND (:username = '' OR rr.requested_reviewer_login = :username)
                  {repo_where}
                ORDER BY pr.updated_at_github DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
            result["pending_reviews"] = [_row_to_dict(r) for r in rows]

        # --- Changes requested — not addressed (no commits since review) ---
        if not category or category == "changes_not_addressed":
            rows = session.execute(text(f"""
                SELECT DISTINCT pr.number AS pr_number, pr.title AS pr_title,
                       pr.updated_at_github, rp.name AS repo_name, rp.owner AS repo_owner,
                       rev.author_login AS reviewer, rev.submitted_at AS review_date
                FROM pr_reviews rev
                JOIN pull_requests pr ON pr.id = rev.pull_request_id
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'OPEN'
                  AND (:username = '' OR pr.author_login = :username)
                  AND rev.state = 'CHANGES_REQUESTED'
                  {repo_where}
                  AND rev.submitted_at = (
                    SELECT MAX(r2.submitted_at) FROM pr_reviews r2
                    WHERE r2.pull_request_id = rev.pull_request_id
                      AND r2.author_login = rev.author_login
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM review_requests rr
                    WHERE rr.pull_request_id = pr.id
                      AND rr.requested_reviewer_login = rev.author_login
                      AND rr.status = 'pending'
                  )
                  AND (pr.last_commit_at IS NULL OR pr.last_commit_at <= rev.submitted_at)
                ORDER BY rev.submitted_at DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
            result["changes_not_addressed"] = [_row_to_dict(r) for r in rows]

        # --- Changes requested — not re-requested (commits after review, no re-request) ---
        if not category or category == "changes_forgot_rerequest":
            rows = session.execute(text(f"""
                SELECT DISTINCT pr.number AS pr_number, pr.title AS pr_title,
                       pr.updated_at_github, rp.name AS repo_name, rp.owner AS repo_owner,
                       pr.author_login AS pr_author,
                       rev.author_login AS reviewer, rev.submitted_at AS review_date,
                       pr.last_commit_at
                FROM pr_reviews rev
                JOIN pull_requests pr ON pr.id = rev.pull_request_id
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'OPEN'
                  AND (:username = '' OR pr.author_login = :username)
                  AND rev.state = 'CHANGES_REQUESTED'
                  {repo_where}
                  AND rev.submitted_at = (
                    SELECT MAX(r2.submitted_at) FROM pr_reviews r2
                    WHERE r2.pull_request_id = rev.pull_request_id
                      AND r2.author_login = rev.author_login
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM review_requests rr
                    WHERE rr.pull_request_id = pr.id
                      AND rr.requested_reviewer_login = rev.author_login
                      AND rr.status = 'pending'
                  )
                  AND pr.last_commit_at > rev.submitted_at
                ORDER BY pr.last_commit_at DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
            result["changes_forgot_rerequest"] = [_row_to_dict(r) for r in rows]

        # --- Changes requested — addressed (awaiting re-review) ---
        if not category or category == "changes_addressed":
            rows = session.execute(text(f"""
                SELECT DISTINCT pr.number AS pr_number, pr.title AS pr_title,
                       pr.updated_at_github, rp.name AS repo_name, rp.owner AS repo_owner,
                       rev.author_login AS reviewer, rev.submitted_at AS review_date
                FROM pr_reviews rev
                JOIN pull_requests pr ON pr.id = rev.pull_request_id
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'OPEN'
                  AND (:username = '' OR pr.author_login = :username)
                  AND rev.state = 'CHANGES_REQUESTED'
                  {repo_where}
                  AND rev.submitted_at = (
                    SELECT MAX(r2.submitted_at) FROM pr_reviews r2
                    WHERE r2.pull_request_id = rev.pull_request_id
                      AND r2.author_login = rev.author_login
                  )
                  AND EXISTS (
                    SELECT 1 FROM review_requests rr
                    WHERE rr.pull_request_id = pr.id
                      AND rr.requested_reviewer_login = rev.author_login
                      AND rr.status = 'pending'
                  )
                ORDER BY rev.submitted_at DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
            result["changes_addressed"] = [_row_to_dict(r) for r in rows]

        # --- Merged PRs ---
        if not category or category == "changes_merged":
            if category == "changes_merged":
                result["total"] = session.execute(text(f"""
                    SELECT COUNT(*) FROM pull_requests pr
                    JOIN repositories rp ON rp.id = pr.repository_id
                    WHERE pr.state = 'MERGED' AND (:username = '' OR pr.author_login = :username)
                    {repo_where}
                """), params).scalar()
            rows = session.execute(text(f"""
                SELECT pr.number AS pr_number, pr.title AS pr_title,
                       pr.merged_at_github, rp.name AS repo_name, rp.owner AS repo_owner
                FROM pull_requests pr
                JOIN repositories rp ON rp.id = pr.repository_id
                WHERE pr.state = 'MERGED'
                  AND (:username = '' OR pr.author_login = :username)
                  {repo_where}
                ORDER BY pr.merged_at_github DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
            result["changes_merged"] = [_row_to_dict(r) for r in rows]

        # --- Recent comments ---
        if not category or category == "comments":
            rows = session.execute(text(f"""
                (SELECT 'comment' AS type, pc.author_login, pc.body,
                        pc.comment_created_at AS ts,
                        pr.number AS pr_number, pr.title AS pr_title,
                        rp.name AS repo_name, rp.owner AS repo_owner
                 FROM pr_comments pc
                 JOIN pull_requests pr ON pr.id = pc.pull_request_id
                 JOIN repositories rp ON rp.id = pc.repository_id
                 WHERE 1 = 1 {repo_where}
                 ORDER BY pc.comment_created_at DESC LIMIT 50)
                UNION ALL
                (SELECT 'review_comment' AS type, rc.comment_author_login AS author_login,
                        rc.body, rc.comment_created_at AS ts,
                        pr.number AS pr_number, pr.title AS pr_title,
                        rp.name AS repo_name, rp.owner AS repo_owner
                 FROM review_comments rc
                 JOIN pull_requests pr ON pr.id = rc.pull_request_id
                 JOIN repositories rp ON rp.id = rc.repository_id
                 WHERE 1 = 1 {repo_where}
                 ORDER BY rc.comment_created_at DESC LIMIT 50)
                ORDER BY ts DESC
                LIMIT :limit OFFSET :offset
            """), params).fetchall()
            result["comments"] = [_row_to_dict(r) for r in rows]

    return {"username": username, "sections": result, "page": page, "per_page": per_page, "category": category}


def _row_to_dict(row) -> dict:
    d = dict(row._mapping)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = _iso_utc(v)
    return d


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex of Critique</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#060d18;--s1:#0c1829;--s2:#132034;--border:#1a2e4a;
  --blue:#3b82f6;--cyan:#22d3ee;--green:#10b981;--yellow:#f59e0b;--red:#ef4444;
  --text:#dde5f0;--muted:#4e6278;
  --mono:'JetBrains Mono',monospace;--sans:'Inter',sans-serif;
  --diff-add:#1a3a2a;--diff-del:#3a1a1a;--diff-add-text:#7ee787;--diff-del-text:#f97583;
  --code-bg:#0d1117;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;padding:0}
.app-wrap{padding:28px 32px}

/* ---- HEADER ---- */
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:0;padding:20px 32px;border-bottom:1px solid var(--border)}
.logo{font-family:var(--mono);font-size:20px;font-weight:700;color:var(--cyan);letter-spacing:-.3px}
.subrepo{font-family:var(--mono);font-size:12px;color:var(--muted);margin-top:5px}
.header-right{display:flex;align-items:center;gap:14px}
.badge{display:flex;align-items:center;gap:8px;padding:6px 16px;border-radius:999px;font-size:12px;font-weight:600;font-family:var(--mono);border:1px solid;transition:all .4s}
.badge.active{background:#10b98114;border-color:var(--green);color:var(--green)}
.badge.idle{background:#3b82f614;border-color:var(--blue);color:var(--blue)}
.badge.error{background:#ef444414;border-color:var(--red);color:var(--red)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0}
.pulse{animation:pulse 1s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}
.live-chip{font-family:var(--mono);font-size:11px;background:var(--s1);border:1px solid var(--border);padding:4px 10px;border-radius:6px;transition:color .4s,border-color .4s}
.live-chip.clr-green{color:var(--green);border-color:#10b98140}
.live-chip.clr-yellow{color:var(--yellow);border-color:#f59e0b40}
.live-chip.clr-muted{color:var(--muted);border-color:var(--border)}
.live-chip .live-dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px}
.live-chip.clr-green .live-dot{background:var(--green);animation:pulse 1s ease-in-out infinite}
.live-chip.clr-yellow .live-dot{background:var(--yellow);animation:pulse 1.5s ease-in-out infinite}
.live-chip.clr-muted .live-dot{background:var(--muted);animation:none}

/* ---- TABS ---- */
.tab-bar{display:flex;gap:0;background:var(--s1);border-bottom:1px solid var(--border);padding:0 32px}
.tab{padding:12px 24px;font-size:13px;font-weight:600;font-family:var(--mono);color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .2s;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--cyan);border-bottom-color:var(--cyan)}
.tab-page{display:none}.tab-page.active{display:block}

/* ---- DASHBOARD ---- */
.section{margin-bottom:28px}
.section-title{font-size:10px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted);margin-bottom:14px}

.phase-card{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:26px}
.phase-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.phase-left{display:flex;align-items:center;gap:12px}
.phase-name{font-size:14px;font-weight:600}
.phase-tag{font-family:var(--mono);font-size:11px;padding:3px 10px;border-radius:5px;background:var(--s2);color:var(--muted)}
.spinner{width:20px;height:20px;flex-shrink:0;display:none}
.spinner.on{display:block;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.throughput{font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:16px;min-height:16px}
.throughput span{color:var(--green);font-weight:700}
.bars{display:flex;flex-direction:column;gap:16px}
.bar-row label{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);font-family:var(--mono);margin-bottom:7px}
.bar-row label span{color:var(--text);font-weight:700}
.track{height:7px;background:var(--s2);border-radius:99px;overflow:hidden;position:relative}
.fill{height:100%;border-radius:99px;transition:width 1.8s cubic-bezier(.4,0,.2,1)}
.fill.blue{background:linear-gradient(90deg,var(--blue),var(--cyan))}
.fill.green{background:linear-gradient(90deg,var(--green),#34d399)}
.fill.active-shimmer{position:relative;overflow:hidden}
.fill.active-shimmer::after{
  content:'';position:absolute;top:0;left:-40px;width:40px;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);
  animation:shimmer 1.2s ease-in-out infinite;
}
@keyframes shimmer{0%{left:-40px}100%{left:100%}}

.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px;position:relative;overflow:hidden;transition:border-color .3s}
.card.flash{border-color:var(--green)!important;box-shadow:0 0 12px #10b98130}
.card::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--blue),var(--cyan));opacity:.35}
.card-icon{font-size:20px;margin-bottom:10px}
.card-count{font-size:30px;font-weight:700;font-family:var(--mono);line-height:1;transition:color .3s}
.card-count.bump{color:var(--green)}
.card-label{font-size:10px;color:var(--muted);margin-top:5px;text-transform:uppercase;letter-spacing:.9px}
.card-delta{font-size:10px;color:var(--green);font-family:var(--mono);margin-top:3px;min-height:14px;transition:opacity .5s}

.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.info-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:20px}
.info-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.9px;margin-bottom:8px}
.info-value{font-family:var(--mono);font-size:13px}
.ok{color:var(--green)}.err{color:var(--red)}.dim{color:var(--muted)}

.feed{background:var(--s1);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.feed-row{display:flex;align-items:center;gap:12px;padding:10px 18px;border-bottom:1px solid var(--border);font-size:12px;animation:slide-in .3s ease-out}
.feed-row:last-child{border-bottom:none}
@keyframes slide-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
.feed-pr{font-family:var(--mono);color:var(--cyan);font-size:11px;min-width:48px}
.feed-actor{color:var(--muted);min-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.feed-path{color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:var(--mono);font-size:11px}
.feed-ts{color:var(--muted);font-size:10px;font-family:var(--mono);white-space:nowrap}

.err-box{background:#ef444410;border:1px solid var(--red);border-radius:8px;padding:12px 16px;font-size:12px;font-family:var(--mono);color:var(--red);margin-top:14px;display:none}
.control-grid{display:grid;grid-template-columns:minmax(320px,1.2fr) minmax(260px,.8fr);gap:16px;align-items:start}
.repo-picker{position:relative;overflow:visible}
.repo-picker summary{list-style:none;cursor:pointer;padding:10px 14px;min-height:40px;background:var(--s2);border:1px solid var(--border);border-radius:10px;font-family:var(--mono);font-size:12px;color:var(--text);display:flex;align-items:center;justify-content:space-between;gap:10px;transition:border-color .18s ease,box-shadow .18s ease,background .18s ease}
.repo-picker summary::-webkit-details-marker{display:none}
.repo-picker summary::after{content:'';width:9px;height:9px;border-right:1.5px solid var(--muted);border-bottom:1.5px solid var(--muted);transform:rotate(45deg) translateY(-2px);transform-origin:center;transition:transform .18s ease,border-color .18s ease;flex-shrink:0}
.repo-picker summary:hover{border-color:#2d4b68;background:#122033}
.repo-picker[open]{z-index:30}
.repo-picker[open] summary{border-color:#22d3ee55;box-shadow:0 0 0 3px #22d3ee12}
.repo-picker[open] summary::after{transform:rotate(225deg) translateY(-1px);border-color:var(--cyan)}
.repo-picker-body{position:absolute;top:calc(100% + 8px);left:0;right:0;z-index:31;padding:12px;background:#08111d;border:1px solid #1f3349;border-radius:12px;box-shadow:0 20px 48px rgba(0,0,0,.38);display:flex;flex-direction:column;gap:12px;max-height:var(--picker-max-height,calc(100vh - 32px));overflow-x:hidden;overflow-y:auto}
.repo-picker-actions{display:flex;gap:8px;flex-wrap:wrap;padding-bottom:10px;border-bottom:1px solid var(--border)}
.repo-picker-actions button{background:transparent;border:1px solid var(--border);color:var(--muted);font-family:var(--mono);font-size:10px;padding:5px 10px;border-radius:999px;cursor:pointer}
.repo-picker-actions button:hover{border-color:var(--cyan);color:var(--text)}
.repo-picker-list{display:flex;flex-direction:column;gap:4px;padding-right:2px}
.repo-option{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid transparent;border-radius:8px;background:transparent;font-family:var(--mono);font-size:11px;color:var(--text);transition:background .15s ease,border-color .15s ease}
.repo-option:hover{background:#0f1c2d;border-color:var(--border)}
.repo-option input{accent-color:var(--cyan);width:14px;height:14px;flex-shrink:0}
.repo-option span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.repo-option.dim span{color:#9fb0c4}
.native-picker{display:none}
.single-picker .repo-option{cursor:pointer}
.repo-hint{font-family:var(--mono);font-size:11px;color:var(--muted)}
.repo-status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.repo-status-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:10px}
.repo-status-card.excluded{opacity:.6}
.repo-status-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.repo-status-name{font-family:var(--mono);font-size:12px;color:var(--text);word-break:break-word}
.repo-status-badge{font-family:var(--mono);font-size:10px;padding:3px 8px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.repo-status-badge.state-idle,.repo-status-badge.state-synced{color:var(--blue);border-color:#3b82f640;background:#3b82f614}
.repo-status-badge.state-starting,.repo-status-badge.state-fetching_prs,.repo-status-badge.state-processing_prs,.repo-status-badge.state-queued{color:var(--cyan);border-color:#22d3ee40;background:#22d3ee14}
.repo-status-badge.state-complete{color:var(--green);border-color:#10b98140;background:#10b98114}
.repo-status-badge.state-error{color:var(--red);border-color:#ef444440;background:#ef444414}
.repo-status-badge.state-excluded{color:var(--muted);border-color:var(--border);background:#0f1c2d}
.repo-status-lines{display:flex;flex-direction:column;gap:6px}
.repo-status-line{display:flex;justify-content:space-between;gap:12px;font-family:var(--mono);font-size:11px;color:var(--muted)}
.repo-status-line strong{color:var(--text);font-weight:600}
.repo-status-line.error strong{color:var(--red)}

/* ---- SEARCH PAGE ---- */
.search-bar{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:20px 24px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin-bottom:24px;overflow:visible}
.search-bar .field{display:flex;flex-direction:column;gap:4px;flex:1;min-width:160px}
.search-bar .field label{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600}
.search-bar select,.search-bar input[type=text]{background:var(--s2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:12px;padding:8px 12px;border-radius:8px;outline:none;width:100%}
.search-bar select:focus,.search-bar input[type=text]:focus{border-color:var(--cyan)}
.search-bar select option{background:var(--s2);color:var(--text)}
.btn-search{background:var(--cyan);color:var(--bg);font-family:var(--mono);font-weight:700;font-size:12px;border:none;padding:9px 24px;border-radius:8px;cursor:pointer;white-space:nowrap;align-self:flex-end;transition:opacity .2s}
.btn-search:hover{opacity:.85}
.btn-search:disabled{opacity:.4;cursor:not-allowed}
.repo-filter-stack{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;width:100%;flex:1 1 100%}
.repo-filter-stack .field{min-width:0}

.search-meta{font-size:12px;color:var(--muted);font-family:var(--mono);margin-bottom:16px;display:flex;justify-content:space-between;align-items:center}
.pagination{display:flex;gap:6px}
.pagination button{background:var(--s2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:11px;padding:5px 14px;border-radius:6px;cursor:pointer}
.pagination button:disabled{opacity:.3;cursor:not-allowed}
.pagination button:hover:not(:disabled){border-color:var(--cyan)}

/* Review comment card */
.rc-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;margin-bottom:14px;overflow:hidden;transition:border-color .2s}
.rc-card:hover{border-color:var(--blue)}
.rc-head{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.rc-avatar{width:28px;height:28px;border-radius:50%;background:var(--s2);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--cyan);flex-shrink:0}
.rc-author{font-weight:600;font-size:13px}
.rc-pr-badge{font-family:var(--mono);font-size:11px;color:var(--cyan);background:#22d3ee10;border:1px solid #22d3ee30;padding:2px 8px;border-radius:4px}
.rc-repo-badge{font-family:var(--mono);font-size:10px;color:var(--muted);background:#0f1c2d;border:1px solid var(--border);padding:2px 8px;border-radius:4px}
.rc-pr-title{font-size:11px;color:var(--muted);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:100px}
.rc-time{font-family:var(--mono);font-size:10px;color:var(--muted);margin-left:auto;white-space:nowrap}
.rc-path{padding:10px 18px;font-family:var(--mono);font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:6px;background:var(--s2)}
.rc-path .icon{color:var(--blue)}
.rc-path .line-badge{background:var(--blue);color:var(--bg);font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;margin-left:auto}
.rc-body{padding:16px 18px;font-size:13px;line-height:1.65;white-space:pre-wrap;word-break:break-word}

/* Diff hunk (GitHub-style) */
.diff-block{border-top:1px solid var(--border);background:var(--code-bg);overflow-x:auto;font-family:var(--mono);font-size:11px;line-height:1.7}
.diff-block .diff-header{padding:6px 16px;background:#161b22;color:var(--muted);font-size:10px;border-bottom:1px solid var(--border)}
.diff-line{padding:0 16px;white-space:pre}
.diff-line.add{background:var(--diff-add);color:var(--diff-add-text)}
.diff-line.del{background:var(--diff-del);color:var(--diff-del-text)}
.diff-line.hunk{color:var(--blue);background:#1a2233}

/* Snippet block */
.snippet-block{border-top:1px solid var(--border);background:var(--code-bg);overflow-x:auto;font-family:var(--mono);font-size:11px;line-height:1.7}
.snippet-block .snippet-header{padding:6px 16px;background:#161b22;color:var(--muted);font-size:10px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between}
.snippet-block pre{padding:10px 16px;margin:0;color:var(--text)}

/* ---- ACTIVITY PAGE ---- */
.activity-bar{background:var(--s1);border:1px solid var(--border);border-radius:14px;padding:20px 24px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin-bottom:24px;overflow:visible}
.activity-bar .field{display:flex;flex-direction:column;gap:4px;flex:1;min-width:160px}
.activity-bar .field label{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:600}
.activity-bar input[type=text],.activity-bar select{background:var(--s2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:12px;padding:8px 12px;border-radius:8px;outline:none;width:100%}
.activity-bar input[type=text]:focus,.activity-bar select:focus{border-color:var(--cyan)}
.activity-section{margin-bottom:28px}
.activity-section-header{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.activity-section-title{font-size:10px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted)}
.count-badge{font-family:var(--mono);font-size:10px;background:var(--cyan);color:var(--bg);padding:2px 8px;border-radius:10px;font-weight:700}
.activity-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:14px 18px;margin-bottom:10px;display:flex;align-items:center;gap:14px;transition:border-color .2s;cursor:pointer;text-decoration:none;color:inherit}
.activity-card:hover{border-color:var(--blue)}
.activity-card .ac-pr{font-family:var(--mono);font-size:12px;color:var(--cyan);white-space:nowrap}
.activity-card .ac-title{font-size:13px;font-weight:500;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.activity-card .ac-meta{font-size:11px;color:var(--muted);font-family:var(--mono);white-space:nowrap}
.activity-card .ac-repo{font-size:10px;color:var(--muted);font-family:var(--mono);white-space:nowrap}
.comment-card{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:14px 18px;margin-bottom:10px;transition:border-color .2s;cursor:pointer;text-decoration:none;color:inherit;display:block}
.comment-card:hover{border-color:var(--blue)}
.comment-card .cc-head{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.comment-card .cc-author{font-weight:600;font-size:12px}
.comment-card .cc-pr{font-family:var(--mono);font-size:11px;color:var(--cyan)}
.comment-card .cc-time{font-family:var(--mono);font-size:10px;color:var(--muted);margin-left:auto}
.comment-card .cc-body{font-size:12px;color:var(--text);line-height:1.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}

.empty-state{text-align:center;padding:60px 20px;color:var(--muted);font-family:var(--mono);font-size:13px}

footer{display:flex;align-items:center;justify-content:center;gap:16px;font-size:11px;color:var(--muted);font-family:var(--mono);padding:16px 32px;border-top:1px solid var(--border)}
.sse-status{display:flex;align-items:center;gap:6px}
.sse-dot{width:6px;height:6px;border-radius:50%;background:var(--green)}
.sse-dot.disconnected{background:var(--red)}
@media (max-width: 980px){
  .grid3,.grid2,.control-grid,.repo-status-grid,.repo-filter-stack{grid-template-columns:1fr}
  .feed-row{flex-wrap:wrap;align-items:flex-start}
  .feed-pr,.feed-actor,.feed-path,.feed-ts{min-width:auto}
}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">&#x2B21; Codex of Critique</div>
    <div class="subrepo" id="repo">connecting&#x2026;</div>
  </div>
  <div class="header-right">
    <div class="live-chip clr-muted" id="live-chip"><span class="live-dot"></span><span id="active-ago">&#x2014;</span></div>
    <div class="badge idle" id="badge">
      <div class="dot" id="dot"></div>
      <span id="badge-txt">&#x2014;</span>
    </div>
  </div>
</header>

<div class="tab-bar">
  <div class="tab active" data-tab="dashboard">Dashboard</div>
  <div class="tab" data-tab="search">Search</div>
  <div class="tab" data-tab="activity">Activity</div>
</div>

<!-- ==================== DASHBOARD TAB ==================== -->
<div class="tab-page active" id="page-dashboard"><div class="app-wrap">

<div class="section">
  <div class="section-title">Sync Control</div>
  <div class="phase-card" style="margin-bottom:12px">
    <div class="control-grid">
      <div style="display:flex;flex-direction:column;gap:12px">
        <details class="repo-picker">
          <summary><span id="sync-summary">All configured repositories</span></summary>
          <div class="repo-picker-body">
            <div class="repo-picker-actions">
              <button type="button" onclick="setRepoSelection('sync', true)">Select all</button>
              <button type="button" onclick="setRepoSelection('sync', false)">Clear</button>
            </div>
            <div class="repo-picker-list" id="sync-list"></div>
          </div>
        </details>
        <div class="repo-hint" id="sync-hint">Loading repository options…</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:12px">
        <div style="font-size:12px;color:var(--muted);font-family:var(--mono)">Schedule: <span style="color:var(--text)" id="i-cron">&#x2014;</span></div>
        <div style="font-size:12px;color:var(--muted);font-family:var(--mono)">Last sync: <span class="ok" id="i-ok">&#x2014;</span></div>
        <div style="font-size:12px;color:var(--muted);font-family:var(--mono)">Next sync: <span style="color:var(--cyan)" id="i-next">&#x2014;</span></div>
        <div style="font-size:12px;color:var(--muted);font-family:var(--mono)">Cursor: <span style="color:var(--text)" id="i-cursor">&#x2014;</span></div>
        <button id="btn-sync" onclick="triggerSync()" class="btn-search" style="font-size:13px;padding:10px 28px;align-self:flex-start">&#x25B6; Run Sync Now</button>
      </div>
    </div>
    <div id="sync-progress" style="display:none;margin-top:14px">
      <div style="display:flex;justify-content:space-between;font-size:11px;font-family:var(--mono);color:var(--muted);margin-bottom:6px">
        <span id="sp-label">Processing PRs&#x2026;</span>
        <span id="sp-count">0 / 0</span>
      </div>
      <div class="track"><div class="fill blue active-shimmer" id="sp-bar" style="width:0%"></div></div>
      <div style="font-size:10px;font-family:var(--mono);color:var(--muted);margin-top:4px">Current: <span style="color:var(--cyan)" id="sp-current">&#x2014;</span></div>
    </div>
  </div>
  <div class="grid2">
    <div class="info-card"><div class="info-label">Last error</div><div class="info-value err" id="i-err">&#x2014;</div></div>
    <div class="info-card"><div class="info-label">GitHub &#x2014; Total PRs across repos</div><div class="info-value" id="i-gh">&#x2014;</div></div>
  </div>
  <div class="err-box" id="err-box"></div>
</div>

<div class="section">
  <div class="section-title">Sync Progress</div>
  <div class="phase-card">
    <div class="phase-header">
      <div class="phase-left">
        <svg class="spinner" id="spinner" viewBox="0 0 24 24" fill="none" stroke="var(--cyan)" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10" stroke-opacity=".2"/><path d="M22 12a10 10 0 01-10 10"/></svg>
        <span class="phase-name" id="phase-name">&#x2014;</span>
      </div>
      <span class="phase-tag" id="phase-tag">&#x2014;</span>
    </div>
    <div class="throughput" id="throughput"></div>
    <div class="bars">
      <div class="bar-row">
        <label>Pull Requests <span id="pr-txt">&#x2014;</span></label>
        <div class="track"><div class="fill blue" id="pr-bar" style="width:0%"></div></div>
      </div>
      <div class="bar-row">
        <label>Review Comments <span id="rc-txt">&#x2014;</span></label>
        <div class="track"><div class="fill green" id="rc-bar" style="width:0%"></div></div>
      </div>
      <div class="bar-row">
        <label>Code Snippets <span id="cs-txt">&#x2014;</span></label>
        <div class="track"><div class="fill green" id="cs-bar" style="width:0%"></div></div>
      </div>
    </div>
  </div>
</div>

<div class="section">
  <div class="section-title">Database</div>
  <div class="grid3">
    <div class="card" id="card-repo"><div class="card-icon">&#x1F3DB;&#xFE0F;</div><div class="card-count" id="c-repo">&#x2014;</div><div class="card-label">Repositories</div><div class="card-delta" id="d-repo"></div></div>
    <div class="card" id="card-pr"><div class="card-icon">&#x1F500;</div><div class="card-count" id="c-pr">&#x2014;</div><div class="card-label">Pull Requests</div><div class="card-delta" id="d-pr"></div></div>
    <div class="card" id="card-rt"><div class="card-icon">&#x1F9F5;</div><div class="card-count" id="c-rt">&#x2014;</div><div class="card-label">Review Threads</div><div class="card-delta" id="d-rt"></div></div>
    <div class="card" id="card-rc"><div class="card-icon">&#x1F4AC;</div><div class="card-count" id="c-rc">&#x2014;</div><div class="card-label">Review Comments</div><div class="card-delta" id="d-rc"></div></div>
    <div class="card" id="card-ca"><div class="card-icon">&#x1F50E;</div><div class="card-count" id="c-ca">&#x2014;</div><div class="card-label">Code Authorship</div><div class="card-delta" id="d-ca"></div></div>
    <div class="card" id="card-cs"><div class="card-icon">&#x1F4C4;</div><div class="card-count" id="c-cs">&#x2014;</div><div class="card-label">Code Snippets</div><div class="card-delta" id="d-cs"></div></div>
  </div>
</div>

<div class="section">
  <div class="section-title">Recent Activity</div>
  <div class="feed" id="feed"><div class="feed-row"><span style="color:var(--muted);font-size:12px">Loading&#x2026;</span></div></div>
</div>

<div class="section">
  <div class="section-title">Repository Status</div>
  <div class="repo-status-grid" id="repo-status-grid">
    <div class="info-card"><div class="info-value dim">Waiting for repository data…</div></div>
  </div>
</div>

<div class="section">
  <div class="section-title" style="cursor:pointer;user-select:none" onclick="$('info-guide').style.display=$('info-guide').style.display==='none'?'block':'none'">&#x2139;&#xFE0F; How This Works <span style="font-size:8px;vertical-align:middle;color:var(--cyan)">(click)</span></div>
  <div id="info-guide" class="phase-card" style="display:none;font-size:12px;line-height:1.8;font-family:var(--mono);color:var(--muted)">
    <p style="color:var(--text);margin-bottom:12px">Codex of Critique syncs data from GitHub into a local database. It does <b>NOT</b> query GitHub in real-time &#x2014; everything you see comes from the local DB.</p>
    <p style="color:var(--cyan);font-weight:700;margin-bottom:4px">SYNC PROCESS</p>
    <p>&#x2022; Runs automatically on a cron schedule (default: every 15 min)<br>&#x2022; Fetches PRs updated since last sync<br>&#x2022; For each PR: reviews, comments, review requests, code threads, blame, snippets<br>&#x2022; Can be triggered manually with "Run Sync Now"</p>
    <p style="color:var(--cyan);font-weight:700;margin:12px 0 4px">ACTIVITY TAB</p>
    <p>&#x2022; <b>Pending Reviews</b> &#x2014; PRs requesting your review<br>&#x2022; <b>Needs Action</b> &#x2014; changes requested, no commits since<br>&#x2022; <b>Not Re-requested</b> &#x2014; commits pushed but review not re-requested<br>&#x2022; <b>Addressed</b> &#x2014; re-review pending<br>&#x2022; <b>Merged</b> &#x2014; your merged PRs<br>&#x2022; <b>Comments</b> &#x2014; recent PR conversation</p>
    <p style="color:var(--cyan);font-weight:700;margin:12px 0 4px">DATA FRESHNESS</p>
    <p>Data is as fresh as the last sync. Use "Run Sync Now" to get the latest.</p>
  </div>
</div>

</div></div>

<!-- ==================== SEARCH TAB ==================== -->
<div class="tab-page" id="page-search"><div class="app-wrap">

<div class="search-bar" id="search-bar">
  <div class="field">
    <label>Include Repositories</label>
    <details class="repo-picker">
      <summary><span id="search-include-summary">All repositories</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="setRepoSelection('search-include', true)">Select all</button>
          <button type="button" onclick="setRepoSelection('search-include', false)">Clear</button>
        </div>
        <div class="repo-picker-list" id="search-include-list"></div>
      </div>
    </details>
  </div>
  <div class="field">
    <label>PR Authors</label>
    <details class="repo-picker">
      <summary><span id="search-pr-author-include-summary">All PR authors</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="setValueSelection('search-pr-author-include', true)">Select all</button>
          <button type="button" onclick="setValueSelection('search-pr-author-include', false)">Clear</button>
        </div>
        <div class="repo-picker-list" id="search-pr-author-include-list"></div>
      </div>
    </details>
  </div>
  <div class="field">
    <label>Exclude PR Authors</label>
    <details class="repo-picker">
      <summary><span id="search-pr-author-exclude-summary">No exclusions</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="setValueSelection('search-pr-author-exclude', true)">Select all</button>
          <button type="button" onclick="setValueSelection('search-pr-author-exclude', false)">Clear</button>
        </div>
        <div class="repo-picker-list" id="search-pr-author-exclude-list"></div>
      </div>
    </details>
  </div>
  <div class="field">
    <label>Reviewers</label>
    <details class="repo-picker">
      <summary><span id="search-reviewer-include-summary">All reviewers</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="setValueSelection('search-reviewer-include', true)">Select all</button>
          <button type="button" onclick="setValueSelection('search-reviewer-include', false)">Clear</button>
        </div>
        <div class="repo-picker-list" id="search-reviewer-include-list"></div>
      </div>
    </details>
  </div>
  <div class="field">
    <label>Exclude Reviewers</label>
    <details class="repo-picker">
      <summary><span id="search-reviewer-exclude-summary">No exclusions</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="setValueSelection('search-reviewer-exclude', true)">Select all</button>
          <button type="button" onclick="setValueSelection('search-reviewer-exclude', false)">Clear</button>
        </div>
        <div class="repo-picker-list" id="search-reviewer-exclude-list"></div>
      </div>
    </details>
  </div>
  <div class="repo-filter-stack">
    <div class="field">
      <label>Comment text</label>
      <input type="text" id="f-comment" placeholder="Search in comment body&#x2026;">
    </div>
    <div class="field">
      <label>Snippet text</label>
      <input type="text" id="f-snippet" placeholder="Search in code snippets&#x2026;">
    </div>
  </div>
  <button class="btn-search" id="btn-search" onclick="doSearch(1)">Search</button>
</div>

<div class="search-meta" id="search-meta" style="display:none">
  <span id="search-info"></span>
  <div class="pagination">
    <button id="pg-prev" onclick="doSearch(searchPage-1)">&#x25C0; Prev</button>
    <button id="pg-next" onclick="doSearch(searchPage+1)">Next &#x25B6;</button>
  </div>
</div>

<div id="search-results">
  <div class="empty-state">Use the filters above to search review comments and code snippets.</div>
</div>

</div></div>

<!-- ==================== ACTIVITY TAB ==================== -->
<div class="tab-page" id="page-activity"><div class="app-wrap">

<div class="activity-bar">
  <div class="field">
    <label>GitHub Username</label>
    <select id="a-username" class="native-picker"><option value="">All users</option></select>
    <details class="repo-picker single-picker">
      <summary><span id="activity-username-summary">All users</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="resetSinglePicker('activity-username')">All users</button>
        </div>
        <div class="repo-picker-list" id="activity-username-list"></div>
      </div>
    </details>
  </div>
  <div class="field">
    <label>Category</label>
    <select id="a-category" class="native-picker">
      <option value="">All categories</option>
      <option value="pending_reviews">Pending Reviews</option>
      <option value="changes_not_addressed">Changes — Needs Action</option>
      <option value="changes_forgot_rerequest">Changes — Not Re-requested</option>
      <option value="changes_addressed">Changes — Addressed</option>
      <option value="changes_merged">Changes — Merged</option>
      <option value="comments">Recent Comments</option>
    </select>
    <details class="repo-picker single-picker">
      <summary><span id="activity-category-summary">All categories</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="resetSinglePicker('activity-category')">All categories</button>
        </div>
        <div class="repo-picker-list" id="activity-category-list"></div>
      </div>
    </details>
  </div>
  <div class="field">
    <label>Include Repositories</label>
    <details class="repo-picker">
      <summary><span id="activity-include-summary">All repositories</span></summary>
      <div class="repo-picker-body">
        <div class="repo-picker-actions">
          <button type="button" onclick="setRepoSelection('activity-include', true)">Select all</button>
          <button type="button" onclick="setRepoSelection('activity-include', false)">Clear</button>
        </div>
        <div class="repo-picker-list" id="activity-include-list"></div>
      </div>
    </details>
  </div>
  <button class="btn-search" id="btn-activity" onclick="loadActivity()">Refresh</button>
</div>

<div class="search-meta" id="act-meta" style="display:none">
  <span id="act-info"></span>
  <div class="pagination">
    <button id="act-prev" onclick="loadActivity(actPage-1)">&#x25C0; Prev</button>
    <button id="act-next" onclick="loadActivity(actPage+1)">Next &#x25B6;</button>
  </div>
</div>

<div id="activity-content">
  <div class="empty-state">Enter your GitHub username and click Refresh to see what needs your attention.</div>
</div>

</div></div>

<footer>
  <div class="sse-status"><div class="sse-dot" id="sse-dot"></div><span id="sse-txt">connecting&#x2026;</span></div>
  <span>&#xB7;</span>
  <span>Last update: <span id="ts" style="color:var(--cyan)">&#x2014;</span></span>
  <span>&#xB7;</span>
  <span id="update-count" style="color:var(--muted)">0 updates received</span>
</footer>

<script>
const fmt = n => n != null ? n.toLocaleString('en-US') : '\u2014';
const parseDate = s => {
  if (!s) return null;
  if (s instanceof Date) return Number.isNaN(s.getTime()) ? null : s;
  let normalized = s;
  if (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(s)) {
    normalized += 'Z';
  }
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
};
const fmtDate = s => {
  const d = parseDate(s);
  return d ? d.toLocaleString('en-US') : '\u2014';
};
const $ = id => document.getElementById(id);
const esc = s => {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
};
const relTime = s => {
  const d = parseDate(s);
  if (!d) return '\u2014';
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  const absSec = Math.abs(sec);
  if (absSec < 5) return 'just now';
  if (sec < 0) {
    if (absSec < 60) return 'in ' + absSec + 's';
    if (absSec < 3600) return 'in ' + Math.floor(absSec/60) + 'm';
    if (absSec < 86400) return 'in ' + Math.floor(absSec/3600) + 'h';
    return fmtDate(d);
  }
  if (sec < 60) return sec + 's ago';
  if (sec < 3600) return Math.floor(sec/60) + 'm ago';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
  return fmtDate(d);
};

const REPO_PICKERS = {
  'sync': { listId: 'sync-list', summaryId: 'sync-summary', allText: 'All configured repositories', noneText: 'No repositories selected', defaultChecked: true },
  'search-include': { listId: 'search-include-list', summaryId: 'search-include-summary', allText: 'All repositories', noneText: 'No repositories selected', defaultChecked: true },
  'activity-include': { listId: 'activity-include-list', summaryId: 'activity-include-summary', allText: 'All repositories', noneText: 'No repositories selected', defaultChecked: true },
};

const VALUE_PICKERS = {
  'search-pr-author-include': { listId: 'search-pr-author-include-list', summaryId: 'search-pr-author-include-summary', allText: 'All PR authors', noneText: 'No PR authors selected', defaultChecked: true, source: 'pr_authors' },
  'search-pr-author-exclude': { listId: 'search-pr-author-exclude-list', summaryId: 'search-pr-author-exclude-summary', allText: 'All PR authors selected for exclusion', noneText: 'No exclusions', defaultChecked: false, source: 'pr_authors' },
  'search-reviewer-include': { listId: 'search-reviewer-include-list', summaryId: 'search-reviewer-include-summary', allText: 'All reviewers', noneText: 'No reviewers selected', defaultChecked: true, source: 'reviewers' },
  'search-reviewer-exclude': { listId: 'search-reviewer-exclude-list', summaryId: 'search-reviewer-exclude-summary', allText: 'All reviewers selected for exclusion', noneText: 'No exclusions', defaultChecked: false, source: 'reviewers' },
};

const SINGLE_PICKERS = {
  'activity-username': { selectId: 'a-username', listId: 'activity-username-list', summaryId: 'activity-username-summary', emptyText: 'No users available' },
  'activity-category': { selectId: 'a-category', listId: 'activity-category-list', summaryId: 'activity-category-summary', emptyText: 'No categories available' },
};

let repositoryChoices = [];
let repositoryOptionsLoaded = false;
let manualSyncRunning = false;
let filterChoices = { pr_authors: [], reviewers: [] };

function normalizeRepoChoices(items) {
  const seen = new Set();
  return (items || []).map(item => {
    if (!item) return null;
    const owner = (item.owner || '').trim();
    const repo = (item.repo || '').trim();
    const id = (item.id || (owner && repo ? owner + '/' + repo : '')).trim();
    if (!id || seen.has(id)) return null;
    seen.add(id);
    return { owner, repo, id };
  }).filter(Boolean);
}

function hydrateRepositoryChoices(items) {
  const normalized = normalizeRepoChoices(items);
  const before = repositoryChoices.map(item => item.id).join(',');
  const after = normalized.map(item => item.id).join(',');
  if (before === after && repositoryOptionsLoaded) return;

  repositoryChoices = normalized;
  repositoryOptionsLoaded = true;

  Object.keys(REPO_PICKERS).forEach(renderRepoPicker);
  updateSyncHint();
  updateSyncButtonState();
}

function renderRepoPicker(prefix) {
  const cfg = REPO_PICKERS[prefix];
  const container = $(cfg.listId);
  if (!container) return;

  const previous = container.dataset.ready === 'true' ? new Set(getSelectedRepos(prefix)) : null;
  container.innerHTML = repositoryChoices.length
    ? repositoryChoices.map(item => {
        const checked = previous
          ? previous.has(item.id)
          : cfg.defaultChecked;
        return '<label class="repo-option' + (cfg.defaultChecked ? '' : ' dim') + '">' +
          '<input type="checkbox" data-repo-group="' + prefix + '" value="' + esc(item.id) + '"' + (checked ? ' checked' : '') + '>' +
          '<span>' + esc(item.id) + '</span>' +
        '</label>';
      }).join('')
    : '<div class="repo-hint">No repositories available.</div>';

  container.dataset.ready = 'true';
  container.querySelectorAll('input').forEach(input => {
    input.addEventListener('change', () => {
      updateRepoSummary(prefix);
      if (prefix === 'sync') updateSyncButtonState();
    });
  });
  updateRepoSummary(prefix);
}

function getSelectedRepos(prefix) {
  return Array.from(document.querySelectorAll('[data-repo-group="' + prefix + '"]'))
    .filter(input => input.checked)
    .map(input => input.value);
}

function setRepoSelection(prefix, checked) {
  document.querySelectorAll('[data-repo-group="' + prefix + '"]').forEach(input => {
    input.checked = checked;
  });
  updateRepoSummary(prefix);
  if (prefix === 'sync') updateSyncButtonState();
}

function updateRepoSummary(prefix) {
  const cfg = REPO_PICKERS[prefix];
  const summaryEl = $(cfg.summaryId);
  if (!summaryEl) return;

  const total = repositoryChoices.length;
  const selected = getSelectedRepos(prefix);
  let text = cfg.noneText;

  if (!total) {
    text = 'No repositories configured';
  } else if (selected.length === total) {
    text = cfg.allText + ' (' + total + ')';
  } else if (selected.length) {
    const preview = selected.slice(0, 2).join(', ');
    const suffix = selected.length > 2 ? ' +' + (selected.length - 2) : '';
    text = selected.length + ' selected: ' + preview + suffix;
  }

  summaryEl.textContent = text;
  if (prefix === 'sync') updateSyncHint();
}

function updateSyncHint() {
  const hint = $('sync-hint');
  if (!hint) return;

  if (!repositoryChoices.length) {
    hint.textContent = 'No repositories configured.';
    return;
  }

  const selected = getSelectedRepos('sync');
  hint.textContent = selected.length + ' of ' + repositoryChoices.length + ' repositories selected for the next sync.';
}

function updateSyncButtonState() {
  const btn = $('btn-sync');
  if (!btn || manualSyncRunning) return;

  const total = repositoryChoices.length;
  const selected = getSelectedRepos('sync');
  btn.disabled = total === 0 || selected.length === 0;
  if (!total) {
    btn.textContent = 'No repositories configured';
  } else if (!selected.length) {
    btn.textContent = 'Select repositories';
  } else if (selected.length === total) {
    btn.textContent = '\u25B6 Run Sync Now';
  } else {
    btn.textContent = '\u25B6 Run Selected Repos';
  }
}

function configuredRepoLabel(repositories) {
  if (!repositories || !repositories.length) return 'no repositories configured';
  if (repositories.length === 1) return repositories[0].id;
  const preview = repositories.slice(0, 3).map(item => item.id).join(', ');
  const suffix = repositories.length > 3 ? ' +' + (repositories.length - 3) : '';
  return repositories.length + ' repositories configured \u2022 ' + preview + suffix;
}

function repoStatusLabel(status) {
  const labels = {
    idle: 'Idle',
    synced: 'Synced',
    queued: 'Queued',
    starting: 'Starting',
    fetching_prs: 'Fetching PRs',
    processing_prs: 'Processing',
    complete: 'Complete',
    error: 'Error',
    excluded: 'Excluded',
  };
  return labels[status] || status;
}

function renderRepoStatuses(items) {
  const grid = $('repo-status-grid');
  if (!grid) return;

  if (!items || !items.length) {
    grid.innerHTML = '<div class="info-card"><div class="info-value dim">No repository status available.</div></div>';
    return;
  }

  grid.innerHTML = items.map(item => {
    let progress = 'No synced data';
    if (item.selected_for_sync && item.runtime_phase === 'fetching_prs') {
      progress = 'Fetching PR list';
    } else if (item.selected_for_sync && item.runtime_total_prs) {
      progress = item.runtime_processed_prs + ' / ' + item.runtime_total_prs + ' PRs';
    } else if (item.pull_requests || item.review_comments) {
      progress = fmt(item.pull_requests) + ' PRs \u2022 ' + fmt(item.review_comments) + ' comments';
    }

    const current = item.runtime_current_pr ? 'PR #' + item.runtime_current_pr : '\u2014';
    const lastSync = item.last_success_at ? relTime(item.last_success_at) : 'Never';
    const cursor = item.last_pr_updated_at ? fmtDate(item.last_pr_updated_at) : '\u2014';
    const errorText = item.runtime_error || item.last_error_message || '';
    const error = errorText ? esc(errorText.slice(0, 120)) : '\u2014';

    return '<div class="repo-status-card' + (item.status === 'excluded' ? ' excluded' : '') + '">' +
      '<div class="repo-status-top">' +
        '<div class="repo-status-name">' + esc(item.id) + '</div>' +
        '<span class="repo-status-badge state-' + esc(item.status) + '">' + esc(repoStatusLabel(item.status)) + '</span>' +
      '</div>' +
      '<div class="repo-status-lines">' +
        '<div class="repo-status-line"><span>Progress</span><strong>' + esc(progress) + '</strong></div>' +
        '<div class="repo-status-line"><span>Current PR</span><strong>' + esc(current) + '</strong></div>' +
        '<div class="repo-status-line"><span>Last sync</span><strong>' + esc(lastSync) + '</strong></div>' +
        '<div class="repo-status-line"><span>Cursor</span><strong>' + esc(cursor) + '</strong></div>' +
        '<div class="repo-status-line' + (errorText ? ' error' : '') + '"><span>Last error</span><strong>' + error + '</strong></div>' +
      '</div>' +
    '</div>';
  }).join('');
}

async function loadRepositoryOptions() {
  try {
    const d = await fetch('/api/repositories').then(r => r.json());
    hydrateRepositoryChoices(d.repositories || []);
  } catch {}
}

function normalizeFilterChoices(items) {
  const seen = new Set();
  return (items || []).map(item => String(item || '').trim()).filter(value => {
    if (!value || seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

function hydrateFilterChoices(source, items) {
  const normalized = normalizeFilterChoices(items);
  const before = (filterChoices[source] || []).join(',');
  const after = normalized.join(',');
  if (before === after) return;

  filterChoices[source] = normalized;
  Object.keys(VALUE_PICKERS)
    .filter(prefix => VALUE_PICKERS[prefix].source === source)
    .forEach(renderValuePicker);
}

function getSelectedValues(prefix) {
  return Array.from(document.querySelectorAll('[data-value-group="' + prefix + '"]'))
    .filter(input => input.checked)
    .map(input => input.value);
}

function renderValuePicker(prefix) {
  const cfg = VALUE_PICKERS[prefix];
  const container = $(cfg.listId);
  if (!container) return;

  const items = filterChoices[cfg.source] || [];
  const previous = container.dataset.ready === 'true' ? new Set(getSelectedValues(prefix)) : null;
  container.innerHTML = items.length
    ? items.map(value => {
        const checked = previous ? previous.has(value) : cfg.defaultChecked;
        return '<label class="repo-option' + (cfg.defaultChecked ? '' : ' dim') + '">' +
          '<input type="checkbox" data-value-group="' + prefix + '" value="' + esc(value) + '"' + (checked ? ' checked' : '') + '>' +
          '<span>' + esc(value) + '</span>' +
        '</label>';
      }).join('')
    : '<div class="repo-hint">No options available.</div>';

  container.dataset.ready = 'true';
  container.querySelectorAll('input').forEach(input => {
    input.addEventListener('change', () => updateValueSummary(prefix));
  });
  updateValueSummary(prefix);
}

function setValueSelection(prefix, checked) {
  document.querySelectorAll('[data-value-group="' + prefix + '"]').forEach(input => {
    input.checked = checked;
  });
  updateValueSummary(prefix);
}

function updateValueSummary(prefix) {
  const cfg = VALUE_PICKERS[prefix];
  const summaryEl = $(cfg.summaryId);
  if (!summaryEl) return;

  const items = filterChoices[cfg.source] || [];
  const selected = getSelectedValues(prefix);
  let text = cfg.noneText;

  if (!items.length) {
    text = 'No options available';
  } else if (selected.length === items.length) {
    text = cfg.allText + ' (' + items.length + ')';
  } else if (selected.length) {
    const preview = selected.slice(0, 2).join(', ');
    const suffix = selected.length > 2 ? ' +' + (selected.length - 2) : '';
    text = selected.length + ' selected: ' + preview + suffix;
  }

  summaryEl.textContent = text;
}

function renderSinglePicker(prefix) {
  const cfg = SINGLE_PICKERS[prefix];
  const select = $(cfg.selectId);
  const container = $(cfg.listId);
  if (!cfg || !select || !container) return;

  const options = Array.from(select.options);
  container.innerHTML = options.length
    ? options.map(option => {
        const checked = option.value === select.value;
        return '<label class="repo-option' + (checked ? '' : ' dim') + '">' +
          '<input type="checkbox" data-single-group="' + prefix + '" value="' + esc(option.value) + '"' + (checked ? ' checked' : '') + '>' +
          '<span>' + esc(option.textContent || option.label || option.value) + '</span>' +
        '</label>';
      }).join('')
    : '<div class="repo-hint">' + esc(cfg.emptyText) + '</div>';

  container.querySelectorAll('input').forEach(input => {
    input.addEventListener('change', () => {
      setSingleSelection(prefix, input.checked ? input.value : '');
    });
  });

  updateSingleSummary(prefix);
}

function setSingleSelection(prefix, value, closeAfterSelection = true) {
  const cfg = SINGLE_PICKERS[prefix];
  const select = cfg ? $(cfg.selectId) : null;
  const container = cfg ? $(cfg.listId) : null;
  if (!cfg || !select) return;

  const nextValue = Array.from(select.options).some(option => option.value === value) ? value : '';
  select.value = nextValue;
  updateSingleSummary(prefix);
  renderSinglePicker(prefix);

  if (closeAfterSelection && container) {
    const picker = container.closest('.repo-picker');
    if (picker) picker.removeAttribute('open');
  }
}

function resetSinglePicker(prefix) {
  setSingleSelection(prefix, '');
}

function updateSingleSummary(prefix) {
  const cfg = SINGLE_PICKERS[prefix];
  const select = $(cfg.selectId);
  const summaryEl = $(cfg.summaryId);
  if (!cfg || !select || !summaryEl) return;

  const selectedOption = select.options[select.selectedIndex];
  summaryEl.textContent = selectedOption
    ? (selectedOption.textContent || selectedOption.label || selectedOption.value)
    : cfg.emptyText;
}

function closeRepoPickers(exceptPicker) {
  document.querySelectorAll('.repo-picker[open]').forEach(picker => {
    if (picker !== exceptPicker) picker.removeAttribute('open');
  });
}

function layoutRepoPicker(picker) {
  const summary = picker?.querySelector('summary');
  const panel = picker?.querySelector('.repo-picker-body');
  if (!picker || !summary || !panel) return;

  if (!picker.open) {
    picker.style.removeProperty('--picker-max-height');
    return;
  }

  const panelGap = 8;
  const viewportMargin = 16;
  const rect = summary.getBoundingClientRect();
  const availableBelow = Math.max(
    0,
    Math.floor(window.innerHeight - rect.bottom - panelGap - viewportMargin)
  );

  picker.style.setProperty('--picker-max-height', availableBelow + 'px');
}

function layoutOpenRepoPickers() {
  document.querySelectorAll('.repo-picker[open]').forEach(layoutRepoPicker);
}

document.querySelectorAll('.repo-picker').forEach(picker => {
  picker.addEventListener('toggle', () => {
    if (picker.open) {
      closeRepoPickers(picker);
      layoutRepoPicker(picker);
    } else {
      layoutRepoPicker(picker);
    }
  });
});

document.addEventListener('click', event => {
  if (event.target.closest('.repo-picker')) return;
  closeRepoPickers();
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeRepoPickers();
});

window.addEventListener('resize', layoutOpenRepoPickers);
window.addEventListener('scroll', layoutOpenRepoPickers, true);

Object.keys(SINGLE_PICKERS).forEach(renderSinglePicker);

/* ---- TABS ---- */
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-page').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('page-' + t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'search' && !filtersLoaded) loadFilters();
    if (t.dataset.tab === 'activity') { if (!filtersLoaded) loadFilters(); }
  });
});

/* ---- DASHBOARD ---- */
let prev = null, maxRc = 0, maxCs = 0, updateCount = 0;
let activeSecs = 9999, lastTs = null;
let prevTotals = [], throughputVal = 0;

setInterval(() => {
  if (lastTs) activeSecs = Math.round((Date.now() - lastTs) / 1000);
  const el = $('active-ago');
  const chip = $('live-chip');
  if (activeSecs < 10) {
    el.textContent = 'active now';
    chip.className = 'live-chip clr-green';
  } else if (activeSecs < 30) {
    el.textContent = 'active ' + activeSecs + 's ago';
    chip.className = 'live-chip clr-green';
  } else if (activeSecs < 120) {
    el.textContent = 'active ' + activeSecs + 's ago';
    chip.className = 'live-chip clr-yellow';
  } else {
    el.textContent = 'idle';
    chip.className = 'live-chip clr-muted';
  }
}, 1000);

function animateCount(el) {
  el.classList.add('bump');
  setTimeout(() => el.classList.remove('bump'), 800);
}
function flashCard(id) {
  const c = $(id);
  c.classList.add('flash');
  setTimeout(() => c.classList.remove('flash'), 1200);
}

function calcThroughput(t) {
  const now = Date.now();
  const total = t.pull_requests + t.review_comments + t.code_snippets + t.code_authorship + t.review_threads;
  prevTotals.push({ total, ts: now });
  prevTotals = prevTotals.filter(p => now - p.ts < 60000);
  if (prevTotals.length < 2) return 0;
  const oldest = prevTotals[0];
  const elapsed = (now - oldest.ts) / 1000;
  if (elapsed < 2) return throughputVal;
  throughputVal = Math.round((total - oldest.total) / elapsed * 60);
  return throughputVal;
}

function update(d) {
  updateCount++;
  $('update-count').textContent = updateCount + ' updates received';
  $('ts').textContent = new Date().toLocaleTimeString('en-US');

  const { tables: t, sync, github: gh, progress: p, activity, repo_statuses } = d;
  manualSyncRunning = sync.manual_running;
  hydrateRepositoryChoices(gh.repositories || []);

  if (p.active_secs_ago < activeSecs) {
    activeSecs = p.active_secs_ago;
    lastTs = Date.now() - activeSecs * 1000;
  }

  $('repo').textContent = configuredRepoLabel(gh.repositories || []);

  const isActive = p.is_active;
  $('badge').className = 'badge ' + (isActive ? 'active' : 'idle');
  $('dot').className = 'dot' + (isActive ? ' pulse' : '');
  $('badge-txt').textContent = isActive ? 'Syncing' : p.phase === 'idle' ? 'Complete' : 'Idle';

  $('phase-name').textContent = p.phase_label;
  $('phase-tag').textContent = p.phase;
  $('spinner').className = 'spinner' + (isActive ? ' on' : '');

  const rpm = calcThroughput(t);
  if (isActive && rpm > 0) {
    $('throughput').innerHTML = '<span>' + fmt(rpm) + '</span> records/min';
  } else if (isActive) {
    $('throughput').textContent = 'Processing\u2026';
  } else {
    $('throughput').textContent = '';
  }

  const prPct = p.pr_pct ?? 100;
  const prBar = $('pr-bar');
  prBar.style.width = prPct + '%';
  prBar.className = 'fill blue' + (isActive && p.phase === 'fetching_prs' ? ' active-shimmer' : '');
  $('pr-txt').textContent = gh.total_prs
    ? fmt(t.pull_requests) + ' / ' + fmt(gh.total_prs) + '  (' + prPct + '%)'
    : fmt(t.pull_requests);

  maxRc = Math.max(maxRc, t.review_comments);
  const rcPct = maxRc > 0 && p.phase !== 'fetching_prs' ? Math.min(100, Math.round(t.review_comments / maxRc * 100)) : 0;
  const rcBar = $('rc-bar');
  rcBar.style.width = rcPct + '%';
  rcBar.className = 'fill green' + (isActive && p.phase === 'fetching_threads' ? ' active-shimmer' : '');
  $('rc-txt').textContent = fmt(t.review_comments);

  maxCs = Math.max(maxCs, t.code_snippets);
  const csPct = maxCs > 0 && p.phase !== 'fetching_prs' ? Math.min(100, Math.round(t.code_snippets / maxCs * 100)) : 0;
  $('cs-bar').style.width = csPct + '%';
  $('cs-txt').textContent = fmt(t.code_snippets);

  const map = [
    ['c-repo','d-repo','card-repo','repositories'],
    ['c-pr','d-pr','card-pr','pull_requests'],
    ['c-rt','d-rt','card-rt','review_threads'],
    ['c-rc','d-rc','card-rc','review_comments'],
    ['c-ca','d-ca','card-ca','code_authorship'],
    ['c-cs','d-cs','card-cs','code_snippets'],
  ];
  map.forEach(([cId, dId, cardId, key]) => {
    const el = $(cId);
    const newV = t[key], oldV = prev ? prev[key] : newV;
    if (prev && newV !== oldV) { animateCount(el); flashCard(cardId); }
    el.textContent = fmt(newV);
    const diff = prev ? newV - oldV : 0;
    $(dId).textContent = diff > 0 ? '+' + fmt(diff) + ' new' : '';
  });

  $('i-cron').textContent   = sync.cron || '\u2014';
  $('i-ok').textContent     = sync.last_success_at ? relTime(sync.last_success_at) : 'Never';
  $('i-cursor').textContent = fmtDate(sync.last_pr_updated_at);
  $('i-next').textContent   = sync.next_sync_at ? relTime(sync.next_sync_at) : '\u2014';
  $('i-err').textContent    = sync.last_error_at ? fmtDate(sync.last_error_at) : 'None';
  $('i-gh').textContent     = fmt(gh.total_prs);

  const syncBtn = $('btn-sync');
  const spDiv = $('sync-progress');
  if (sync.manual_running) {
    syncBtn.disabled = true;
    syncBtn.textContent = '\u23F3 Syncing\u2026';
    const phase = sync.progress_phase;
    const total = sync.progress_total || 0;
    const done = sync.progress_done || 0;
    const currentRepo = sync.progress_current_repo || '';
    const bar = $('sp-bar');
    if (phase === 'fetching_prs') {
      spDiv.style.display = 'block';
      $('sp-label').textContent = currentRepo ? 'Fetching pull requests \u2022 ' + currentRepo : 'Fetching pull requests\u2026';
      $('sp-count').textContent = '';
      bar.style.width = '30%';
      bar.className = 'fill blue active-shimmer';
      $('sp-current').textContent = currentRepo || 'querying GitHub';
    } else if (phase === 'processing_prs' && total > 0) {
      spDiv.style.display = 'block';
      const pct = Math.min(100, Math.round(done / total * 100));
      $('sp-label').textContent = currentRepo ? 'Processing PRs \u2022 ' + currentRepo : 'Processing PRs\u2026';
      $('sp-count').textContent = done + ' / ' + total + ' (' + pct + '%)';
      bar.style.width = Math.max(2, pct) + '%';
      bar.className = 'fill green' + (pct < 100 ? ' active-shimmer' : '');
      $('sp-current').textContent = sync.progress_current_pr ? currentRepo + ' \u2022 PR #' + sync.progress_current_pr : (currentRepo || '\u2014');
    } else {
      spDiv.style.display = 'block';
      $('sp-label').textContent = phase === 'starting' ? 'Starting\u2026' : 'Working\u2026';
      $('sp-count').textContent = '';
      bar.style.width = '10%';
      bar.className = 'fill blue active-shimmer';
      $('sp-current').textContent = '\u2014';
    }
  } else {
    spDiv.style.display = 'none';
    updateSyncButtonState();
  }

  const box = $('err-box');
  box.style.display = sync.last_error_message ? 'block' : 'none';
  if (sync.last_error_message) box.textContent = sync.last_error_message;

  if (activity && activity.length) {
    $('feed').innerHTML = activity.map(a =>
      '<div class="feed-row">' +
        '<span class="feed-pr">' + esc(a.repo_owner + '/' + a.repo_name) + ' #' + a.pr_number + '</span>' +
        '<span class="feed-actor">' + esc(a.actor || '\u2014') + '</span>' +
        '<span class="feed-path">' + esc(a.path || '\u2014') + '</span>' +
        '<span class="feed-ts">' + relTime(a.ts) + '</span>' +
      '</div>').join('');
  }

  renderRepoStatuses(repo_statuses);

  prev = Object.assign({}, t);
}

async function triggerSync() {
  const btn = $('btn-sync');
  const selected = getSelectedRepos('sync');
  if (!selected.length) {
    btn.disabled = false;
    btn.textContent = 'Select repositories';
    $('err-box').style.display = 'block';
    $('err-box').textContent = 'Select at least one repository before starting a sync.';
    return;
  }

  btn.disabled = true; btn.textContent = '\u23F3 Starting\u2026';
  try {
    const params = new URLSearchParams();
    if (selected.length !== repositoryChoices.length) params.set('repositories', selected.join(','));
    const url = '/api/sync/trigger' + (params.toString() ? '?' + params.toString() : '');
    const r = await fetch(url, { method: 'POST' }).then(r => r.json());
    if (r.status === 'already_running') {
      btn.textContent = '\u23F3 Already running\u2026';
    } else if (r.status === 'error') {
      $('err-box').style.display = 'block';
      $('err-box').textContent = r.message || 'Sync could not be started.';
      updateSyncButtonState();
    } else {
      btn.textContent = '\u23F3 Syncing\u2026';
    }
  } catch (e) {
    updateSyncButtonState();
  }
}

function connect() {
  const es = new EventSource('/api/stream');
  es.onopen = () => {
    $('sse-dot').className = 'sse-dot';
    $('sse-txt').textContent = 'live stream connected';
  };
  es.onmessage = e => { try { update(JSON.parse(e.data)); } catch {} };
  es.onerror = () => {
    $('sse-dot').className = 'sse-dot disconnected';
    $('sse-txt').textContent = 'reconnecting\u2026';
    es.close();
    setTimeout(connect, 3000);
  };
}
connect();
loadRepositoryOptions();

/* ---- SEARCH ---- */
let filtersLoaded = false, searchPage = 1;

async function loadFilters() {
  try {
    await loadRepositoryOptions();
    const d = await fetch('/api/filters').then(r => r.json());
    hydrateFilterChoices('pr_authors', d.pr_authors);
    hydrateFilterChoices('reviewers', d.reviewers);
    fillSelect('a-username', d.pr_authors);
    renderSinglePicker('activity-username');
    filtersLoaded = true;
  } catch {}
}
function fillSelect(id, items) {
  const sel = $(id);
  if (!sel) return;

  const previousValue = sel.value;
  const firstOption = sel.firstElementChild ? sel.firstElementChild.cloneNode(true) : null;
  sel.innerHTML = '';
  if (firstOption) sel.appendChild(firstOption);

  normalizeFilterChoices(items).forEach(v => {
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    sel.appendChild(o);
  });

  if (previousValue && Array.from(sel.options).some(option => option.value === previousValue)) {
    sel.value = previousValue;
  }
}

$('f-comment').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(1); });
$('f-snippet').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(1); });

/* ---- ACTIVITY ---- */
let activityLoaded = false, actPage = 1;
const ACT_ALL_LIMIT = 10, ACT_PAGE_LIMIT = 50;

const ACT_SECTIONS = [
  { key: 'pending_reviews',          cat: 'pending_reviews',          title: 'Pending Reviews' },
  { key: 'changes_not_addressed',    cat: 'changes_not_addressed',    title: 'Changes \u2014 Needs Action' },
  { key: 'changes_forgot_rerequest', cat: 'changes_forgot_rerequest', title: 'Changes \u2014 Not Re-requested' },
  { key: 'changes_addressed',        cat: 'changes_addressed',        title: 'Changes \u2014 Addressed' },
  { key: 'changes_merged',           cat: 'changes_merged',           title: 'Merged PRs' },
  { key: 'comments',                 cat: 'comments',                 title: 'Recent Comments' },
];

function ghUrl(owner, repo, number) {
  return 'https://github.com/' + owner + '/' + repo + '/pull/' + number;
}

function selectCategory(cat) {
  $('a-category').value = cat;
  updateSingleSummary('activity-category');
  renderSinglePicker('activity-category');
  loadActivity(1);
}

async function loadActivity(page) {
  actPage = page || 1;
  const user = $('a-username').value;
  const btn = $('btn-activity');
  btn.disabled = true; btn.textContent = 'Loading\u2026';

  const cat = $('a-category').value;
  const perPage = cat ? ACT_PAGE_LIMIT : ACT_ALL_LIMIT;
  const params = new URLSearchParams({ per_page: perPage, page: actPage });
  const includeRepos = getSelectedRepos('activity-include');
  if (repositoryChoices.length && !includeRepos.length) {
    $('activity-content').innerHTML = '<div class="empty-state">Select at least one repository to load activity.</div>';
    $('act-meta').style.display = 'none';
    btn.disabled = false; btn.textContent = 'Refresh';
    return;
  }
  if (user) params.set('username', user);
  if (cat) params.set('category', cat);
  if (repositoryChoices.length && includeRepos.length !== repositoryChoices.length) {
    params.set('repositories', includeRepos.join(','));
  }

  try {
    const d = await fetch('/api/activity?' + params).then(r => r.json());
    if (d.error || d.detail) {
      const message = d.error || d.detail;
      $('activity-content').innerHTML = '<div class="empty-state">' + esc(message) + '</div>';
      $('act-meta').style.display = 'none';
    } else {
      renderActivity(d);
      activityLoaded = true;
    }
  } catch (e) {
    $('activity-content').innerHTML = '<div class="empty-state">Error: ' + esc(e.message) + '</div>';
    $('act-meta').style.display = 'none';
  }
  btn.disabled = false; btn.textContent = 'Refresh';
}

function renderActivityCard(r, cat) {
  if (cat === 'comments') {
    return '<a class="comment-card" href="' + ghUrl(r.repo_owner, r.repo_name, r.pr_number) + '" target="_blank">' +
      '<div class="cc-head">' +
        '<span class="cc-author">@' + esc(r.author_login || '\u2014') + '</span>' +
        '<span class="cc-pr">#' + r.pr_number + '</span>' +
        '<span style="font-size:10px;color:var(--muted)">' + esc(r.repo_owner + '/' + r.repo_name) + '</span>' +
        '<span class="cc-time">' + relTime(r.ts) + '</span>' +
      '</div>' +
      '<div class="cc-body">' + esc((r.body || '').substring(0, 200)) + '</div>' +
    '</a>';
  }
  let style = '', meta = '';
  if (cat === 'changes_forgot_rerequest') {
    style = ' style="border-color:#f59e0b40"';
    meta = '<span class="ac-meta" style="color:var(--yellow)">@' + esc(r.pr_author || r.reviewer || '') + ' \u2192 @' + esc(r.reviewer || '') + ' \u2014 committed ' + relTime(r.last_commit_at) + '</span>';
  } else if (cat === 'changes_addressed') {
    style = ' style="border-color:#10b98140"';
    meta = '<span class="ac-meta" style="color:var(--green)">@' + esc(r.reviewer || '') + ' re-review pending</span>';
  } else if (cat === 'changes_merged') {
    style = ' style="opacity:.7"';
    meta = '<span class="ac-meta" style="color:var(--muted)">merged ' + relTime(r.merged_at_github) + '</span>';
  } else if (cat === 'changes_not_addressed') {
    meta = '<span class="ac-meta">@' + esc(r.reviewer || '') + ' \u2014 ' + relTime(r.review_date) + '</span>';
  } else {
    meta = '<span class="ac-meta">' + relTime(r.updated_at_github) + '</span>';
  }
  return '<a class="activity-card"' + style + ' href="' + ghUrl(r.repo_owner, r.repo_name, r.pr_number) + '" target="_blank">' +
    '<span class="ac-repo">' + esc(r.repo_owner + '/' + r.repo_name) + '</span>' +
    '<span class="ac-pr">#' + r.pr_number + '</span>' +
    '<span class="ac-title">' + esc(r.pr_title) + '</span>' +
    meta +
  '</a>';
}

function renderActivity(d) {
  const s = d.sections;
  const activeCat = d.category;
  let html = '';

  for (const sec of ACT_SECTIONS) {
    const items = s[sec.key];
    if (items === undefined) continue;
    let footer = '';
    if (!activeCat && items.length === ACT_ALL_LIMIT) {
      footer = '<div style="padding:8px 18px"><button onclick="selectCategory(\'' + sec.cat + '\')" style="background:none;border:none;color:var(--cyan);font-family:var(--mono);font-size:11px;cursor:pointer;padding:0">Show all \u2192</button></div>';
    }
    if (!items.length) {
      html += '<div class="activity-section">' +
        '<div class="activity-section-header"><span class="activity-section-title">' + esc(sec.title) + '</span><span class="count-badge">0</span></div>' +
        '<div class="empty-state" style="padding:20px">None found.</div></div>';
    } else {
      html += '<div class="activity-section">' +
        '<div class="activity-section-header"><span class="activity-section-title">' + esc(sec.title) + '</span><span class="count-badge">' + items.length + '</span></div>' +
        items.map(r => renderActivityCard(r, sec.cat)).join('') +
        footer +
        '</div>';
    }
  }

  if (!html) html = '<div class="empty-state">No activity found.</div>';
  $('activity-content').innerHTML = html;

  // Pagination bar — only in single-category mode
  const meta = $('act-meta');
  if (activeCat && s.total !== undefined) {
    const total = s.total;
    const totalPages = Math.ceil(total / ACT_PAGE_LIMIT) || 1;
    meta.style.display = 'flex';
    $('act-info').textContent = fmt(total) + ' results \u2014 page ' + actPage + ' of ' + totalPages;
    $('act-prev').disabled = actPage <= 1;
    $('act-next').disabled = actPage >= totalPages;
  } else {
    meta.style.display = 'none';
  }
}

async function doSearch(page) {
  searchPage = page;
  const btn = $('btn-search');
  btn.disabled = true; btn.textContent = 'Searching\u2026';

  const params = new URLSearchParams();
  const includeRepos = getSelectedRepos('search-include');
  const includeAuthors = getSelectedValues('search-pr-author-include');
  const excludeAuthors = getSelectedValues('search-pr-author-exclude');
  const includeReviewers = getSelectedValues('search-reviewer-include');
  const excludeReviewers = getSelectedValues('search-reviewer-exclude');
  const cq = $('f-comment').value.trim();
  const sq = $('f-snippet').value.trim();
  if (repositoryChoices.length && !includeRepos.length) {
    $('search-meta').style.display = 'none';
    $('search-results').innerHTML = '<div class="empty-state">Select at least one repository to search.</div>';
    btn.disabled = false; btn.textContent = 'Search';
    return;
  }
  if (repositoryChoices.length && includeRepos.length !== repositoryChoices.length) {
    params.set('repositories', includeRepos.join(','));
  }
  if (filterChoices.pr_authors.length && includeAuthors.length !== filterChoices.pr_authors.length) {
    params.set('pr_authors', includeAuthors.join(','));
  }
  if (excludeAuthors.length) params.set('exclude_pr_authors', excludeAuthors.join(','));
  if (filterChoices.reviewers.length && includeReviewers.length !== filterChoices.reviewers.length) {
    params.set('reviewers', includeReviewers.join(','));
  }
  if (excludeReviewers.length) params.set('exclude_reviewers', excludeReviewers.join(','));
  if (cq) params.set('comment_q', cq);
  if (sq) params.set('snippet_q', sq);
  params.set('page', page);

  try {
    const d = await fetch('/api/search?' + params).then(r => r.json());
    if (d.error || d.detail) {
      $('search-meta').style.display = 'none';
      $('search-results').innerHTML = '<div class="empty-state">' + esc(d.error || d.detail) + '</div>';
    } else {
      renderResults(d);
    }
  } catch (e) {
    $('search-results').innerHTML = '<div class="empty-state">Error: ' + esc(e.message) + '</div>';
  }
  btn.disabled = false; btn.textContent = 'Search';
}

function renderDiff(hunk) {
  if (!hunk) return '';
  const lines = hunk.split('\n').map(l => {
    const cls = l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : l.startsWith('@@') ? 'hunk' : '';
    return '<div class="diff-line ' + cls + '">' + esc(l) + '</div>';
  }).join('');
  return '<div class="diff-block"><div class="diff-header">Diff hunk</div>' + lines + '</div>';
}

function renderSnippet(s) {
  const label = s.snippet_type === 'blob_excerpt' ? 'Blob excerpt' : s.snippet_type;
  const range = (s.start_line && s.end_line) ? 'L' + s.start_line + '-L' + s.end_line : '';
  return '<div class="snippet-block"><div class="snippet-header"><span>' + esc(label) + '</span><span>' + range + '</span></div><pre>' + esc(s.snippet_text) + '</pre></div>';
}

function renderResults(d) {
  const meta = $('search-meta');
  meta.style.display = 'flex';
  const totalPages = Math.ceil(d.total / d.per_page);
  $('search-info').textContent = fmt(d.total) + ' results \u2014 page ' + d.page + ' of ' + (totalPages || 1);
  $('pg-prev').disabled = d.page <= 1;
  $('pg-next').disabled = d.page >= totalPages;

  if (!d.results.length) {
    $('search-results').innerHTML = '<div class="empty-state">No results found.</div>';
    return;
  }

  $('search-results').innerHTML = d.results.map(r => {
    const initials = (r.comment_author_login || '?').substring(0, 2).toUpperCase();
    const lineInfo = r.line ? (r.start_line && r.start_line !== r.line ? 'L' + r.start_line + '-L' + r.line : 'L' + r.line) : '';

    let snippetsHtml = '';
    if (r.diff_hunk) snippetsHtml += renderDiff(r.diff_hunk);
    if (r.snippets) r.snippets.forEach(s => { snippetsHtml += renderSnippet(s); });

    return '<div class="rc-card">' +
      '<div class="rc-head">' +
        '<div class="rc-avatar">' + esc(initials) + '</div>' +
        '<span class="rc-author">' + esc(r.comment_author_login || '\u2014') + '</span>' +
        '<span class="rc-repo-badge">' + esc(r.repo_owner + '/' + r.repo_name) + '</span>' +
        '<span class="rc-pr-badge">#' + r.pr_number + '</span>' +
        '<span class="rc-pr-title">' + esc(r.pr_title) + '</span>' +
        '<span class="rc-time">' + relTime(r.comment_created_at) + '</span>' +
      '</div>' +
      (r.path ? '<div class="rc-path"><span class="icon">\uD83D\uDCC1</span> ' + esc(r.path) + (lineInfo ? '<span class="line-badge">' + lineInfo + '</span>' : '') + '</div>' : '') +
      '<div class="rc-body">' + esc(r.body || '') + '</div>' +
      snippetsHtml +
    '</div>';
  }).join('');
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return _HTML


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
