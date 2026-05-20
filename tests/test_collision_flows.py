"""
test_collision_flows — verifies the load-time collision flow that
`LibraryPanel.add_entry` and `PrimerDesignScreen._save_primers_btn`
share via `_resolve_load_collisions`.

Pure-helper coverage; the full UI smoke is handled by the parts-bin
dedupe tests (the same helpers back every subsystem).
"""
from __future__ import annotations

import splicecraft as sc


class _StubApp:
    def __init__(self):
        self._next_callback = None
        self.pushed: list[type] = []

    def push_screen(self, screen, callback=None):
        self.pushed.append(type(screen))
        self._next_callback = callback

    def fire(self, payload):
        cb = self._next_callback
        self._next_callback = None
        if cb is not None:
            cb(payload)


class TestLibraryEntryCollisionShape:
    """Verifies the shape `LibraryPanel.add_entry` feeds through
    `_resolve_load_collisions`: name_key = "name", content = gb_text.
    Plasmid library is the highest-volume load path, so the modal
    routing has to handle real-world entry shapes correctly.
    """

    def test_same_name_diff_gb_routes_to_name_collision(self):
        app = _StubApp()
        new = [{"name": "pUC19", "id": "newid", "gb_text": "DIFFERENT"}]
        existing = [{"name": "pUC19", "id": "oldid", "gb_text": "ORIGINAL"}]
        resolved: list = []
        sc._resolve_load_collisions(
            app, "plasmid", new, existing,
            content_fn=lambda e: e.get("gb_text", "") or "",
            on_resolved=lambda i, r: resolved.append((i, r)),
        )
        # First push must be NameCollisionModal (not ExactCopy)
        assert app.pushed[0] is sc.NameCollisionModal
        app.fire("overwrite")
        items, replace = resolved[0]
        assert items[0]["id"] == "newid"
        assert replace == {"pUC19"}

    def test_same_name_same_gb_routes_to_exact_copy(self):
        app = _StubApp()
        new = [{"name": "pUC19", "id": "newid", "gb_text": "SAME"}]
        existing = [{"name": "pUC19", "id": "oldid", "gb_text": "SAME"}]
        resolved: list = []
        sc._resolve_load_collisions(
            app, "plasmid", new, existing,
            content_fn=lambda e: e.get("gb_text", "") or "",
            on_resolved=lambda i, r: resolved.append((i, r)),
        )
        assert app.pushed[0] is sc.ExactCopyConfirmModal
        app.fire(True)  # keep with COPY
        items, _ = resolved[0]
        assert items[0]["name"] == "pUC19 COPY"


class TestPrimerCollisionShape:
    """Verifies the primer save flow's collision contract:
    name_key = "name", content = uppercased sequence.
    """

    def test_same_name_diff_seq_routes_to_collision(self):
        app = _StubApp()
        new = [
            {"name": "FOO-F", "sequence": "ATCGATCG"},
            {"name": "FOO-R", "sequence": "TTTTTTTT"},
        ]
        existing = [{"name": "FOO-F", "sequence": "DIFFERENT"}]
        resolved: list = []
        sc._resolve_load_collisions(
            app, "primer", new, existing,
            content_fn=lambda e: (e.get("sequence") or "").upper(),
            on_resolved=lambda i, r: resolved.append((i, r)),
        )
        assert app.pushed[0] is sc.NameCollisionModal
        app.fire("keep")
        items, replace = resolved[0]
        # FOO-F dropped (kept original); FOO-R lands
        names = [i["name"] for i in items]
        assert "FOO-R" in names
        assert "FOO-F" not in names
        assert replace == set()


class TestCopyNameUniquenessInBatch:
    """If renaming with COPY would also collide, the helper appends
    " COPY 2", " COPY 3", ... until unique.
    """

    def test_copy_number_increments(self):
        app = _StubApp()
        # Existing has both "FOO" and "FOO COPY"; the new "FOO" gets
        # renamed to "FOO COPY 2".
        new = [{"name": "FOO", "sequence": "ACGT"}]
        existing = [
            {"name": "FOO", "sequence": "ACGT"},
            {"name": "FOO COPY", "sequence": "XXXX"},
        ]
        resolved: list = []
        sc._resolve_load_collisions(
            app, "part", new, existing,
            content_fn=lambda e: (e.get("sequence") or "").upper(),
            on_resolved=lambda i, r: resolved.append((i, r)),
        )
        # FOO matches existing FOO exactly → exact copy path
        assert app.pushed[0] is sc.ExactCopyConfirmModal
        app.fire(True)
        items, _ = resolved[0]
        assert items[0]["name"] == "FOO COPY 2"


class TestCascadeCancelStopsLoad:
    """Cancel on the name-collision modal aborts the entire load — no
    items saved, no replacements applied, on_cancelled fired."""

    def test_cancel_after_keep_copy_still_aborts_collision_stage(self):
        app = _StubApp()
        resolved: list = []
        cancelled = [False]
        new = [
            {"name": "DUP", "sequence": "ACGT"},      # exact copy
            {"name": "COLLIDE", "sequence": "GGGG"},  # name collision
        ]
        existing = [
            {"name": "DUP", "sequence": "ACGT"},
            {"name": "COLLIDE", "sequence": "CCCC"},
        ]
        sc._resolve_load_collisions(
            app, "part", new, existing,
            content_fn=lambda e: (e.get("sequence") or "").upper(),
            on_resolved=lambda i, r: resolved.append((i, r)),
            on_cancelled=lambda: cancelled.__setitem__(0, True),
        )
        # Stage 1: ExactCopyConfirmModal — user picks Keep
        app.fire(True)
        # Stage 2: NameCollisionModal — user picks Cancel
        app.fire("cancel")
        # Whole load aborts; nothing in resolved.
        assert resolved == []
        assert cancelled[0] is True
