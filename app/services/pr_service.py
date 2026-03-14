import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient
from app.repos.pull_request_repo import upsert_pull_request

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


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
            )

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

    return persisted
