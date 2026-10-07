"""
tests/test_seed_idempotency.py — Unit tests for seed_fake_data idempotency logic.

Tests _existing_seed_order_notes() and seed_orders() with mocked HTTP responses.
No live store is required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow importing from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import seed_fake_data as seed  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order(note: str, order_id: int = 1) -> dict:
    return {"id": order_id, "customer_note": note, "status": "processing"}


def _note(i: int) -> str:
    return f"{seed.SEED_MARKER} order #{i:02d}"


# ---------------------------------------------------------------------------
# Tests for _existing_seed_order_notes()
# ---------------------------------------------------------------------------

class TestExistingSeedOrderNotes:
    """Tests for the paginated GET scan that detects already-seeded orders."""

    def test_no_existing_orders_returns_empty_set(self):
        """When the store has no orders, the returned set is empty."""
        with patch("seed_fake_data._get", return_value=[]) as mock_get:
            result = seed._existing_seed_order_notes()
        assert result == set()
        mock_get.assert_called_once()

    def test_all_seeded_orders_detected(self):
        """When 30 seeded orders exist, all 30 notes are returned."""
        fake_orders = [_make_order(_note(i), i) for i in range(1, 31)]
        # Single page, fewer than 100 → stops after first call
        with patch("seed_fake_data._get", return_value=fake_orders):
            result = seed._existing_seed_order_notes()
        expected = {_note(i) for i in range(1, 31)}
        assert result == expected

    def test_non_seeded_orders_not_included(self):
        """Orders without the SEED_MARKER in customer_note are excluded."""
        fake_orders = [
            _make_order(_note(1), 1),
            _make_order("Regular order note", 2),
            _make_order("", 3),
            _make_order(_note(5), 5),
        ]
        with patch("seed_fake_data._get", return_value=fake_orders):
            result = seed._existing_seed_order_notes()
        assert result == {_note(1), _note(5)}

    def test_partial_existing_orders(self):
        """Only orders 1-15 exist; notes 1-15 are returned, not 16-30."""
        fake_orders = [_make_order(_note(i), i) for i in range(1, 16)]
        with patch("seed_fake_data._get", return_value=fake_orders):
            result = seed._existing_seed_order_notes()
        assert len(result) == 15
        assert _note(1) in result
        assert _note(15) in result
        assert _note(16) not in result


# ---------------------------------------------------------------------------
# Tests for seed_orders() idempotency
# ---------------------------------------------------------------------------

class TestSeedOrdersIdempotency:
    """Tests that seed_orders() skips already-existing per-order markers."""

    def _run_seed(self, existing_notes: set[str], captured: list) -> None:
        """Run seed_orders with mocked _get (returns given notes) and _post (captures calls)."""
        def fake_get(path, params=None):
            if "orders" in path:
                # Return one page of fake orders for the notes we have
                return [_make_order(n, i) for i, n in enumerate(existing_notes, 1)]
            return []

        def fake_post(path, data, dry_run):
            if not dry_run:
                captured.append(data.get("customer_note", ""))
            return {"id": len(captured)}

        with (
            patch("seed_fake_data._get", side_effect=fake_get),
            patch("seed_fake_data._post", side_effect=fake_post),
            patch("seed_fake_data.time.sleep"),
        ):
            seed.seed_orders(
                product_ids=[1, 2, 3],
                dry_run=False,
                force=False,
            )

    def test_no_existing_orders_creates_all_30(self):
        """With no existing orders, all 30 are created."""
        created: list[str] = []
        self._run_seed(set(), created)
        assert len(created) == 30

    def test_all_existing_orders_skips_all(self):
        """With all 30 notes already present, nothing is created."""
        all_notes = {_note(i) for i in range(1, 31)}
        created: list[str] = []
        self._run_seed(all_notes, created)
        assert len(created) == 0

    def test_partial_existing_orders_creates_missing_only(self):
        """With orders 1-15 already present, only orders 16-30 are created."""
        partial_notes = {_note(i) for i in range(1, 16)}
        created: list[str] = []
        self._run_seed(partial_notes, created)
        assert len(created) == 15
        # Check the created notes are the missing ones (#16 through #30)
        created_nums = {int(n.split("#")[1]) for n in created}
        assert created_nums == set(range(16, 31))

    def test_force_flag_recreates_even_if_existing(self):
        """--force bypasses idempotency check and creates all 30."""
        all_notes = {_note(i) for i in range(1, 31)}

        created: list[str] = []

        def fake_post(path, data, dry_run):
            if not dry_run:
                created.append(data.get("customer_note", ""))
            return {"id": len(created)}

        with (
            patch("seed_fake_data._get"),
            patch("seed_fake_data._post", side_effect=fake_post),
            patch("seed_fake_data.time.sleep"),
        ):
            seed.seed_orders(
                product_ids=[1, 2, 3],
                dry_run=False,
                force=True,  # bypass idempotency
            )
        assert len(created) == 30
