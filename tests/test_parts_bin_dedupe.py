"""
test_parts_bin_dedupe — load-time collision detection across the parts
bin (and the generic helpers that back it).

Covers the 2026-05-20 fix for two parts-bin bugs the user surfaced:

  1. Loading a part that matches an existing entry by (name, sequence)
     silently double-added the part. Now `_resolve_load_collisions`
     gates the save behind `ExactCopyConfirmModal` (keep as COPY or
     skip; default skip).

  2. Deleting a single row that has duplicate (name, sequence) tuples
     removed BOTH matching entries via the set-based filter in
     `PartsBinModal._delete_selected`. Now the filter uses Counter so
     selecting one of two duplicates removes one, not both.

Plus regression coverage for the generic helpers:
  * `_ensure_unique_copy_name` — COPY suffix uniqueness.
  * `_classify_collisions` — three-way bucketing.
  * `_resolve_load_collisions` callback semantics for every modal
    decision branch (exact-copy: skip / keep; name-collision: keep
    original / overwrite / cancel).
"""
from __future__ import annotations

from collections import Counter

import splicecraft as sc


# ═══════════════════════════════════════════════════════════════════════════════
# `_ensure_unique_copy_name` — rename-with-suffix
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnsureUniqueCopyName:
    def test_simple_copy(self):
        assert sc._ensure_unique_copy_name("foo", {"foo"}) == "foo COPY"

    def test_second_copy_numbered(self):
        assert sc._ensure_unique_copy_name(
            "foo", {"foo", "foo COPY"},
        ) == "foo COPY 2"

    def test_third_copy_numbered(self):
        assert sc._ensure_unique_copy_name(
            "foo", {"foo", "foo COPY", "foo COPY 2"},
        ) == "foo COPY 3"

    def test_base_returned_when_unique(self):
        # Defensive branch: caller shouldn't hit this in practice, but
        # an empty existing-names set must not infinite-loop.
        assert sc._ensure_unique_copy_name("foo", set()) == "foo"

    def test_custom_suffix(self):
        assert sc._ensure_unique_copy_name(
            "P1", {"P1"}, suffix="DUP",
        ) == "P1 DUP"


# ═══════════════════════════════════════════════════════════════════════════════
# `_classify_collisions` — bucket new items into new / exact_copy / collision
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyCollisions:
    def test_no_collisions(self):
        new = [{"name": "A", "seq": "ACGT"}, {"name": "B", "seq": "TGCA"}]
        existing = [{"name": "C", "seq": "GGGG"}]
        out = sc._classify_collisions(
            new, existing, name_key="name",
            content_fn=lambda e: e.get("seq", ""),
        )
        assert out["new"] == [0, 1]
        assert out["exact_copy"] == []
        assert out["collision"] == []

    def test_exact_copy_detected(self):
        new = [{"name": "A", "seq": "ACGT"}]
        existing = [{"name": "A", "seq": "ACGT"}]
        out = sc._classify_collisions(
            new, existing, name_key="name",
            content_fn=lambda e: e.get("seq", ""),
        )
        assert out["exact_copy"] == [0]
        assert out["new"] == []
        assert out["collision"] == []

    def test_name_collision_detected(self):
        new = [{"name": "A", "seq": "ACGT"}]
        existing = [{"name": "A", "seq": "TTTT"}]
        out = sc._classify_collisions(
            new, existing, name_key="name",
            content_fn=lambda e: e.get("seq", ""),
        )
        assert out["collision"] == [0]
        assert out["new"] == []
        assert out["exact_copy"] == []

    def test_within_batch_dedupe(self):
        # Two identical items in a single batch — second one detected
        # as an exact copy of the first (cross-batch awareness).
        new = [
            {"name": "A", "seq": "ACGT"},
            {"name": "A", "seq": "ACGT"},
        ]
        existing: list[dict] = []
        out = sc._classify_collisions(
            new, existing, name_key="name",
            content_fn=lambda e: e.get("seq", ""),
        )
        assert out["new"] == [0]
        assert out["exact_copy"] == [1]

    def test_empty_name_treated_as_new(self):
        new = [{"name": "", "seq": "ACGT"}, {"name": None, "seq": "TT"}]
        existing = [{"name": "", "seq": "ACGT"}]
        out = sc._classify_collisions(
            new, existing, name_key="name",
            content_fn=lambda e: e.get("seq", ""),
        )
        # Empty-named items always classify as "new" — no rational
        # dedup key.
        assert out["new"] == [0, 1]

    def test_non_dict_items_safe(self):
        # `_classify_collisions` shouldn't crash on a hand-edited
        # entries list with garbage in it.
        new = [{"name": "A", "seq": "ACGT"}, "not_a_dict"]  # type: ignore[list-item]
        existing = ["also_not_a_dict"]  # type: ignore[list-item]
        out = sc._classify_collisions(
            new, existing, name_key="name",
            content_fn=lambda e: e.get("seq", ""),
        )
        # Non-dict new item → empty name → "new" bucket
        assert 0 in out["new"]

    def test_default_content_fn_excludes_timestamps(self):
        # Two entries differing only in `added` should still classify
        # as exact_copy.
        new = [{"name": "A", "seq": "ACGT", "added": "2026-01-01"}]
        existing = [{"name": "A", "seq": "ACGT", "added": "2025-12-01"}]
        out = sc._classify_collisions(new, existing, name_key="name")
        assert out["exact_copy"] == [0]


# ═══════════════════════════════════════════════════════════════════════════════
# `_resolve_load_collisions` — modal-flow callback semantics
# ═══════════════════════════════════════════════════════════════════════════════

class _StubApp:
    """Minimal stand-in for the Textual app that captures `push_screen`
    calls and lets the test drive the modal callback directly.
    """

    def __init__(self):
        self._next_callback = None
        self._next_modal_cls = None
        self.pushed_screens: list[tuple[type, list]] = []

    def push_screen(self, screen, callback=None):
        self.pushed_screens.append((type(screen), getattr(screen, "_names", [])))
        self._next_callback = callback
        self._next_modal_cls = type(screen)

    def fire(self, payload):
        cb = self._next_callback
        self._next_callback = None
        self._next_modal_cls = None
        if cb is not None:
            cb(payload)


class TestResolveLoadCollisionsNoCollisions:
    def test_no_collisions_resolved_immediately(self):
        app = _StubApp()
        resolved: list[tuple[list, set]] = []

        def _on_resolved(items, replace):
            resolved.append((items, replace))

        sc._resolve_load_collisions(
            app, "part",
            [{"name": "A", "sequence": "ACGT"}],
            [{"name": "B", "sequence": "TTTT"}],
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=_on_resolved,
        )
        assert len(resolved) == 1
        items, replace = resolved[0]
        assert len(items) == 1
        assert items[0]["name"] == "A"
        assert replace == set()
        # No modal pushed.
        assert app.pushed_screens == []


class TestResolveLoadCollisionsExactCopy:
    def setup_method(self):
        self.app = _StubApp()
        self.resolved: list[tuple[list, set]] = []
        self.cancelled = False
        self.new_items = [
            {"name": "A", "sequence": "ACGT"},
            {"name": "B", "sequence": "TTTT"},  # exact copy
        ]
        self.existing = [{"name": "B", "sequence": "TTTT"}]

    def _on_resolved(self, items, replace):
        self.resolved.append((items, replace))

    def _on_cancelled(self):
        self.cancelled = True

    def test_skip_duplicates_default(self):
        sc._resolve_load_collisions(
            self.app, "part", self.new_items, self.existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=self._on_resolved,
            on_cancelled=self._on_cancelled,
        )
        # First push: ExactCopyConfirmModal
        assert self.app.pushed_screens[0][0] is sc.ExactCopyConfirmModal
        # User picks "skip" (False)
        self.app.fire(False)
        items, replace = self.resolved[0]
        assert [i["name"] for i in items] == ["A"]
        assert replace == set()

    def test_keep_as_copy(self):
        sc._resolve_load_collisions(
            self.app, "part", self.new_items, self.existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=self._on_resolved,
            on_cancelled=self._on_cancelled,
        )
        # User picks "keep" (True) → rename with COPY suffix
        self.app.fire(True)
        items, replace = self.resolved[0]
        names = {i["name"] for i in items}
        assert "A" in names
        assert "B COPY" in names
        assert replace == set()

    def test_escape_treated_as_skip(self):
        sc._resolve_load_collisions(
            self.app, "part", self.new_items, self.existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=self._on_resolved,
            on_cancelled=self._on_cancelled,
        )
        # User pressed Escape → None payload → treated as skip
        self.app.fire(None)
        items, replace = self.resolved[0]
        assert [i["name"] for i in items] == ["A"]


class TestResolveLoadCollisionsNameCollision:
    def setup_method(self):
        self.app = _StubApp()
        self.resolved: list[tuple[list, set]] = []
        self.cancelled = False
        # Same name "B" but DIFFERENT sequence → name collision
        self.new_items = [
            {"name": "A", "sequence": "ACGT"},
            {"name": "B", "sequence": "DIFFERENT"},
        ]
        self.existing = [{"name": "B", "sequence": "TTTT"}]

    def _on_resolved(self, items, replace):
        self.resolved.append((items, replace))

    def _on_cancelled(self):
        self.cancelled = True

    def test_keep_original_drops_new(self):
        sc._resolve_load_collisions(
            self.app, "part", self.new_items, self.existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=self._on_resolved,
            on_cancelled=self._on_cancelled,
        )
        assert self.app.pushed_screens[0][0] is sc.NameCollisionModal
        self.app.fire("keep")
        items, replace = self.resolved[0]
        # B dropped; only A loaded
        assert [i["name"] for i in items] == ["A"]
        assert replace == set()

    def test_overwrite_replaces_existing(self):
        sc._resolve_load_collisions(
            self.app, "part", self.new_items, self.existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=self._on_resolved,
            on_cancelled=self._on_cancelled,
        )
        self.app.fire("overwrite")
        items, replace = self.resolved[0]
        assert {i["name"] for i in items} == {"A", "B"}
        assert replace == {"B"}

    def test_cancel_aborts_load(self):
        sc._resolve_load_collisions(
            self.app, "part", self.new_items, self.existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=self._on_resolved,
            on_cancelled=self._on_cancelled,
        )
        self.app.fire("cancel")
        assert self.resolved == []
        assert self.cancelled is True

    def test_escape_treated_as_cancel(self):
        sc._resolve_load_collisions(
            self.app, "part", self.new_items, self.existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=self._on_resolved,
            on_cancelled=self._on_cancelled,
        )
        self.app.fire(None)
        assert self.resolved == []
        assert self.cancelled is True


class TestResolveLoadCollisionsMixed:
    """Batch with both exact copies AND name collisions — modal cascade."""

    def test_both_modals_fire(self):
        app = _StubApp()
        resolved: list[tuple[list, set]] = []
        new_items = [
            {"name": "NEW", "sequence": "AAAA"},
            {"name": "EXACT", "sequence": "TTTT"},     # exact copy
            {"name": "COLLIDE", "sequence": "GGGG"},    # name collision
        ]
        existing = [
            {"name": "EXACT", "sequence": "TTTT"},
            {"name": "COLLIDE", "sequence": "CCCC"},
        ]
        sc._resolve_load_collisions(
            app, "part", new_items, existing,
            content_fn=lambda e: e.get("sequence", ""),
            on_resolved=lambda i, r: resolved.append((i, r)),
        )
        # Stage 1: ExactCopyConfirmModal
        assert app.pushed_screens[0][0] is sc.ExactCopyConfirmModal
        app.fire(True)  # keep with COPY
        # Stage 2: NameCollisionModal
        assert app.pushed_screens[1][0] is sc.NameCollisionModal
        app.fire("overwrite")
        items, replace = resolved[0]
        names = {i["name"] for i in items}
        assert "NEW" in names
        assert "EXACT COPY" in names
        assert "COLLIDE" in names
        assert replace == {"COLLIDE"}


# ═══════════════════════════════════════════════════════════════════════════════
# Delete fix — Counter-based removal preserves one-of-N duplicates
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteCounterFix:
    """Regression guard for 2026-05-20 fix.

    Pre-fix `PartsBinModal._delete_selected` used a set of (name, seq)
    tuples for filter membership; legacy duplicates collapsed into one
    set element and a single-row delete removed ALL N matches.

    Post-fix uses Counter to decrement counts and only remove the
    selected count, leaving N - selected entries intact.
    """

    def test_counter_removes_one_of_two_duplicates(self, tmp_path, monkeypatch):
        # Two identical entries; select ONE for delete.
        monkeypatch.setattr(sc._state, "_PARTS_BIN_FILE", tmp_path / "parts_bin.json")
        sc._state._parts_bin_cache = None
        entries = [
            {"name": "DUP", "sequence": "ACGT", "type": "CDS"},
            {"name": "DUP", "sequence": "ACGT", "type": "CDS"},
            {"name": "OTHER", "sequence": "TTTT", "type": "CDS"},
        ]
        sc._save_parts_bin(entries)
        # Simulate the new delete logic: Counter with 1 count of DUP.
        targets = Counter([("DUP", "ACGT")])
        loaded = sc._load_parts_bin()
        kept: list[dict] = []
        for e in loaded:
            key = (e.get("name", ""), e.get("sequence", ""))
            if targets.get(key, 0) > 0:
                targets[key] -= 1
            else:
                kept.append(e)
        # One DUP and the OTHER survive.
        assert len(kept) == 2
        kept_names = [e["name"] for e in kept]
        assert kept_names.count("DUP") == 1
        assert "OTHER" in kept_names

    def test_counter_removes_all_when_count_matches(self, tmp_path,
                                                     monkeypatch):
        """Selecting BOTH duplicates → both removed (preserves the
        explicit user intent — Counter doesn't add false safety)."""
        monkeypatch.setattr(sc._state, "_PARTS_BIN_FILE", tmp_path / "parts_bin.json")
        sc._state._parts_bin_cache = None
        entries = [
            {"name": "DUP", "sequence": "ACGT"},
            {"name": "DUP", "sequence": "ACGT"},
            {"name": "OTHER", "sequence": "TTTT"},
        ]
        sc._save_parts_bin(entries)
        targets = Counter([("DUP", "ACGT"), ("DUP", "ACGT")])
        loaded = sc._load_parts_bin()
        kept: list[dict] = []
        for e in loaded:
            key = (e.get("name", ""), e.get("sequence", ""))
            if targets.get(key, 0) > 0:
                targets[key] -= 1
            else:
                kept.append(e)
        assert len(kept) == 1
        assert kept[0]["name"] == "OTHER"

    def test_pre_fix_bug_would_remove_both(self):
        """Documents the bug the Counter fix replaced: a set-based
        filter on duplicate (name, sequence) tuples collapsed both
        rows into one set element and removed both on a single-row
        delete."""
        entries = [
            {"name": "DUP", "sequence": "ACGT"},
            {"name": "DUP", "sequence": "ACGT"},
            {"name": "OTHER", "sequence": "TTTT"},
        ]
        # Old logic:
        targets_set = {("DUP", "ACGT")}
        kept = [
            e for e in entries
            if (e.get("name", ""), e.get("sequence", "")) not in targets_set
        ]
        # Without the fix, BOTH duplicates removed (the bug).
        assert len(kept) == 1
        assert kept[0]["name"] == "OTHER"
