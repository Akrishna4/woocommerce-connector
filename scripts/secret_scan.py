#!/usr/bin/env python3
"""
scripts/secret_scan.py — Scan git-tracked files for API keys and secrets.

Checks every file tracked by git for patterns that look like WooCommerce
consumer keys/secrets, AWS keys, or generic high-entropy credentials.

Exits 0 if clean, 1 if potential secrets are found, 2 on usage error.

Usage:
    python scripts/secret_scan.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns considered suspicious
# ---------------------------------------------------------------------------

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # WooCommerce API keys — 40-char hex after ck_ / cs_ prefix
    ("WooCommerce consumer_key",    re.compile(r"\bck_[a-f0-9]{40}\b")),
    ("WooCommerce consumer_secret", re.compile(r"\bcs_[a-f0-9]{40}\b")),
    # AWS access key
    ("AWS access key",              re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # Generic: variable assignment with a long non-placeholder value
    ("Credential assignment",       re.compile(
        r'(?i)(?:secret|password|api[_-]?key|token)\s*[=:]\s*["\']?(?!your_|<|PLACEHOLDER|example)[^\s"\']{16,}'
    )),
]

# ---------------------------------------------------------------------------
# Lines that are known-safe (pattern examples, placeholders, this file itself)
# ---------------------------------------------------------------------------

ALLOWLIST: list[re.Pattern[str]] = [
    re.compile(r"your_.*_here"),
    re.compile(r"PLACEHOLDER"),
    re.compile(r"<your"),
    re.compile(r"re\.compile"),         # pattern definition lines in this file
    re.compile(r"example\.com"),        # fixture / doc emails
    re.compile(r"ck_test_"),            # test key fixture values
    re.compile(r"cs_test_"),
    re.compile(r"#.*ck_[a-f0-9]"),     # commented-out examples
    re.compile(r"#.*cs_[a-f0-9]"),
    # Python variable-to-variable assignments are not secrets:
    # e.g.  consumer_secret=consumer_secret,   (dataclass / function call)
    re.compile(r"(?:secret|password|api[_-]?key|token)\s*=\s*[a-z_][a-z0-9_]*[,)]?\s*$", re.IGNORECASE),
]

# Files to always skip (binary or known-safe)
SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                   ".ttf", ".eot", ".svg", ".pdf", ".zip", ".gz", ".pyc"}
SKIP_FILES = {"scripts/secret_scan.py"}   # this file itself


def get_tracked_files() -> list[Path]:
    """Return paths of all git-tracked files."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(p) for p in result.stdout.splitlines() if p.strip()]


def is_allowlisted(line: str) -> bool:
    return any(p.search(line) for p in ALLOWLIST)


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (line_no, pattern_name, line_content) for suspicious lines."""
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return []
    hits: list[tuple[int, str, str]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if is_allowlisted(line):
                    continue
                for name, pattern in PATTERNS:
                    if pattern.search(line):
                        hits.append((lineno, name, line.rstrip()))
                        break   # one hit per line is enough
    except (IsADirectoryError, PermissionError):
        pass
    return hits


def main() -> None:
    try:
        tracked = get_tracked_files()
    except subprocess.CalledProcessError:
        print("ERROR: Not inside a git repository, or git is not installed.", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError:
        print("ERROR: git not found in PATH.", file=sys.stderr)
        sys.exit(2)

    total_hits = 0
    for path in tracked:
        if str(path) in SKIP_FILES:
            continue
        hits = scan_file(path)
        for lineno, pattern_name, line in hits:
            print(f"WARN  {path}:{lineno}  [{pattern_name}]")
            print(f"      {line[:120]}")
            total_hits += 1

    print()
    if total_hits:
        print(f"FAILED: {total_hits} potential secret(s) found in tracked files.")
        print("Ensure secrets are only in .env (which is .gitignored).")
        sys.exit(1)
    else:
        print(f"OK: Scanned {len(tracked)} tracked file(s) — no secrets found.")


if __name__ == "__main__":
    main()
