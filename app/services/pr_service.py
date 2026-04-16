import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient
from app.repos.pull_request_repo import upsert_pull_request

logger = logging.getLogger(__name__)

MERGEABLE_RETRY_DELAY_SECONDS = 3
MERGEABLE_MAX_RETRIES = 2


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _resolve_unknown_mergeables(
    session: Session,
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    repository_id: int,
    unknown_prs: list[dict[str, Any]],
) -> None:
    if not unknown_prs:
        return

    for attempt in range(1, MERGEABLE_MAX_RETRIES + 1):
        if not unknown_prs:
            break

        logger.info(
            "mergeable_retry",
            extra={"attempt": attempt, "count": len(unknown_prs), "owner": owner, "repo": repo_name},
        )
        time.sleep(MERGEABLE_RETRY_DELAY_SECONDS)

        still_unknown: list[dict[str, Any]] = []
        for pr_info in unknown_prs:
            try:
                data = gql.execute_query_file(
                    "pull_request_mergeable",
                    {"owner": owner, "name": repo_name, "number": pr_info["number"]},
                )
                mergeable = data["repository"]["pullRequest"]["mergeable"]
                if mergeable and mergeable != "UNKNOWN":
                    upsert_pull_request(
                        session=session,
                        repository_id=repository_id,
                        github_node_id=pr_info["github_node_id"],
                        number=pr_info["number"],
                        title=pr_info["title"],
                        author_login=pr_info["author_login"],
                        review_decision=pr_info["review_decision"],
                        state=pr_info["state"],
                        created_at_github=pr_info["created_at_github"],
                        updated_at_github=pr_info["updated_at_github"],
                        merged_at_github=pr_info["merged_at_github"],
                        raw_payload=pr_info["raw_payload"],
                        mergeable=mergeable,
                        mergeable_updated_at=datetime.utcnow(),
                    )
                    logger.info("mergeable_resolved", extra={"pr_number": pr_info["number"], "mergeable": mergeable})
                else:
                    still_unknown.append(pr_info)
            except Exception:
                logger.exception("mergeable_retry_error", extra={"pr_number": pr_info["number"]})
                still_unknown.append(pr_info)

        unknown_prs = still_unknown

    if unknown_prs:
        logger.info("mergeable_still_unknown", extra={"count": len(unknown_prs), "pr_numbers": [p["number"] for p in unknown_prs]})


def fetch_and_persist_prs(
    session: Session,
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    repository_id: int,
    since: datetime | None,
) -> list[dict[str, Any]]:
    """Page through all PRs updated since `since` and persist them.

    Returns a list of dicts with keys: db_id, number, github_node_id,
    updated_at_github, and the raw node payload.
    """
    persisted: list[dict[str, Any]] = []
    unknown_prs: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        data = gql.execute_query_file(
            "pull_requests_page",
            {"owner": owner, "name": repo_name, "after": cursor},
        )

        pr_conn = data["repository"]["pullRequests"]
        nodes = pr_conn["nodes"]
        page_info = pr_conn["pageInfo"]

        for node in nodes:
            updated = _parse_dt(node["updatedAt"])

            if since and updated and updated < since:
                continue

            author = node.get("author") or {}
            mergeable = node.get("mergeable")
            now = datetime.utcnow()

            db_id = upsert_pull_request(
                session=session,
                repository_id=repository_id,
                github_node_id=node["id"],
                number=node["number"],
                title=node["title"],
                author_login=author.get("login"),
                review_decision=node.get("reviewDecision"),
                state=node.get("state"),
                created_at_github=_parse_dt(node["createdAt"]),
                updated_at_github=updated,
                merged_at_github=_parse_dt(node.get("mergedAt")),
                raw_payload=node,
                mergeable=mergeable,
                mergeable_updated_at=now,
            )

            if mergeable == "UNKNOWN" and node.get("state") == "OPEN":
                unknown_prs.append({
                    "number": node["number"],
                    "github_node_id": node["id"],
                    "title": node["title"],
                    "author_login": author.get("login"),
                    "review_decision": node.get("reviewDecision"),
                    "state": node.get("state"),
                    "created_at_github": _parse_dt(node["createdAt"]),
                    "updated_at_github": updated,
                    "merged_at_github": _parse_dt(node.get("mergedAt")),
                    "raw_payload": node,
                })

            persisted.append({
                "db_id": db_id,
                "number": node["number"],
                "github_node_id": node["id"],
                "updated_at_github": updated,
                "node": node,
            })

        logger.info(
            "pr_page_processed",
            extra={"count": len(nodes), "persisted": len(persisted), "has_next": page_info["hasNextPage"]},
        )

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    _resolve_unknown_mergeables(
        session=session,
        gql=gql,
        owner=owner,
        repo_name=repo_name,
        repository_id=repository_id,
        unknown_prs=unknown_prs,
    )

    return persisted
