import logging
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger(__name__)

QUERIES_DIR = Path(__file__).resolve().parent.parent / "queries"


class RateLimitExhausted(Exception):
    pass


class GraphQLError(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(f"GraphQL errors: {errors}")


class GitHubGraphQLClient:
    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=settings.github_request_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        self._query_cache: dict[str, str] = {}

    def _load_query(self, name: str) -> str:
        if name not in self._query_cache:
            path = QUERIES_DIR / f"{name}.graphql"
            self._query_cache[name] = path.read_text(encoding="utf-8")
        return self._query_cache[name]

    @retry(
        stop=stop_after_attempt(settings.github_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(
            settings.github_graphql_url,
            json={"query": query, "variables": variables},
        )
        resp.raise_for_status()
        payload = resp.json()

        if "errors" in payload:
            raise GraphQLError(payload["errors"])

        data: dict[str, Any] = payload["data"]

        rate = data.get("rateLimit", {})
        remaining = rate.get("remaining")
        if remaining is not None:
            logger.info(
                "graphql_rate_limit",
                extra={"cost": rate.get("cost"), "remaining": remaining, "reset_at": rate.get("resetAt")},
            )
            if remaining < settings.github_min_remaining_budget:
                raise RateLimitExhausted(
                    f"Rate limit remaining ({remaining}) below budget ({settings.github_min_remaining_budget})"
                )

        return data

    def execute_query_file(self, name: str, variables: dict[str, Any]) -> dict[str, Any]:
        query = self._load_query(name)
        return self.execute(query, variables)

    def close(self) -> None:
        self._client.close()
