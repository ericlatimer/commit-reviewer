"""Entry point.

  python -m commit_reviewer review --repo PATH --rules RULES (--range BEFORE..AFTER | --commit SHA ...)
                                   [--post owner/name] [--evidence-dir DIR] [--no-agent]
  python -m commit_reviewer gate --github owner/name     # exit 1 if any severity:high issue is open
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

from .gitdiff import commits_in_range, load_commit
from .report import CommitReport, cap_and_sort, comment_md, evidence_record, high_issue, sha256
from .rules import load_rules
from .triage import gitleaks_findings, triage


def _client():
    from azure.identity.aio import DefaultAzureCredential
    from agent_framework.openai import OpenAIChatClient

    # The account's OpenAI endpoint, not the Foundry project endpoint: the project endpoint refuses the
    # inference-only role (Cognitive Services OpenAI User) and wants Foundry User, which can also list keys.
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return OpenAIChatClient(
        model=os.environ.get("FOUNDRY_MODEL", "reviewer"),
        base_url=os.environ["FOUNDRY_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/",
        credential=credential,
    ), credential


async def review(args) -> int:
    rules = load_rules(args.rules)
    if args.range:
        before, after = args.range.split("..", 1)
        shas = commits_in_range(args.repo, before, after)
    else:
        shas = args.commit
    client, credential = (None, None) if args.no_agent else _client()
    reports: list[CommitReport] = []
    try:
        for sha in shas:
            reports.append(await review_one(args, rules, sha, client))
    finally:
        if credential:
            await credential.close()

    repo_url = f"https://github.com/{args.post}" if args.post else ""
    for r in reports:
        print(comment_md(r, repo_url), end="\n\n---\n\n")
    if args.evidence_dir:
        Path(args.evidence_dir).mkdir(parents=True, exist_ok=True)
        (Path(args.evidence_dir) / "evidence.json").write_text(
            evidence_record(reports, rules.repo, os.environ.get("FOUNDRY_MODEL", "reviewer")))
    if args.post:
        post(args.post, reports, repo_url, os.environ.get("REVIEWER_MENTION", ""))
    return 0


async def review_one(args, rules, sha, client) -> CommitReport:
    from .agent import build_instructions, build_prompt, review_commit

    commit = load_commit(args.repo, sha)
    t = triage(args.repo, commit, rules)
    secrets = gitleaks_findings(args.repo, commit.sha, required=bool(os.environ.get("CI")))
    t.findings.extend(secrets)
    r = CommitReport(sha=commit.sha, subject=commit.subject, findings=t.findings, reviewed_by_agent=False,
                     triage_reason=t.reason, skipped=t.skipped)
    if secrets:
        r.triage_reason = "possible secret found: nothing sent to the model"
    elif t.needs_agent and client is not None:
        prompt = build_prompt(commit, t, rules)
        r.prompt_sha256 = sha256(build_instructions(t, rules) + prompt)
        result = await review_commit(client, args.repo, commit, t, rules)
        r.reviewed_by_agent = True
        r.findings = t.findings + result.findings
        r.summary, r.guardrail_block = result.summary, result.blocked_by_guardrail
        r.input_tokens, r.output_tokens = result.input_tokens, result.output_tokens
    r.findings = cap_and_sort(r.findings, rules.max_low_findings)
    return r


def post(slug: str, reports: list[CommitReport], repo_url: str, mention: str) -> None:
    from .github import BASE_LABEL, GitHub

    gh = GitHub(slug)
    worth_posting = [r for r in reports if r.findings or r.reviewed_by_agent or r.guardrail_block]
    if not worth_posting:
        return
    rolling = gh.rolling_issue()
    for r in worth_posting:
        for f in r.high:
            title, body = high_issue(f, r, repo_url, mention)
            print("opened", gh.open_issue(title, body, [BASE_LABEL, "severity:high", f"area:{f.area}"]))
        print("commented", gh.comment(rolling, comment_md(r, repo_url)))


def gate(args) -> int:
    from .github import GitHub

    open_high = GitHub(args.github).open_high_issues()
    for issue in open_high:
        print(f"  #{issue['number']} {issue['title']}  {issue['html_url']}")
    return 1 if open_high else 0


def main() -> None:
    p = argparse.ArgumentParser(prog="commit_reviewer")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review")
    r.add_argument("--repo", default=".")
    r.add_argument("--rules", required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--range")
    g.add_argument("--commit", nargs="+")
    r.add_argument("--post", metavar="OWNER/NAME", help="post to this repo's issues (needs GITHUB_TOKEN)")
    r.add_argument("--evidence-dir")
    r.add_argument("--no-agent", action="store_true", help="triage and checks only, no model call")
    gt = sub.add_parser("gate")
    gt.add_argument("--github", required=True, metavar="OWNER/NAME")
    args = p.parse_args()
    sys.exit(asyncio.run(review(args)) if args.cmd == "review" else gate(args))
