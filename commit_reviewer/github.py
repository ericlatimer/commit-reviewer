"""Minimal GitHub Issues client: one rolling issue for findings, one issue per high finding."""
import json
import os
import urllib.error
import urllib.request

ROLLING_TITLE = "Reviewer findings"
BASE_LABEL = "reviewer"
LABEL_COLORS = {BASE_LABEL: "5319e7", "severity:high": "b60205"}


class GitHub:
    def __init__(self, repo_slug: str, token: str | None = None):
        self.repo = repo_slug  # "owner/name"
        self.token = token or os.environ["GITHUB_TOKEN"]

    def _call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            return json.loads(data) if data else None

    def ensure_label(self, name: str) -> None:
        color = LABEL_COLORS.get(name, "fbca04")
        try:
            self._call("POST", "/labels", {"name": name, "color": color})
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 422 = already exists
                raise

    def rolling_issue(self) -> int:
        self.ensure_label(BASE_LABEL)
        issues = self._call("GET", f"/issues?state=open&labels={BASE_LABEL}&per_page=100")
        for issue in issues:
            if issue["title"] == ROLLING_TITLE:
                return issue["number"]
        created = self._call("POST", "/issues", {
            "title": ROLLING_TITLE,
            "labels": [BASE_LABEL],
            "body": "The commit reviewer posts one comment per reviewed commit here. High-severity findings get "
                    "their own issue. React 👍/👎 on each comment to grade it.",
        })
        return created["number"]

    def comment(self, issue: int, body: str) -> str:
        return self._call("POST", f"/issues/{issue}/comments", {"body": body})["html_url"]

    def open_issue(self, title: str, body: str, labels: list[str]) -> str:
        for label in labels:
            self.ensure_label(label)
        return self._call("POST", "/issues", {"title": title, "body": body, "labels": labels})["html_url"]

    def open_high_issues(self) -> list[dict]:
        return self._call("GET", "/issues?state=open&labels=severity:high&per_page=100")
