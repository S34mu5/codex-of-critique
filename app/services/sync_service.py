import logging
from datetime import datetime, timedelta

from app.clients.github_graphql import GitHubGraphQLClient
from app.clients.github_rest import GitHubRESTClient
from app.config import settings
from app.db import get_session
from app.repos.repository_repo import get_or_create_repository
from app.repos.sync_state_repo import advance_cursor, get_sync_state, record_error
from app.services.blame_service import BlameCache, resolve_and_persist_blame
from app.services.pr_service import fetch_and_persist_prs
from app.services.review_thread_service import fetch_and_persist_threads
from app.services.pr_extras_service import fetch_and_persist_pr_extras
from app.services.snippet_service import (
    ContentCache,
    fetch_and_persist_blob_excerpt,
    persist_diff_hunk,
)

logger = logging.getLogger(__name__)

OVERLAP_HOURS = 6


def run_repository_sync() -> None:
    owner = settings.github_owner
    repo_name = settings.github_repo

    if not owner or not repo_name or not settings.github_token:
        logger.error("sync_skip", extra={"reason": "Missing GITHUB_OWNER, GITHUB_REPO, or GITHUB_TOKEN"})
        return

    gql = GitHubGraphQLClient()
    rest = GitHubRESTClient()
    blame_cache = BlameCache()
    content_cache = ContentCache()

    session = get_session()
    try:
        repository = get_or_create_repository(session, owner, repo_name)
        state = get_sync_state(session, repository.id)
        session.commit()

        effective_since = None
        if state.last_pr_updated_at:
            effective_since = state.last_pr_updated_at - timedelta(hours=OVERLAP_HOURS)

        logger.info(
            "sync_start",
            extra={"owner": owner, "repo": repo_name, "since": str(effective_since)},
        )

        prs = fetch_and_persist_prs(
            session=session,
            gql=gql,
            owner=owner,
            repo_name=repo_name,
            repository_id=repository.id,
            since=effective_since,
        )
        session.commit()

        logger.info("sync_prs_done", extra={"count": len(prs)})

        max_updated: datetime | None = None
        total_comments = 0

        for pr in prs:
            try:
                fetch_and_persist_pr_extras(
                    session=session,
                    gql=gql,
                    owner=owner,
                    repo_name=repo_name,
                    repository_id=repository.id,
                    pull_request_id=pr["db_id"],
                    pr_number=pr["number"],
                )
                session.commit()

                comments = fetch_and_persist_threads(
                    session=session,
                    gql=gql,
                    owner=owner,
                    repo_name=repo_name,
                    repository_id=repository.id,
                    pull_request_id=pr["db_id"],
                    pr_number=pr["number"],
                )
                session.commit()

                for c in comments:
                    resolve_and_persist_blame(
                        session=session,
                        gql=gql,
                        owner=owner,
                        repo=repo_name,
                        cache=blame_cache,
                        comment_db_id=c["comment_db_id"],
                        commit_oid=c.get("commit_oid"),
                        path=c.get("path"),
                        line=c.get("line"),
                    )

                    persist_diff_hunk(
                        session=session,
                        comment_db_id=c["comment_db_id"],
                        commit_oid=c.get("commit_oid"),
                        path=c.get("path", ""),
                        diff_hunk=c.get("diff_hunk"),
                    )

                    fetch_and_persist_blob_excerpt(
                        session=session,
                        rest=rest,
                        cache=content_cache,
                        owner=owner,
                        repo=repo_name,
                        comment_db_id=c["comment_db_id"],
                        commit_oid=c.get("commit_oid"),
                        path=c.get("path"),
                        line=c.get("line"),
                        start_line=c.get("start_line"),
                    )

                session.commit()
                total_comments += len(comments)

                pr_updated = pr.get("updated_at_github")
                if pr_updated and (max_updated is None or pr_updated > max_updated):
                    max_updated = pr_updated

            except Exception:
                session.rollback()
                logger.exception("sync_pr_error", extra={"pr_number": pr["number"]})
                continue

        if max_updated:
            state = get_sync_state(session, repository.id)
            advance_cursor(session, state, max_updated)
            session.commit()

        logger.info(
            "sync_complete",
            extra={
                "prs": len(prs),
                "comments": total_comments,
                "blame_cache_size": blame_cache.size,
                "cursor": str(max_updated),
            },
        )

    except Exception as exc:
        session.rollback()
        logger.exception("sync_fatal_error")
        try:
            repository = get_or_create_repository(session, owner, repo_name)
            state = get_sync_state(session, repository.id)
            record_error(session, state, str(exc))
            session.commit()
        except Exception:
            logger.exception("sync_error_recording_failed")
    finally:
        session.close()
        gql.close()
        rest.close()
