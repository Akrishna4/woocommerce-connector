#!/usr/bin/env python3
"""
scripts/secret_scan.py — Scan git-tracked files for API keys and secrets.

Checks every file tracked by git for patterns that look like WooCommerce
consumer keys/secrets, AWS keys, or generic high-entropy credentials.

Exits 0 if clean, 1 if potential secrets are found, 2 on usage error.

Pattern tiers
-------------
HARD_PATTERNS  — WooCommerce ck_/cs_ keys and AWS access keys.  These are
                 NEVER suppressed by the allowlist; a match always fails the
                 scan regardless of the surrounding context.

SOFT_PATTERNS  — Generic credential-assignment heuristics (variable = value).
                 These CAN be suppressed by an allowlist entry.  False positives
                 in this tier (e.g. Python variable-to-variable assignments) are
                 handled here without risking masking a real key.

Usage:
    python scripts/secret_scan.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# HARD patterns — never suppressible by the allowlist
# ---------------------------------------------------------------------------

HARD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # WooCommerce API keys — 40-char hex after ck_ / cs_ prefix
    ("WooCommerce consumer_key",    re.compile(r"\bck_[a-f0-9]{40}\b")),
    ("WooCommerce consumer_secret", re.compile(r"\bcs_[a-f0-9]{40}\b")),
    # AWS access key
    ("AWS access key",              re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
]

# ---------------------------------------------------------------------------
# SOFT patterns — suppressible by the allowlist
# ---------------------------------------------------------------------------

SOFT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Generic: variable assignment with a long non-placeholder value.
    # Note: lines with ck_/cs_ prefixes hit HARD_PATTERNS first and are
    # never evaluated here.
    ("Credential assignment", re.compile(
        r'(?i)(?:secret|password|api[_-]?key|token)\s*[=:]\s*["\']?'
        r'(?!your_|<|PLACEHOLDER|example)[^\s"\']{16,}'
    )),
]

# ---------------------------------------------------------------------------
# Allowlist — applies ONLY to SOFT_PATTERNS hits
# ---------------------------------------------------------------------------

ALLOWLIST: list[re.Pattern[str]] = [
    re.compile(r"your_.*_here"),
    re.compile(r"PLACEHOLDER"),
    re.compile(r"<your"),
    re.compile(r"re\.compile"),         # pattern definition lines in this file
    re.compile(r"example\.com"),        # fixture / doc emails
    re.compile(r"ck_test_"),            # test key fixture values (short, non-40-hex)
    re.compile(r"cs_test_"),
    re.compile(r"#.*ck_[a-f0-9]"),     # commented-out examples
    re.compile(r"#.*cs_[a-f0-9]"),
    # Python variable-to-variable assignments are NOT secrets.
    # Example: consumer_secret=consumer_secret,   (dataclass / function call)
    #
    # Requirements for this allowlist to fire:
    #   1. Value must be a bare Python identifier (letters/digits/underscores only).
    #   2. Value must NOT start with ck_ or cs_ (those are caught by HARD_PATTERNS
    #      and never reach allowlist evaluation).
    #   3. Value must NOT be quoted (quoted strings are genuine values, not variables).
    #
    # Pattern breakdown:
    #   (?:secret|...) \s*=\s*   — keyword followed by equals sign
    #   (?!ck_|cs_)              — NOT a WC key prefix (extra safety guard)
    #   [a-z_][a-z0-9_]*        — bare Python identifier (no quotes, no special chars)
    #   [,)]?\s*$               — optionally followed by comma or closing paren, then EOL
    re.compile(
        r"(?:secret|password|api[_-]?key|token)\s*=\s*(?!ck_|cs_|[\"'])"
        r"[a-z_][a-z0-9_]*[,)]?\s*$",
        re.IGNORECASE,
    ),
]

# Files to always skip (binary or known-safe)
SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                   ".ttf", ".eot", ".svg", ".pdf", ".zip", ".gz", ".pyc"}
SKIP_FILES = {"scripts/secret_scan.py", "tests/test_secret_scan.py"}   # skip script and its test


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
    """Return True if *line* matches any allowlist pattern."""
    return any(p.search(line) for p in ALLOWLIST)


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (line_no, pattern_name, line_content) for suspicious lines.

    HARD_PATTERNS are checked first and are never suppressed by the allowlist.
    SOFT_PATTERNS are checked second and can be suppressed.
    """
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return []
    hits: list[tuple[int, str, str]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                # --- Hard patterns: always report, never allow-listed ---
                for name, pattern in HARD_PATTERNS:
                    if pattern.search(line):
                        hits.append((lineno, name, line.rstrip()))
                        break   # one hit per line is enough
                else:
                    # --- Soft patterns: allowlist can suppress ---
                    if is_allowlisted(line):
                        continue
                    for name, pattern in SOFT_PATTERNS:
                        if pattern.search(line):
                            hits.append((lineno, name, line.rstrip()))
                            break
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
