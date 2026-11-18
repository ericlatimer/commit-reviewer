"""Decide, without a model, what a commit needs: nothing, deterministic findings, and/or a review."""
import json
import shutil
import subprocess
from dataclasses import dataclass, field

from .gitdiff import Commit, FileChange, file_at
from .models import Finding
from .rules import Rules

SYNC_STAMP = "# Last sync:"


@dataclass
class Triage:
    review_files: list[FileChange] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # path -> reason
    findings: list[Finding] = field(default_factory=list)
    needs_agent: bool = False
    reason: str = ""


def triage(repo: str, commit: Commit, rules: Rules) -> Triage:
    t = Triage()
    always = []
    for f in commit.files:
        if f.binary:
            t.skipped[f.path] = "binary"
            continue
        if rules.is_skipped(f.path):
            t.skipped[f.path] = "skip_paths"
            continue
        if _is_synced(repo, commit.sha, f, rules):
            t.skipped[f.path] = "synced copy"
            if _hand_edited_sync_copy(f):
                t.findings.append(Finding(
                    severity="medium", area="sync", file=f.path, line=None, rule="edit the source, not the copy",
                    claim="Synced copy changed without a sync: its '# Last sync:' line didn't change, so the next "
                          "sync will overwrite this edit.",
                    evidence=f"{len(f.added)} added / {len(f.removed)} removed lines, header untouched",
                    source="check",
                ))
            continue
        t.findings.extend(_run_checks(f, rules))
        t.review_files.append(f)
        area = rules.area_for(f.path)
        if area and area.always_review:
            always.append(f.path)

    changed = sum(f.changed_lines for f in t.review_files if rules.counts_toward_threshold(f.path))
    if always:
        t.needs_agent, t.reason = True, f"always-review area: {', '.join(always[:3])}"
    elif changed >= rules.review_min_changed_lines:
        t.needs_agent, t.reason = True, f"{changed} changed lines"
    else:
        t.reason = f"small ({changed} changed lines), no always-review area"
    return t


def _is_synced(repo: str, sha: str, f: FileChange, rules: Rules) -> bool:
    if rules.is_synced_path(f.path):
        return True
    if not rules.synced_header or f.deleted:
        return False
    content = file_at(repo, sha, f.path) or ""
    return content.startswith(rules.synced_header)


def _hand_edited_sync_copy(f: FileChange) -> bool:
    body_changed = any(not text.startswith(SYNC_STAMP) for _, text in f.added) or any(
        not text.startswith(SYNC_STAMP) for text in f.removed
    )
    stamp_changed = any(text.startswith(SYNC_STAMP) for _, text in f.added)
    return body_changed and not stamp_changed


def _run_checks(f: FileChange, rules: Rules) -> list[Finding]:
    out = []
    for check in rules.checks:
        if not check.applies_to(f.path):
            continue
        for line_no, text in f.added:
            if check.pattern.search(text):
                out.append(Finding(
                    severity=check.severity, area=check.area, file=f.path, line=line_no, rule=check.id,
                    claim=check.message, evidence=text.strip()[:200], source="check",
                ))
                break  # one finding per check per file is enough to look
    return out


def gitleaks_findings(repo: str, sha: str, required: bool) -> list[Finding]:
    """Secrets in this commit, values redacted. Runs before anything is sent to the model."""
    if not shutil.which("gitleaks"):
        if required:
            raise RuntimeError("gitleaks is required here but isn't installed")
        return []
    proc = subprocess.run(
        ["gitleaks", "git", repo, "--log-opts", f"-1 {sha}", "--redact", "--no-banner",
         "--report-format", "json", "--report-path", "-", "--exit-code", "0"],
        capture_output=True, text=True,
    )
    try:
        leaks = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"gitleaks output wasn't JSON: {proc.stderr[:300]}") from e
    return [
        Finding(
            severity="high", area="secrets", file=leak.get("File", "?"), line=leak.get("StartLine"),
            rule=f"gitleaks:{leak.get('RuleID', '?')}",
            claim="Possible secret committed. Rotate it if real; removing it from the code is not enough.",
            evidence=leak.get("Match", "")[:120],  # already redacted by --redact
            source="gitleaks",
        )
        for leak in leaks or []
    ]
