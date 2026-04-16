import logging
import time
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient, RateLimitExhausted

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 3
MAX_RETRIES = 2


def _fetch_mergeable_with_retry(
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    pr_number: int,
) -> str | None:
    """Fetch mergeable for a single PR, retrying UNKNOWN up to MAX_RETRIES times."""
    mergeable = None
    for attempt in range(MAX_RETRIES + 1):
        data = gql.execute_query_file(
            "pull_request_mergeable",
            {"owner": owner, "name": repo_name, "number": pr_number},
        )
        mergeable = data["repository"]["pullRequest"]["mergeable"]
        if mergeable != "UNKNOWN" or attempt == MAX_RETRIES:
            return mergeable
        logger.info(
            "backfill_retry_unknown",
            extra={"pr_number": pr_number, "attempt": attempt + 1},
        )
        time.sleep(RETRY_DELAY_SECONDS)
    return mergeable


def run_backfill(session: Session, gql: GitHubGraphQLClient) -> dict:
    """Backfill mergeable for all PRs where it is NULL.

    Returns a summary dict with counts.
    """
    rows = session.execute(text("""
        SELECT pr.id, pr.number, pr.state, r.owner, r.name AS repo_name
        FROM pull_requests pr
        JOIN repositories r ON r.id = pr.repository_id
        WHERE pr.mergeable IS NULL
        ORDER BY
            CASE WHEN pr.state = 'OPEN' THEN 0 ELSE 1 END,
            pr.updated_at_github DESC
    """)).fetchall()

    total = len(rows)
    updated = 0
    skipped = 0
    errors = 0

    logger.info("backfill_start", extra={"total": total})

    for row in rows:
        try:
            mergeable = _fetch_mergeable_with_retry(
                gql, row.owner, row.repo_name, row.number
            )
            if mergeable:
                session.execute(text("""
                    UPDATE pull_requests
                    SET mergeable = :mergeable, mergeable_updated_at = :now
                    WHERE id = :pr_id
                """), {
                    "mergeable": mergeable,
                    "now": datetime.utcnow(),
                    "pr_id": row.id,
                })
                session.commit()
                updated += 1
                logger.info(
                    "backfill_updated",
                    extra={
                        "pr_number": row.number,
                        "repo": f"{row.owner}/{row.repo_name}",
                        "mergeable": mergeable,
                        "progress": f"{updated + skipped + errors}/{total}",
                    },
                )
            else:
                skipped += 1
        except RateLimitExhausted:
            logger.warning("backfill_rate_limit_hit", extra={"updated_so_far": updated})
            break
        except Exception:
            session.rollback()
            logger.exception(
                "backfill_error",
                extra={"pr_number": row.number, "repo": f"{row.owner}/{row.repo_name}"},
            )
            errors += 1

    summary = {"total": total, "updated": updated, "skipped": skipped, "errors": errors}
    logger.info("backfill_complete", extra=summary)
    return summary
