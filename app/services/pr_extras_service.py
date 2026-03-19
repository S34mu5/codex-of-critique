import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient
from app.models.pull_request import PullRequest
from app.repos.pr_review_repo import upsert_pr_review
from app.repos.pr_comment_repo import upsert_pr_comment
from app.repos.review_request_repo import sync_review_requests

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def fetch_and_persist_pr_extras(
    session: Session,
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    repository_id: int,
    pull_request_id: int,
    pr_number: int,
) -> None:
    """Fetch reviews, PR comments, and review requests for a PR and persist them."""
    data = gql.execute_query_file(
        "pull_request_extras",
        {
            "owner": owner,
            "name": repo_name,
            "number": pr_number,
        },
    )

    pr_data = data["repository"]["pullRequest"]

    # --- Reviews ---
    review_nodes = pr_data.get("reviews", {}).get("nodes", [])
    for node in review_nodes:
        author = node.get("author") or {}
        upsert_pr_review(
            session=session,
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            github_node_id=node["id"],
            author_login=author.get("login"),
            state=node.get("state"),
            body=node.get("body"),
            submitted_at=_parse_dt(node.get("submittedAt")),
        )

    # --- PR comments (conversation tab) ---
    comment_nodes = pr_data.get("comments", {}).get("nodes", [])
    for node in comment_nodes:
        author = node.get("author") or {}
        upsert_pr_comment(
            session=session,
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            github_node_id=node["id"],
            github_database_id=node.get("databaseId"),
            author_login=author.get("login"),
            author_association=node.get("authorAssociation"),
            body=node.get("body"),
            comment_created_at=_parse_dt(node["createdAt"]),
            comment_edited_at=_parse_dt(node.get("lastEditedAt")),
        )

    # --- Last commit date ---
    commit_nodes = pr_data.get("commits", {}).get("nodes", [])
    if commit_nodes:
        last_commit_at = _parse_dt(commit_nodes[0]["commit"]["committedDate"])
        if last_commit_at:
            session.query(PullRequest).filter_by(id=pull_request_id).update(
                {"last_commit_at": last_commit_at}
            )

    # --- Review requests ---
    request_nodes = pr_data.get("reviewRequests", {}).get("nodes", [])
    sync_review_requests(
        session=session,
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        current_requests=request_nodes,
    )

    logger.info(
        "pr_extras_synced",
        extra={
            "pr": pr_number,
            "reviews": len(review_nodes),
            "comments": len(comment_nodes),
            "review_requests": len(request_nodes),
        },
    )