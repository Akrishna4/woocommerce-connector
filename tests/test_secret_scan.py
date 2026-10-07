"""
tests/test_secret_scan.py — Unit tests for scripts/secret_scan.py.

Tests the scan_file() and is_allowlisted() functions in isolation using
temporary files.  No git process is spawned.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from scripts/ without installing
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import secret_scan  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scan_text(content: str, suffix: str = ".py") -> list[tuple[int, str, str]]:
    """Write *content* to a temp file, scan it, return hits."""
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = Path(f.name)
    try:
        return secret_scan.scan_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# HARD patterns — must ALWAYS fire, even on allowlisted lines
# ---------------------------------------------------------------------------

class TestHardPatterns:
    """HARD_PATTERNS are never suppressible by the allowlist."""

    def test_wc_consumer_key_40hex_caught(self):
        """A real-looking ck_ key with 40 hex chars is always reported."""
        line = "WC_CONSUMER_KEY=ck_" + "a" * 40 + "\n"
        hits = _scan_text(line)
        assert hits, "Should have flagged ck_ key"
        assert any("consumer_key" in h[1].lower() for h in hits)

    def test_wc_consumer_secret_40hex_caught(self):
        """A real-looking cs_ key with 40 hex chars is always reported."""
        line = "WC_CONSUMER_SECRET=cs_" + "b" * 40 + "\n"
        hits = _scan_text(line)
        assert hits, "Should have flagged cs_ key"
        assert any("consumer_secret" in h[1].lower() for h in hits)

    def test_aws_access_key_caught(self):
        """AWS access key format is flagged."""
        line = "AWS_KEY=AKIAIOSFODNN7EXAMPLE\n"
        # AKIAIOSFODNN7EXAMPLE is exactly 20 chars; AKIA + 16 upper/digits
        # Use a valid 20-char format
        line = "key=AKIAZZZZZZZZZZZZZZZZ\n"
        hits = _scan_text(line)
        assert hits

    def test_wc_key_in_markdown_caught(self):
        """A ck_ key embedded in a .md file is still reported."""
        content = "Set `WC_CONSUMER_KEY` to `ck_" + "d" * 40 + "`\n"
        hits = _scan_text(content, suffix=".md")
        assert hits, "Should flag ck_ key in markdown"

    def test_ck_key_not_suppressed_even_when_allowlist_comment_present(self):
        """A real ck_ key must NOT be masked just because the line has 'example.com'."""
        line = "key=ck_" + "f" * 40 + "  # see example.com\n"
        hits = _scan_text(line)
        assert hits, "HARD pattern must fire even with allowlist text on same line"


# ---------------------------------------------------------------------------
# SOFT patterns — suppressible by the allowlist
# ---------------------------------------------------------------------------

class TestSoftPatterns:

    def test_quoted_fake_secret_caught(self):
        """A quoted value that looks like a real secret is reported."""
        hits = _scan_text('api_secret = "SomeLongActualSecretValue1234"\n')
        assert hits, "Should flag quoted secret-looking value"

    def test_long_unquoted_token_caught(self):
        """A non-identifier value assigned to 'token' is flagged.

        Pure Python identifiers (only letters/digits/underscores) are allowlisted
        as variable-to-variable assignments.  A value containing hyphens or mixed
        non-identifier chars cannot be a Python identifier, so it is flagged.
        """
        # Value contains hyphens — not a valid Python identifier, so NOT suppressed
        hits = _scan_text("token=some-long-bearer-token-value-here1234\n")
        assert hits, "Should flag long non-identifier token value"

    def test_placeholder_not_caught(self):
        """Standard placeholder text is not flagged."""
        assert not _scan_text("secret = your_secret_here\n")

    def test_example_com_email_not_caught(self):
        """example.com emails used as fixtures are not flagged."""
        assert not _scan_text('password = "alice@example.com"\n')


# ---------------------------------------------------------------------------
# Allowlist — bare Python identifier assignments
# ---------------------------------------------------------------------------

class TestAllowlist:

    def test_variable_to_variable_not_caught(self):
        """consumer_secret=consumer_secret (dataclass call) is NOT flagged."""
        assert not _scan_text("        consumer_secret=consumer_secret,\n")

    def test_variable_keyword_arg_without_comma_not_caught(self):
        """token=token (keyword arg, no trailing comma) is NOT flagged."""
        assert not _scan_text("    token=token\n")

    def test_ck_prefix_variable_still_caught_by_hard(self):
        """If the value starts with ck_ followed by 40 hex, HARD pattern fires first."""
        line = "consumer_key=ck_" + "a" * 40 + "\n"
        hits = _scan_text(line)
        assert hits, "ck_<40hex> must still be caught even in assignment context"

    def test_cs_prefix_variable_still_caught_by_hard(self):
        """If the value starts with cs_ followed by 40 hex, HARD pattern fires first."""
        line = "consumer_secret=cs_" + "b" * 40 + "\n"
        hits = _scan_text(line)
        assert hits, "cs_<40hex> must still be caught even in assignment context"

    def test_quoted_value_not_suppressed_by_identifier_rule(self):
        """A quoted value like secret='SomeLongVal' must NOT be allowlisted."""
        hits = _scan_text("secret='SomeLongSecretValue1234567'\n")
        assert hits, "Quoted value must not be suppressed by bare-identifier allowlist"


# ---------------------------------------------------------------------------
# Integration: temp file with a fake cs_ value exits non-zero
# ---------------------------------------------------------------------------

class TestCliIntegration:

    def test_scan_file_on_fake_cs_key_returns_hit(self, tmp_path):
        """scan_file returns a hit for a file containing a fake cs_ key."""
        fake_key = "cs_" + "c" * 40
        content = f"WC_CONSUMER_SECRET={fake_key}\n"
        p = tmp_path / "leaked.env"
        p.write_text(content, encoding="utf-8")
        hits = secret_scan.scan_file(p)
        assert hits, "Expected a hit for fake cs_ key"
        assert any("consumer_secret" in h[1].lower() for h in hits)

    def test_scan_file_on_clean_file_returns_no_hits(self, tmp_path):
        """scan_file returns no hits for a clean file."""
        p = tmp_path / "clean.py"
        p.write_text("consumer_secret=consumer_secret,\n", encoding="utf-8")
        hits = secret_scan.scan_file(p)
        assert not hits, f"Expected no hits but got: {hits}"
