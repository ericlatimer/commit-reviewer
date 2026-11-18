"""Triage and checks against throwaway git repos with planted problems. No model, no network."""
import subprocess
from pathlib import Path

import pytest

from commit_reviewer.gitdiff import commits_in_range, load_commit, parse_patch
from commit_reviewer.report import CommitReport, cap_and_sort, comment_md
from commit_reviewer.models import Finding
from commit_reviewer.rules import load_rules
from commit_reviewer.triage import triage

RULES = """
repo: demo
review_min_changed_lines: 10
skip_paths: ["**/*.md", "docs/**"]
synced:
  header: "# Synced from WhatsNext"
  paths: ["scripts/venue/watch.py"]
areas:
  - area: schema
    always_review: true
    paths: ["db_lambda/schema_operations.py"]
    guidance: Schema changes.
  - area: infra
    paths: ["whats_next_cloud/**"]
checks:
  - id: drop-column
    severity: high
    area: data-loss
    ignore_case: true
    pattern: '\\bDROP\\s+(TABLE|COLUMN)\\b'
    message: Deletes data.
  - id: iam-wildcard
    severity: high
    area: infra
    paths: ["whats_next_cloud/**"]
    pattern: 'actions=\\[\\s*"\\*"'
    message: IAM wildcard.
"""


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for path, content in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", msg)
    return sh(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    sh(r, "config", "user.email", "t@example.com")
    sh(r, "config", "user.name", "t")
    synced = "# Synced from WhatsNext: src/backend/x.py\n# Last sync: 2026-11-01 10:00:00\n\ndef f():\n    return 1\n"
    commit(r, {"README.md": "hi\n", "db_lambda/x.py": synced, "app.py": "x = 1\n"}, "init")
    return r


@pytest.fixture
def rules(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(RULES)
    return load_rules(p)


def run(repo, rules, sha):
    return triage(str(repo), load_commit(str(repo), sha), rules)


def test_docs_only_commit_is_skipped_and_free(repo, rules):
    sha = commit(repo, {"README.md": "hi\nmore\n", "docs/a.txt": "x\n"}, "docs")
    t = run(repo, rules, sha)
    assert not t.needs_agent and not t.findings and not t.review_files
    assert t.skipped == {"README.md": "skip_paths", "docs/a.txt": "skip_paths"}


def test_small_commit_gets_no_agent(repo, rules):
    t = run(repo, rules, commit(repo, {"app.py": "x = 2\n"}, "tweak"))
    assert not t.needs_agent and "small" in t.reason


def test_large_commit_goes_to_agent(repo, rules):
    t = run(repo, rules, commit(repo, {"app.py": "".join(f"x{i} = {i}\n" for i in range(20))}, "big"))
    assert t.needs_agent


def test_always_review_area_goes_to_agent_even_if_tiny(repo, rules):
    t = run(repo, rules, commit(repo, {"db_lambda/schema_operations.py": "INDEX = 1\n"}, "schema"))
    assert t.needs_agent and "always-review" in t.reason


def test_planted_drop_column_is_high(repo, rules):
    sha = commit(repo, {"db_lambda/schema_operations.py": 'SQL = "ALTER TABLE beers DROP COLUMN abv"\n'}, "m")
    [f] = run(repo, rules, sha).findings
    assert (f.severity, f.area, f.rule, f.line, f.source) == ("high", "data-loss", "drop-column", 1, "check")


def test_iam_wildcard_only_checked_in_its_paths(repo, rules):
    line = 'policy = Policy(actions=["*"], resources=["*"])\n'
    assert run(repo, rules, commit(repo, {"whats_next_cloud/stack.py": line}, "iam")).findings[0].rule == "iam-wildcard"
    assert not run(repo, rules, commit(repo, {"app.py": line}, "not infra")).findings


def test_sync_rewrite_is_skipped_without_finding(repo, rules):
    new = "# Synced from WhatsNext: src/backend/x.py\n# Last sync: 2026-11-17 09:00:00\n\ndef f():\n    return 2\n"
    t = run(repo, rules, commit(repo, {"db_lambda/x.py": new}, "sync"))
    assert t.skipped == {"db_lambda/x.py": "synced copy"} and not t.findings


def test_hand_edit_of_synced_copy_is_flagged(repo, rules):
    edited = "# Synced from WhatsNext: src/backend/x.py\n# Last sync: 2026-11-01 10:00:00\n\ndef f():\n    return 3\n"
    [f] = run(repo, rules, commit(repo, {"db_lambda/x.py": edited}, "quick fix in prod repo")).findings
    assert (f.severity, f.area) == ("medium", "sync")


def test_headerless_synced_script_is_skipped(repo, rules):
    t = run(repo, rules, commit(repo, {"scripts/venue/watch.py": "#!/usr/bin/env python\nprint(1)\n"}, "s"))
    assert t.skipped == {"scripts/venue/watch.py": "synced copy"}


def test_commits_in_range_oldest_first_and_new_branch(repo):
    base = sh(repo, "rev-parse", "HEAD").strip()
    a = commit(repo, {"app.py": "a\n"}, "a")
    b = commit(repo, {"app.py": "b\n"}, "b")
    assert commits_in_range(str(repo), base, b) == [a, b]
    assert commits_in_range(str(repo), "0" * 40, b) == [b]


def test_parse_patch_tracks_new_line_numbers():
    patch = (
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
        "@@ -10,3 +10,4 @@\n ctx\n-old\n+new1\n+new2\n ctx\n"
    )
    [f] = parse_patch(patch)
    assert f.added == [(11, "new1"), (12, "new2")] and f.removed == ["old"]


def _f(sev, file="a.py"):
    return Finding(severity=sev, area="general", file=file, line=1, rule="r", claim="c", evidence="e")


def test_low_findings_capped_and_sorted_by_severity():
    out = cap_and_sort([_f("low"), _f("high"), _f("low"), _f("medium"), _f("low"), _f("low")], max_low=2)
    assert [f.severity for f in out] == ["high", "medium", "low", "low"]


def test_comment_keeps_high_out_of_rolling_comment():
    r = CommitReport(sha="a" * 40, subject="s", findings=[_f("high"), _f("medium")], reviewed_by_agent=True,
                     triage_reason="x")
    md = comment_md(r, "https://github.com/o/r")
    assert "1 high finding(s) opened as separate issues" in md and "🟡" in md and "🔴" not in md


@pytest.mark.skipif(not __import__("shutil").which("gitleaks"), reason="gitleaks not installed")
def test_gitleaks_catches_planted_key_and_redacts_it(repo):
    from commit_reviewer.triage import gitleaks_findings
    fake = "AKIA" + "Z7Q2" + "M4XK" + "PAWN" + "B3RT"  # built at runtime so this file itself doesn't trip scanners
    sha = commit(repo, {"config.py": f'aws_key = "{fake}"\naws_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY{fake}"\n'}, "oops")
    found = gitleaks_findings(str(repo), sha, required=True)
    assert found and all(f.severity == "high" and f.area == "secrets" for f in found)
    assert all(fake not in f.evidence for f in found)


def test_tools_confine_paths_and_share_one_budget(repo):
    from commit_reviewer import agent as A
    sha = commit(repo, {".github/workflows/x.yml": "on: push\n"}, "wf")
    read_file, search_repo = A.make_tools(str(repo), sha)
    call = lambda t, **kw: t.func(**kw)
    assert "on: push" in call(read_file, path=".github/workflows/x.yml")      # dot-dirs survive
    assert "relative" in call(read_file, path="../etc/passwd")
    assert "relative" in call(read_file, path="/etc/passwd")
    for _ in range(A.MAX_TOOL_CALLS - 3):
        call(search_repo, pattern="push")
    assert "budget used up" in call(search_repo, pattern="push")
    assert "budget used up" in call(read_file, path="app.py")
