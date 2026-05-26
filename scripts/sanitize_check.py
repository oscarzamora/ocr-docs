"""Fail the build if tracked files contain likely personal/sensitive markers.

This script scans tracked text files (from `git ls-files`) and exits non-zero
when it detects denylisted names or local machine path fingerprints.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Extend this list if you discover additional private tokens.
DENYLIST = [
    (re.compile(r"\\b(?:ozamo|oscar|giovanna|luciana|rodrigo)\\b", re.IGNORECASE), "personal name"),
    (re.compile(r"C:\\\\Users\\\\(?!<user>)[^\\\\\s\"']+", re.IGNORECASE), "absolute local Windows user path"),
    (re.compile(r"OneDrive\\\\Documents", re.IGNORECASE), "personal OneDrive path"),
    (re.compile(r"\\((?:OZ|GV)\\)"), "owner initials marker"),
]

TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".py",
    ".json",
    ".ps1",
    ".sh",
    ".gitignore",
}

EXCLUDED_FILES = {
    "scripts/sanitize_check.py",
}


def get_tracked_files(repo_root: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        text=False,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))

    paths: list[Path] = []
    for item in proc.stdout.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8", errors="replace").replace("\\", "/")
        if rel in EXCLUDED_FILES:
            continue
        path = repo_root / rel
        if path.suffix.lower() in TEXT_EXTENSIONS or path.name in {".gitignore"}:
            paths.append(path)
    return paths


def scan_file(path: Path) -> list[str]:
    issues: list[str] = []
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="ignore")

    for line_number, line in enumerate(content.splitlines(), start=1):
        for pattern, reason in DENYLIST:
            if pattern.search(line):
                issues.append(f"{path.as_posix()}:{line_number}: {reason}: {line.strip()}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Check tracked files for personal/sensitive markers.")
    parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / ".git").exists():
        print("Not a git repository root.", file=sys.stderr)
        return 2

    all_issues: list[str] = []
    for path in get_tracked_files(repo_root):
        all_issues.extend(scan_file(path))

    if all_issues:
        print("Sanitization check failed. Sensitive markers found:\n")
        for issue in all_issues:
            print(f"- {issue}")
        print("\nUpdate tracked files or move local-only data to ignored files.")
        return 1

    print("Sanitization check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
