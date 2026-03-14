import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.clients.github_graphql import GitHubGraphQLClient
from app.repos.review_comment_repo import upsert_review_comment, upsert_review_thread
from app.utils.files import file_extension_from_path

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def fetch_and_persist_threads(
    session: Session,
    gql: GitHubGraphQLClient,
    owner: str,
    repo_name: str,
    repository_id: int,
    pull_request_id: int,
    pr_number: int,
) -> list[dict[str, Any]]:
    """Fetch all review threads + comments for a PR and persist them.

    Returns a flat list of persisted comment dicts with keys needed for
    downstream blame/snippet enrichment.
    """
    all_comments: list[dict[str, Any]] = []
    threads_cursor: str | None = None

    while True:
        data = gql.execute_query_file(
            "pull_request_threads",
            {
                "owner": owner,
                "name": repo_name,
                "number": pr_number,
                "after": threads_cursor,
            },
        )

        pr_data = data["repository"]["pullRequest"]
        thread_conn = pr_data["reviewThreads"]
        thread_nodes = thread_conn["nodes"]
        page_info = thread_conn["pageInfo"]

        for t_node in thread_nodes:
            thread_db_id = upsert_review_thread(
                session=session,
                repository_id=repository_id,
                pull_request_id=pull_request_id,
                github_node_id=t_node["id"],
                path=t_node.get("path"),
                line=t_node.get("line"),
                start_line=t_node.get("startLine"),
                original_line=t_node.get("originalLine"),
                original_start_line=t_node.get("originalStartLine"),
                diff_side=t_node.get("diffSide"),
                start_diff_side=t_node.get("startDiffSide"),
                is_resolved=t_node.get("isResolved", False),
                is_outdated=t_node.get("isOutdated", False),
                raw_payload=t_node,
            )

            comment_nodes = t_node.get("comments", {}).get("nodes", [])

            for c_node in comment_nodes:
                author = c_node.get("author") or {}
                review = c_node.get("pullRequestReview") or {}
                review_author = review.get("author") or {}
                commit_data = c_node.get("commit") or {}
                comment_path = c_node.get("path") or t_node.get("path") or ""

                comment_db_id = upsert_review_comment(
                    session=session,
                    repository_id=repository_id,
                    pull_request_id=pull_request_id,
                    review_thread_id=thread_db_id,
                    github_node_id=c_node["id"],
                    github_database_id=c_node.get("databaseId"),
                    review_node_id=review.get("id"),
                    review_state=review.get("state"),
                    review_author_login=review_author.get("login"),
                    review_submitted_at=_parse_dt(review.get("submittedAt")),
                    comment_author_login=author.get("login"),
                    author_association=c_node.get("authorAssociation"),
                    path=comment_path,
                    file_extension=file_extension_from_path(comment_path),
                    body=c_node.get("body", ""),
                    body_text=c_node.get("bodyText"),
                    diff_hunk=c_node.get("diffHunk"),
                    line=c_node.get("line"),
                    start_line=None,
                    original_line=c_node.get("originalLine"),
                    original_start_line=None,
                    comment_commit_oid=commit_data.get("oid"),
                    comment_created_at=_parse_dt(c_node["createdAt"]),
                    comment_edited_at=_parse_dt(c_node.get("lastEditedAt")),
                    raw_payload=c_node,
                )

                all_comments.append({
                    "comment_db_id": comment_db_id,
                    "path": comment_path,
                    "line": c_node.get("line"),
                    "start_line": t_node.get("startLine"),
                    "commit_oid": commit_data.get("oid"),
                    "diff_hunk": c_node.get("diffHunk"),
                })

            # Inner pagination for comments within a thread (>50 comments)
            comments_page = t_node.get("comments", {}).get("pageInfo", {})
            if comments_page.get("hasNextPage"):
                logger.warning(
                    "thread_has_more_comments",
                    extra={
                        "pr": pr_number,
                        "thread_id": t_node["id"],
                        "msg": "Thread has >50 comments; inner pagination not yet implemented",
                    },
                )

        logger.info(
            "threads_page_processed",
            extra={
                "pr": pr_number,
                "threads": len(thread_nodes),
                "comments": len(all_comments),
                "has_next": page_info["hasNextPage"],
            },
        )

        if not page_info["hasNextPage"]:
            break
        threads_cursor = page_info["endCursor"]

    return all_comments
