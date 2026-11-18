"""Read commits and their diffs with plain git."""
import re
import subprocess
from dataclasses import dataclass, field

ZERO_SHA = "0" * 40
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def git(repo: str, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=check
    ).stdout


@dataclass
class FileChange:
    path: str
    patch: str = ""
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line number, text)
    removed: list[str] = field(default_factory=list)
    deleted: bool = False
    binary: bool = False

    @property
    def changed_lines(self) -> int:
        return len(self.added) + len(self.removed)


@dataclass
class Commit:
    sha: str
    subject: str
    author: str
    files: list[FileChange]


def commits_in_range(repo: str, before: str, after: str) -> list[str]:
    """Commits a push added, oldest first. A new branch (before = zeros) reviews only the tip."""
    if not before or before == ZERO_SHA:
        return [after]
    return git(repo, "rev-list", "--reverse", "--no-merges", f"{before}..{after}").split()


def parse_patch(text: str) -> list[FileChange]:
    files: list[FileChange] = []
    cur: FileChange | None = None
    new_line = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            # 'diff --git a/x b/x': the b/ side is the new path (also right for renames).
            cur = FileChange(path=line.split(" b/", 1)[-1])
            files.append(cur)
            continue
        if cur is None:
            continue
        cur.patch += line + "\n"
        if line.startswith("deleted file mode"):
            cur.deleted = True
        elif line.startswith("Binary files"):
            cur.binary = True
        elif m := _HUNK.match(line):
            new_line = int(m.group(1))
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            cur.added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            cur.removed.append(line[1:])
        elif line.startswith(" "):
            new_line += 1
    return files


def load_commit(repo: str, sha: str) -> Commit:
    header = git(repo, "show", "-s", "--format=%H%x00%s%x00%an", sha).strip().split("\x00")
    patch = git(repo, "show", "--format=", "--patch", "--unified=3", "--no-color", "--find-renames", sha)
    return Commit(sha=header[0], subject=header[1], author=header[2], files=parse_patch(patch))


def file_at(repo: str, sha: str, path: str) -> str | None:
    out = subprocess.run(["git", "-C", repo, "show", f"{sha}:{path}"], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None
