import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger(__name__)


class GitHubRESTClient:
    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=settings.github_request_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github.raw+json",
            },
        )

    @retry(
        stop=stop_after_attempt(settings.github_max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    def get_file_contents(self, owner: str, repo: str, path: str, ref: str) -> str:
        url = f"{settings.github_rest_url}/repos/{owner}/{repo}/contents/{path}"
        resp = self._client.get(url, params={"ref": ref})
        resp.raise_for_status()
        return resp.text

    def close(self) -> None:
        self._client.close()
