"""Markdown for GitHub, and the JSON evidence record for each run."""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import SEVERITY_ORDER, Finding

ICON = {"high": "🔴", "medium": "🟡", "low": "⚪"}


@dataclass
class CommitReport:
    sha: str
    subject: str
    findings: list[Finding]
    reviewed_by_agent: bool
    triage_reason: str
    skipped: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    guardrail_block: str | None = None
    prompt_sha256: str = ""

    @property
    def high(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "high"]

    @property
    def rest(self) -> list[Finding]:
        return [f for f in self.findings if f.severity != "high"]


def cap_and_sort(findings: list[Finding], max_low: int) -> list[Finding]:
    findings = sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line or 0))
    lows = [f for f in findings if f.severity == "low"]
    return [f for f in findings if f.severity != "low"] + lows[:max_low]


def _where(f: Finding, repo_url: str, sha: str) -> str:
    loc = f"{f.file}:{f.line}" if f.line else f.file
    anchor = f"#L{f.line}" if f.line else ""
    return f"[`{loc}`]({repo_url}/blob/{sha}/{f.file}{anchor})" if repo_url else f"`{loc}`"


def finding_md(f: Finding, repo_url: str, sha: str) -> str:
    evidence = f.evidence.replace("`", "'").strip()
    return (
        f"{ICON[f.severity]} **{f.severity} · {f.area}** · {_where(f, repo_url, sha)} · _{f.rule}_\n"
        f"{f.claim}\n"
        f"> `{evidence[:300]}`"
    )


def comment_md(r: CommitReport, repo_url: str) -> str:
    link = f"[`{r.sha[:8]}`]({repo_url}/commit/{r.sha})" if repo_url else f"`{r.sha[:8]}`"
    lines = [f"### {link} {r.subject}"]
    if r.summary:
        lines.append(f"_{r.summary}_")
    if r.guardrail_block:
        lines.append("🛡️ Prompt Shields blocked the review of this diff. Read it yourself; it may contain text "
                     "that tries to instruct an AI.")
    if r.high:
        lines.append(f"**{len(r.high)} high finding(s) opened as separate issues.**")
    lines += [finding_md(f, repo_url, r.sha) for f in r.rest]
    if not r.findings and not r.guardrail_block:
        lines.append("No findings.")
    meta = f"{'agent' if r.reviewed_by_agent else 'checks only'} · {r.triage_reason}"
    if r.reviewed_by_agent:
        meta += f" · {r.input_tokens:,} in / {r.output_tokens:,} out tokens"
    lines.append(f"<sub>{meta} · react 👍/👎 on this comment</sub>")
    return "\n\n".join(lines)


def high_issue(f: Finding, r: CommitReport, repo_url: str, mention: str) -> tuple[str, str]:
    title = f"[reviewer] {f.area}: {f.claim[:80]}"
    body = "\n\n".join([
        f"Found in {f'[`{r.sha[:8]}`]({repo_url}/commit/{r.sha})' if repo_url else r.sha[:8]} {r.subject}",
        finding_md(f, repo_url, r.sha),
        "While this issue is open, `deploy.sh` refuses to deploy (override: `REVIEWER_OVERRIDE=\"reason\" ./deploy.sh`). "
        "Close it when fixed, or as not planned if it's a false alarm.",
        f"cc @{mention}" if mention else "",
    ]).strip()
    return title, body


def evidence_record(reports: list[CommitReport], repo: str, model: str) -> str:
    return json.dumps({
        "repo": repo,
        "model_deployment": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commits": [
            {
                "sha": r.sha,
                "reviewed_by_agent": r.reviewed_by_agent,
                "triage_reason": r.triage_reason,
                "prompt_sha256": r.prompt_sha256,
                "tokens": {"input": r.input_tokens, "output": r.output_tokens},
                "guardrail_block": r.guardrail_block,
                "skipped": r.skipped,
                "findings": [f.model_dump() for f in r.findings],
            }
            for r in reports
        ],
    }, indent=2)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
