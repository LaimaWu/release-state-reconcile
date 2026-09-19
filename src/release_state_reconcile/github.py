from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class GitHubAPIError(RuntimeError):
    def __init__(self, status: int, url: str, message: str):
        super().__init__(f"GitHub API {status} for {url}: {message}")
        self.status = status
        self.url = url


class GitHubClient:
    """Small GET-only GitHub REST client.

    There are deliberately no mutation methods in this class. Authentication is
    optional and only expands the public REST rate limit.
    """

    api_root = "https://api.github.com"

    def __init__(self, token: str | None = None, timeout: int = 30):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.timeout = timeout
        self.requests: list[str] = []
        self._cache: dict[str, Any] = {}

    def _get(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        url = path if path.startswith("https://") else f"{self.api_root}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        if url in self._cache:
            return self._cache[url]
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "release-state-reconcile/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, method="GET", headers=headers)
        self.requests.append(url)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("message", body)
            except json.JSONDecodeError:
                message = body
            raise GitHubAPIError(exc.code, url, message) from exc
        self._cache[url] = payload
        return payload

    def issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._get(f"/repos/{owner}/{repo}/issues/{number}")

    def timeline(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        return self._get(
            f"/repos/{owner}/{repo}/issues/{number}/timeline", {"per_page": 100}
        )

    def comments(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        return self._get(
            f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 100}
        )

    def pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._get(f"/repos/{owner}/{repo}/pulls/{number}")

    def check_runs(self, owner: str, repo: str, sha: str) -> dict[str, Any]:
        return self._get(
            f"/repos/{owner}/{repo}/commits/{sha}/check-runs", {"per_page": 100}
        )

    def pull_files(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        return self._get(
            f"/repos/{owner}/{repo}/pulls/{number}/files", {"per_page": 100}
        )

    def branch(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(branch, safe="")
        return self._get(f"/repos/{owner}/{repo}/branches/{quoted}")

    def search_pulls(
        self, owner: str, repo: str, base: str, reference: str
    ) -> dict[str, Any]:
        query = f'repo:{owner}/{repo} is:pr base:"{base}" "{reference}"'
        return self._get("/search/issues", {"q": query, "per_page": 100})

    def releases(self, owner: str, repo: str) -> list[dict[str, Any]]:
        return self._get(f"/repos/{owner}/{repo}/releases", {"per_page": 100})

    def release_by_tag(self, owner: str, repo: str, tag: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(tag, safe="")
        return self._get(f"/repos/{owner}/{repo}/releases/tags/{quoted}")

    def compare(self, owner: str, repo: str, base: str, head: str) -> dict[str, Any]:
        quoted_base = urllib.parse.quote(base, safe="")
        quoted_head = urllib.parse.quote(head, safe="")
        return self._get(f"/repos/{owner}/{repo}/compare/{quoted_base}...{quoted_head}")
