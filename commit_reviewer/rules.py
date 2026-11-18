"""Per-repo rules file (.reviewer/rules.yaml): what to skip, which area a path belongs to, and the
deterministic checks that run before (and instead of) the model."""
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from .models import Severity


def _match(path: str, patterns: list[str]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(pat) for pat in patterns)


@dataclass
class Area:
    name: str
    paths: list[str]
    always_review: bool = False
    guidance: str = ""


@dataclass
class Check:
    id: str
    severity: Severity
    area: str
    pattern: re.Pattern
    message: str
    paths: list[str] = field(default_factory=lambda: ["**"])
    exclude_paths: list[str] = field(default_factory=list)

    def applies_to(self, path: str) -> bool:
        return _match(path, self.paths) and not _match(path, self.exclude_paths)


@dataclass
class Rules:
    repo: str
    skip_paths: list[str]
    areas: list[Area]
    checks: list[Check]
    synced_header: str | None = None
    synced_paths: list[str] = field(default_factory=list)
    count_exclude_paths: list[str] = field(default_factory=list)
    review_min_changed_lines: int = 40
    max_patch_chars: int = 60_000
    max_low_findings: int = 3
    notes: str = ""

    def is_skipped(self, path: str) -> bool:
        return _match(path, self.skip_paths)

    def area_for(self, path: str) -> Area | None:
        return next((a for a in self.areas if _match(path, a.paths)), None)

    def counts_toward_threshold(self, path: str) -> bool:
        return not _match(path, self.count_exclude_paths)

    def is_synced_path(self, path: str) -> bool:
        return _match(path, self.synced_paths)


def load_rules(path: str | Path) -> Rules:
    raw = yaml.safe_load(Path(path).read_text())
    synced = raw.get("synced") or {}
    return Rules(
        repo=raw["repo"],
        skip_paths=raw.get("skip_paths", []),
        areas=[
            Area(a["area"], a["paths"], a.get("always_review", False), a.get("guidance", "").strip())
            for a in raw.get("areas", [])
        ],
        checks=[
            Check(
                id=c["id"],
                severity=c["severity"],
                area=c["area"],
                pattern=re.compile(c["pattern"], re.IGNORECASE if c.get("ignore_case") else 0),
                message=c["message"],
                paths=c.get("paths", ["**"]),
                exclude_paths=c.get("exclude_paths", []),
            )
            for c in raw.get("checks", [])
        ],
        synced_header=synced.get("header"),
        synced_paths=synced.get("paths", []),
        count_exclude_paths=raw.get("count_exclude_paths", []),
        review_min_changed_lines=raw.get("review_min_changed_lines", 40),
        max_patch_chars=raw.get("max_patch_chars", 60_000),
        max_low_findings=raw.get("max_low_findings", 3),
        notes=(raw.get("notes") or "").strip(),
    )
