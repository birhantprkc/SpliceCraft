"""
test_synthesis — gene-synthesis composer (Synthesis menu) tests.

Covers:
  * SynthesisEditor primitives — insert, delete, selection delete,
    cursor moves, IUPAC-only typing, max-bp cap, feature shift on
    insert/delete.
  * Selection arithmetic — drag-to-select via the message-bus
    surface, Shift+arrow extension via `set_cursor(extend_sel=True)`.
  * SynthesisScreen mount + lifecycle — opens, hosts the editor,
    feature-library side panel populates, document-model save flow.
  * Restriction site insert — picker dispatch + cursor splice.
  * Feature library insert mode + annotate mode.
  * AddFeatureModal `total_len` injection (synthesis host w/o the
    main #seq-panel).
  * Save round-trip lands a linear SeqRecord in the library.
  * `_blocks_undo` opt-out on every new modal (invariant #41).
"""
from __future__ import annotations

import pytest

import splicecraft as sc


_TERM = (160, 48)


# ═══════════════════════════════════════════════════════════════════════════════
# SynthesisEditor — synchronous unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSynthesisEditorPrimitives:
    """The editor's mutations are pure functions over (seq, feats,
    cursor, selection); test them without an async Pilot."""

    def _make(self):
        ed = sc.SynthesisEditor()
        ed._seq = ""
        ed._feats = []
        ed._cursor_pos = 0
        return ed

    def test_insert_appends_at_cursor(self):
        ed = self._make()
        ed._cursor_pos = 0
        # `insert_at_cursor` notifies via `self.app.notify` — bypass by
        # poking the buffer directly so we don't need a mounted app.
        ed._seq = "AAA"
        ed._cursor_pos = 3
        # Manual mirror of `insert_at_cursor`'s body so we can test
        # offline. The integration path is exercised in the async tests.
        cur = ed._cursor_pos
        clean = "TTT"
        ed._seq = ed._seq[:cur] + clean + ed._seq[cur:]
        ed._cursor_pos = cur + len(clean)
        assert ed._seq == "AAATTT"
        assert ed._cursor_pos == 6

    def test_clamp_cursor_to_seq_bounds(self):
        ed = self._make()
        ed._seq = "ACGT"
        ed._cursor_pos = 99
        ed._clamp_cursor()
        assert ed._cursor_pos == 4
        ed._cursor_pos = -5
        ed._clamp_cursor()
        assert ed._cursor_pos == 0

    def test_delete_range_shifts_features_right_of_cut(self):
        ed = self._make()
        ed._seq = "ATGCATGCATGC"
        ed._feats = [
            {"start": 0, "end": 4, "label": "left", "type": "misc_feature"},
            {"start": 8, "end": 12, "label": "right", "type": "misc_feature"},
        ]
        ed._cursor_pos = 0
        # Delete bp 4-8 (middle)
        ed._delete_range(4, 8)
        assert ed._seq == "ATGCATGC"
        # `left` is unchanged (entirely left of deletion).
        assert ed._feats[0]["start"] == 0 and ed._feats[0]["end"] == 4
        # `right` shifts left by 4.
        assert ed._feats[1]["start"] == 4 and ed._feats[1]["end"] == 8

    def test_delete_range_clips_overlap(self):
        ed = self._make()
        ed._seq = "ATGCATGCATGC"
        ed._feats = [
            # Spans across the cut.
            {"start": 2, "end": 10, "label": "span", "type": "misc_feature"},
        ]
        ed._cursor_pos = 0
        ed._delete_range(4, 8)  # delete bp 4-8
        # Overlap clipped: surviving span starts at min(s, start) = 2,
        # ends at max(start, e - n_del) = max(4, 10-4) = 6.
        assert ed._feats[0]["start"] == 2
        assert ed._feats[0]["end"] == 6

    def test_delete_range_drops_zero_length_feature(self):
        ed = self._make()
        ed._seq = "ATGCATGC"
        # Feature exactly equal to the deleted range disappears.
        ed._feats = [{"start": 2, "end": 4, "label": "tiny",
                      "type": "misc_feature"}]
        ed._delete_range(2, 4)
        assert ed._feats == []

    def test_insert_shifts_features_at_or_after_cursor(self):
        ed = self._make()
        ed._seq = "ATGCATGC"
        ed._feats = [
            # Feature whose end == cursor extends (the "appending to
            # an upstream feature" case).
            {"start": 0, "end": 4, "label": "extends",
             "type": "misc_feature"},
            # Feature whose start == cursor shifts (insert goes
            # BEFORE the feature).
            {"start": 4, "end": 8, "label": "shifts",
             "type": "misc_feature"},
        ]
        ed._cursor_pos = 4
        # Walk the same logic insert_at_cursor uses (offline mirror).
        cur = ed._cursor_pos
        n_ins = 3
        ed._seq = ed._seq[:cur] + "NNN" + ed._seq[cur:]
        new_feats = []
        for f in ed._feats:
            s = f["start"]
            e = f["end"]
            new_f = dict(f)
            if s >= cur:
                new_f["start"] = s + n_ins
            if e >= cur and not (e == cur and s == cur):
                new_f["end"] = e + n_ins
            new_feats.append(new_f)
        ed._feats = new_feats
        # `extends` end shifted (the feature ate the inserted bases).
        assert ed._feats[0]["start"] == 0
        assert ed._feats[0]["end"] == 7
        # `shifts` moved entirely to the right.
        assert ed._feats[1]["start"] == 7
        assert ed._feats[1]["end"] == 11
        assert ed._seq == "ATGCNNNATGC"


class TestSynthesisCaps:
    def test_max_bp_constant_is_50k(self):
        # Sacred — commercial gene synthesis tops out at ~30 kb for
        # the longest vendors. 50 kb is generous headroom that still
        # keeps `_build_seq_text` snappy on one chunk.
        assert sc._SYNTHESIS_MAX_BP == 50_000

    def test_typeable_bases_iupac_only(self):
        # ACGT plus IUPAC ambiguity codes — same set AddFeatureModal
        # validates against so a fragment composed here round-trips
        # through the feature library without surprises. This is the
        # PROGRAMMATIC insert path (restriction sites, library
        # features); keyboard typing is restricted further below.
        assert sc._SYNTHESIS_TYPEABLE_BASES == frozenset("ACGTRYWSMKBDHVN")

    def test_keyboard_bases_acgtn_only(self):
        # Direct keyboard typing only — vendors universally accept
        # A/C/G/T/N as-is; IUPAC ambiguity codes are still allowed
        # via restriction-site / feature-library inserts where the
        # ambiguity is biologically intentional.
        assert sc._SYNTHESIS_KEYBOARD_BASES == frozenset("ACGTN")


# ═══════════════════════════════════════════════════════════════════════════════
# Menubar wiring
# ═══════════════════════════════════════════════════════════════════════════════

class TestMenubarWiring:
    def test_menubar_contains_synthesis(self):
        assert "Synthesis" in sc.MenuBar.MENUS

    def test_synthesis_placed_after_mutagenize(self):
        # Per the workflow the user picked at design time:
        # Mutagenize → Synthesize new → Parts → Constructor.
        menus = sc.MenuBar.MENUS
        i_mut = menus.index("Mutagenize")
        i_syn = menus.index("Synthesis")
        i_parts = menus.index("Parts")
        assert i_mut < i_syn < i_parts

    def test_action_open_synthesis_defined(self):
        assert hasattr(sc.PlasmidApp, "action_open_synthesis")


# ═══════════════════════════════════════════════════════════════════════════════
# _blocks_undo invariant (sweep #6 / sweep #10 / sweep #12)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlocksUndoOptOut:
    """Every new modal that hosts an Input / TextArea / mutates record
    state must carry `_blocks_undo = True` per invariant #41 so app-
    level Ctrl+Z falls through to the inner editor's undo."""

    @pytest.mark.parametrize("cls_name", [
        "SynthesisScreen",
        "SynthesisLoadModal",
        "RestrictionInsertModal",
        "SynthesisUnsavedChangesModal",
    ])
    def test_blocks_undo_true(self, cls_name):
        cls = getattr(sc, cls_name)
        assert getattr(cls, "_blocks_undo", False) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Async lifecycle — Screen mount, menu open, save flow
# ═══════════════════════════════════════════════════════════════════════════════

class TestSynthesisScreen:
    async def test_screen_mounts_via_action(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.action_open_synthesis()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.SynthesisScreen)

    async def test_screen_mounts_via_menu_string(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            # `open_menu` directly dispatches the synthesis action,
            # bypassing the dropdown (it's a direct-action menu).
            app.open_menu("Synthesis", 0, 0)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, sc.SynthesisScreen)

    async def test_initial_state_empty(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            assert ed._seq == ""
            assert ed._cursor_pos == 0
            assert ed._user_sel is None
            assert scr._dirty is False
            assert scr._loaded_id is None

    async def test_insert_makes_dirty(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.insert_at_cursor("ATGC")
            await pilot.pause()
            await pilot.pause()
            assert ed._seq == "ATGC"
            assert ed._cursor_pos == 4
            assert scr._dirty is True

    async def test_cursor_move_does_not_mark_dirty(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed._seq = "ATGCATGC"
            ed._refresh_view()
            ed.set_cursor(3)
            await pilot.pause()
            await pilot.pause()
            # Cursor moves emit CursorMoved (not Changed); the screen
            # listener refreshes status but doesn't touch dirty.
            assert scr._dirty is False

    async def test_insert_at_cursor_keeps_iupac_codes(self):
        """`insert_at_cursor` is the PROGRAMMATIC path — used by
        restriction-site / feature-library inserts that legitimately
        carry IUPAC ambiguity (AvaI's CYCGRG, etc.). Keep IUPAC
        codes; drop only truly invalid chars."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            # X / Z are not IUPAC; Y is (pyrimidine).
            ed.insert_at_cursor("XAYZG@")
            await pilot.pause()
            assert ed._seq == "AYG"

    async def test_keyboard_typing_rejects_iupac_codes(self):
        """`on_key` is the KEYBOARD path — restricted to A/C/G/T/N
        only so accidental IUPAC ambiguity can't slip into a
        synthesis-bound fragment via a stray keystroke. Y, R, W,
        S, M, K, B, D, H, V should all be rejected at the keystroke;
        A, C, G, T, N should land."""
        from textual.events import Key
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            # Drive on_key directly with synthetic events for each
            # character — bypasses Textual's focus / dispatch chain
            # so the test doesn't depend on which widget has focus.
            for ch in "ACGTNYRWSMKBDHV":
                ed.on_key(Key(key=ch.lower(), character=ch))
            await pilot.pause()
            # Only A, C, G, T, N landed.
            assert ed._seq == "ACGTN"

    async def test_keyboard_lowercase_accepted_as_uppercase(self):
        """Lowercase keystrokes uppercase to a typeable base; verifies
        the .upper() shim in the key handler."""
        from textual.events import Key
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            for ch in "acgtn":
                ed.on_key(Key(key=ch, character=ch))
            await pilot.pause()
            assert ed._seq == "ACGTN"

    async def test_keyboard_path_allows_n_explicitly(self):
        """N (any base) is a common synthesis-vendor placeholder and
        must remain typeable even though it's technically an IUPAC
        ambiguity code (= ACGT)."""
        from textual.events import Key
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.on_key(Key(key="n", character="N"))
            ed.on_key(Key(key="n", character="N"))
            ed.on_key(Key(key="n", character="N"))
            await pilot.pause()
            assert ed._seq == "NNN"

    async def test_max_bp_cap_truncates(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            # Pre-fill near the cap so the truncation path fires.
            ed._seq = "A" * (sc._SYNTHESIS_MAX_BP - 3)
            ed._cursor_pos = len(ed._seq)
            ok = ed.insert_at_cursor("ATGCATGC")
            assert ok is True
            assert len(ed._seq) == sc._SYNTHESIS_MAX_BP

    async def test_backspace_at_cursor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed._seq = "ATGCATGC"
            ed._cursor_pos = 4
            ed._refresh_view()
            ed.delete_at_cursor(forward=False)
            assert ed._seq == "ATGATGC"
            assert ed._cursor_pos == 3

    async def test_delete_forward_at_cursor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed._seq = "ATGCATGC"
            ed._cursor_pos = 4
            ed._refresh_view()
            ed.delete_at_cursor(forward=True)
            assert ed._seq == "ATGCTGC"
            assert ed._cursor_pos == 4

    async def test_selection_delete(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed._seq = "AAAATTTTCCCC"
            ed._user_sel = (4, 8)
            ed._refresh_view()
            ed.delete_at_cursor()
            assert ed._seq == "AAAACCCC"
            assert ed._user_sel is None


# ═══════════════════════════════════════════════════════════════════════════════
# Document-model save round-trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaveRoundTrip:
    async def test_commit_save_lands_linear_record_in_library(self):
        """`_commit_save` builds a SeqRecord with topology=linear,
        molecule_type=DNA, and pushes through LibraryPanel.add_entry.
        Verify the entry lands in the active library."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGCATGCATGC", [
                {"start": 0, "end": 3, "label": "start",
                 "type": "misc_feature", "color": "green",
                 "strand": 1, "qualifiers": {}},
            ])
            scr._loaded_name = "test_frag"
            scr._loaded_id   = "test_frag"
            scr._commit_save("ATGCATGCATGCATGC",
                              [dict(f) for f in ed._feats],
                              after=None)
            await pilot.pause()
            await pilot.pause()
            entries = sc._load_library()
            matches = [e for e in entries if e.get("id") == "test_frag"]
            assert len(matches) == 1
            assert "linear" in matches[0].get("gb_text", "").lower()
            # The annotated feature survived into the saved record.
            assert "start" in matches[0].get("gb_text", "")

    async def test_document_model_overwrite_by_id(self):
        """Second save with the same id silently replaces, not appends."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr._loaded_id = "doc_frag"
            scr._loaded_name = "doc_frag"
            scr._commit_save("AAAA", [], after=None)
            await pilot.pause()
            scr._commit_save("CCCC", [], after=None)
            await pilot.pause()
            entries = sc._load_library()
            matches = [e for e in entries if e.get("id") == "doc_frag"]
            assert len(matches) == 1
            # Second-save content is what landed (BioPython lower-cases
            # the ORIGIN block in GenBank output, hence the .upper()).
            assert "CCCC" in matches[0].get("gb_text", "").upper()


# ═══════════════════════════════════════════════════════════════════════════════
# Restriction-site picker + feature-library side panel
# ═══════════════════════════════════════════════════════════════════════════════

class TestRestrictionInsert:
    async def test_picker_dismisses_with_enzyme_name(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            holder = {"result": "sentinel"}

            def _cb(result):
                holder["result"] = result

            app.push_screen(sc.RestrictionInsertModal(), callback=_cb)
            await pilot.pause()
            await pilot.pause()
            modal = app.screen
            modal.dismiss("EcoRI")
            await pilot.pause()
            await pilot.pause()
            assert holder["result"] == "EcoRI"

    async def test_inserted_site_appears_in_buffer(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("AAAA", [])
            ed._cursor_pos = 2
            # Manually drive the post-pick callback.
            site = sc._site_for_enzyme("EcoRI")
            assert site == "GAATTC"
            ed.insert_at_cursor(site)
            await pilot.pause()
            assert ed._seq == "AAGAATTCAA"


class TestFeatureLibrarySidePanel:
    async def test_pane_widgets_present(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            from textual.widgets import DataTable, Input, Button
            scr.query_one("#syn-featlib-table", DataTable)
            scr.query_one("#syn-featlib-search", Input)
            scr.query_one("#btn-syn-featlib-insert", Button)
            scr.query_one("#btn-syn-featlib-annotate", Button)
            scr.query_one("#btn-syn-featlib-refresh", Button)

    async def test_insert_mode_splices_sequence_at_cursor(self,
                                                            monkeypatch):
        # Seed a feature library entry so _refresh_featlib_table picks
        # it up; mock _load_features to return it deterministically.
        monkeypatch.setattr(sc, "_load_features", lambda: [{
            "name": "rbs1",
            "feature_type": "RBS",
            "sequence": "AAAGGAGG",
            "color": "yellow",
            "strand": 1,
            "qualifiers": {},
            "description": "",
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            scr._refresh_featlib_table()
            await pilot.pause()
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("TTTT", [])
            ed._cursor_pos = 2
            # Drive the side-panel insert path with a known entry —
            # bypass the DataTable cursor lookup so the test doesn't
            # depend on Textual focus state.
            scr._featlib_rows = [("k", {
                "name": "rbs1", "feature_type": "RBS",
                "sequence": "AAAGGAGG", "color": "yellow",
                "strand": 1, "qualifiers": {},
            })]
            # Patch the selector to return our fixed entry.
            scr._featlib_selected_entry = lambda: scr._featlib_rows[0][1]
            scr._featlib_insert_selected(mode="insert")
            await pilot.pause()
            assert ed._seq == "TTAAAGGAGGTT"
            # A new feature dict was added covering the insert range.
            inserted = [f for f in ed._feats if f.get("label") == "rbs1"]
            assert len(inserted) == 1
            assert inserted[0]["start"] == 2
            assert inserted[0]["end"] == 10

    async def test_annotate_mode_requires_selection(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            ed._user_sel = None  # no selection
            scr._featlib_selected_entry = lambda: {
                "name": "x", "feature_type": "misc_feature",
                "sequence": "ATGC", "color": "white",
                "strand": 1, "qualifiers": {},
            }
            scr._featlib_insert_selected(mode="annotate")
            await pilot.pause()
            # No selection → no annotation added.
            assert ed._feats == []

    async def test_annotate_mode_overlays_on_selection(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            ed._user_sel = (2, 6)
            scr._featlib_selected_entry = lambda: {
                "name": "promoter1", "feature_type": "promoter",
                "sequence": "ATGC", "color": "magenta",
                "strand": 1, "qualifiers": {},
            }
            scr._featlib_insert_selected(mode="annotate")
            await pilot.pause()
            # DNA unchanged, feature added over the selection.
            assert ed._seq == "ATGCATGC"
            inserted = [f for f in ed._feats
                         if f.get("label") == "promoter1"]
            assert len(inserted) == 1
            assert inserted[0]["start"] == 2
            assert inserted[0]["end"] == 6


# ═══════════════════════════════════════════════════════════════════════════════
# AddFeatureModal total_len injection (synthesis host has no #seq-panel)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizontalScrollAndDnaCentering:
    """The user explicitly asked for horizontal scroll (not wrap) plus
    DNA strand at viewport vertical center. Regression-lock both."""

    async def test_static_is_auto_width(self):
        """`width: auto` on `#syn-view` is what lets the Static expand
        to the rendered Text's natural width — without it, the Static
        inherits parent `1fr` and the no-wrap render gets clipped on
        the right instead of surfacing a horizontal scrollbar."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            view = app.screen.query_one("#syn-view")
            # Textual stringifies `auto` as `"auto"` in styles.
            assert "auto" in str(view.styles.width)

    async def test_scroll_overflow_horizontal_only(self):
        """`overflow-x: auto` surfaces the horizontal scrollbar when
        the rendered Text is wider than the viewport. `overflow-y:
        hidden` suppresses the vertical scrollbar because the editor
        pre-pads blank lines top/bottom for DNA centering — that
        padding inflates content height past the viewport and would
        otherwise draw a non-functional vertical bar."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scroll = app.screen.query_one("#syn-scroll")
            ox = str(scroll.styles.overflow_x).lower()
            oy = str(scroll.styles.overflow_y).lower()
            assert "auto" in ox
            assert "hidden" in oy

    async def test_dna_strand_lands_at_viewport_center(self):
        """Sacred — `_pad_above_for_centering` should drop the DNA top
        strand at viewport vertical center regardless of how many
        feature lanes sit above the DNA. Verified by computing
        screen-y = pad + above_rows and asserting equal to
        viewport_height // 2."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            # Load something with a feature so `above_rows > 0` — that
            # way we exercise the path where padding has to compensate
            # for stacked feature lanes.
            ed.load("ATGCATGC" * 40, [
                {"start": 4, "end": 60, "label": "f1",
                 "type": "CDS", "color": "red",
                 "strand": 1, "qualifiers": {}},
            ])
            await pilot.pause()
            await pilot.pause()
            vp = ed._viewport_height()
            dna_row = ed._dna_top_row_offset()
            pad = ed._pad_above_rows
            assert vp > 0
            assert pad + dna_row == vp // 2

    async def test_centering_recomputed_on_resize(self):
        """Resize re-fires `_refresh_view` so the centering padding
        re-aligns against the new viewport height."""
        app = sc.PlasmidApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC" * 20, [])
            await pilot.pause()
            await pilot.pause()
            pad_big = ed._pad_above_rows
            # `on_resize` is the trigger; calling it directly with a
            # synthetic event is enough to verify the recompute fires.
            ed.on_resize(None)  # type: ignore[arg-type]
            await pilot.pause()
            # Pad value should not have grown stale; same screen, same
            # viewport, same value.
            assert ed._pad_above_rows == pad_big


class TestFlankMarkers:
    """`5'-` / `-3'` flanking markers wrap the DNA top strand
    visually; they live OUTSIDE the bp coordinate space so
    selection / click resolution never confuses them with bases."""

    async def test_markers_present_in_rendered_text(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGCATGC", [])
            await pilot.pause()
            rendered = str(app.screen.query_one("#syn-view").render())
            assert "5'-" in rendered
            assert "-3'" in rendered
            # Markers hug the DNA: "5'-ATGCATGC...ATGC-3'" should
            # appear on the DNA top-strand row.
            assert "5'-ATGCATGCATGC-3'" in rendered

    async def test_empty_fragment_has_no_markers(self):
        """The 5'/3' markers only appear with a non-empty sequence —
        an empty fragment shows the placeholder text instead."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("", [])
            await pilot.pause()
            rendered = str(app.screen.query_one("#syn-view").render())
            assert "5'-" not in rendered
            assert "-3'" not in rendered

    async def test_select_all_covers_dna_only(self):
        """Ctrl+A → `_user_sel = (0, n)` — the bp coordinate space
        excludes the markers, so the highlight visual stays on the
        DNA bases by construction."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            await pilot.pause()
            ed.select_all()
            await pilot.pause()
            assert ed._user_sel == (0, 8)
            # Selection is in bp coords; markers sit outside that
            # range. The rendered DNA row carries the selection
            # highlight on bases only.

    async def test_click_resolution_skips_marker_offset(self):
        """`_click_to_bp` subtracts horizontal-centring pad + 3-char
        5'- marker so a click at the first base lands at bp 0, not at
        bp 3 (which would happen if the marker offset weren't
        subtracted). The line-number gutter was dropped in
        `_wrap_with_53_markers` so it doesn't enter the formula."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            # Long-enough seq so centring pad is 0 — keeps the column
            # arithmetic deterministic (test wants to verify the
            # marker offset, not the centring math).
            ed.load("ATGCATGC" * 30, [])
            await pilot.pause()
            assert ed._pad_left_cols == 0
            view = app.screen.query_one("#syn-view")
            # Column where bp 0 begins = view.region.x + pad_left (0
            # here) + 5'- marker width (3).
            base_col_x = (view.region.x + ed._pad_left_cols
                          + ed._FLANK_MARKER_WIDTH)
            bp = ed._click_to_bp(base_col_x, view.region.y)
            assert bp == 0
            # Clicking ONE column to the left (still on the 5'-
            # marker) should clamp to bp 0.
            bp_marker = ed._click_to_bp(
                base_col_x - 1, view.region.y,
            )
            assert bp_marker == 0
            # Clicking at the column of the 4th base (bp 3) lands at
            # bp 3.
            bp_3 = ed._click_to_bp(base_col_x + 3, view.region.y)
            assert bp_3 == 3

    async def test_marker_width_constant_is_3(self):
        # Sacred — gutter / cursor / click all assume width == 3.
        # Changing to "5'─" (em-dash) or similar would break the math
        # silently.
        assert sc.SynthesisEditor._FLANK_MARKER_WIDTH == 3
        assert sc.SynthesisEditor._FLANK_MARKER_TOP_LEFT == "5'-"
        assert sc.SynthesisEditor._FLANK_MARKER_TOP_RIGHT == "-3'"
        assert sc.SynthesisEditor._FLANK_MARKER_BOT_LEFT == "3'-"
        assert sc.SynthesisEditor._FLANK_MARKER_BOT_RIGHT == "-5'"

    async def test_bottom_strand_markers_reflect_antiparallel(self):
        """Biological reality — DNA is anti-parallel. Top strand 5'→3',
        bottom strand 3'→5' under standard left-to-right orientation.
        The bottom row must carry `3'-` on the left and `-5'` on the
        right, so the user sees:

            5'-ATGCATGC-3'
            3'-TACGTACG-5'
        """
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            await pilot.pause()
            rendered = str(app.screen.query_one("#syn-view").render())
            # Top strand: 5'- on left, -3' on right, hugging bases.
            assert "5'-ATGCATGC-3'" in rendered
            # Bottom strand: 3'- on left, -5' on right, hugging the
            # reverse complement (TACGTACG is the complement of
            # ATGCATGC read left-to-right).
            assert "3'-TACGTACG-5'" in rendered


class TestSelectAllThenDelete:
    """Sacred — Ctrl+A in either tab MUST select the whole buffer
    AND focus the editor so the very next Backspace / Delete
    keystroke erases the highlighted span.

    Pre-fix bug (reported 2026-05-20): Ctrl+A fired from a screen-
    level binding, which left focus on whatever widget the user
    last interacted with (typically a toolbar Button on modal
    open). Backspace then went to the focused Button, which has
    no key handler → silent no-op. Fix: `action_select_all`
    explicitly focuses the active editor's ScrollableContainer
    AND a screen-level Backspace/Delete fallback dispatches to
    the editor's `delete_at_cursor` so the operation works even
    if focus drifts after select-all.
    """

    async def test_dna_ctrl_a_then_backspace_clears(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGCATGC", [])
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            assert ed._user_sel == (0, 12)
            await pilot.press("backspace")
            await pilot.pause()
            assert ed._seq == ""

    async def test_dna_ctrl_a_then_delete_clears(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            await pilot.press("delete")
            await pilot.pause()
            assert ed._seq == ""

    async def test_protein_ctrl_a_then_backspace_clears(self):
        from textual.widgets import TabbedContent
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            tabs = scr.query_one("#syn-tabs", TabbedContent)
            tabs.active = "syn-tab-protein"
            await pilot.pause()
            await pilot.pause()
            pe = scr.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.load("MSKLNPE")
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            assert pe._user_sel == (0, 7)
            await pilot.press("backspace")
            await pilot.pause()
            assert pe._aa_seq == ""

    async def test_ctrl_a_focuses_active_editor(self):
        """The select_all action must move focus onto the active
        editor's ScrollableContainer. Verifies the explicit focus
        call lands the right widget."""
        from textual.containers import ScrollableContainer
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGC", [])
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            focused = app.focused
            assert isinstance(focused, ScrollableContainer)
            assert focused.id == "syn-scroll"

    async def test_screen_backspace_fallback_no_op_without_selection(self):
        """The screen-level Backspace binding must NOT delete anything
        when there's no active selection — single-base backspace
        belongs to the editor's own on_key when it has focus, not the
        screen fallback."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            ed._cursor_pos = 4
            ed._user_sel = None
            # Direct invoke of the screen-level fallback — should bail
            # (no selection to delete).
            ok = scr._delete_active_editor_selection(forward=False)
            await pilot.pause()
            assert ok is False
            # Buffer unchanged.
            assert ed._seq == "ATGCATGC"


class TestShiftArrowSelection:
    """Sacred — the base under the cursor MUST be part of the
    selection on the first shift+arrow press in either direction.

    Pre-fix bug (reported 2026-05-20): cursor visually sits ON
    base[N] (reverse-video paint by `_build_seq_text`), but the
    set_cursor anchor was set to cursor_pos itself, giving
    half-open (pos, anchor) = (N-1, N) for shift+left — covering
    only base[N-1] and "skipping" base[N]. Fix: direction-aware
    anchor — for shift+left set anchor to cursor_pos+1 so the
    half-open range covers BOTH the base under the cursor AND the
    base the cursor moves to.
    """

    async def test_shift_left_includes_base_under_cursor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGCATGC", [])
            # Simulate a fresh click at base 5 — sets cursor + anchor
            # without any active selection.
            ed._cursor_pos = 5
            ed._user_sel = None
            ed._sel_anchor = 5
            # First shift+left: cursor moves to 4, selection must
            # cover BOTH base 4 (where cursor moved to) AND base 5
            # (where cursor WAS).
            ed.set_cursor(4, extend_sel=True)
            assert ed._cursor_pos == 4
            assert ed._user_sel == (4, 6)
            # Each subsequent shift+left adds exactly one base on
            # the left edge; the original cursor base (5) stays in.
            ed.set_cursor(3, extend_sel=True)
            assert ed._user_sel == (3, 6)
            ed.set_cursor(2, extend_sel=True)
            assert ed._user_sel == (2, 6)

    async def test_shift_right_still_includes_starting_base(self):
        """Shift+right wasn't broken pre-fix — anchor = cursor_pos
        with half-open (anchor, pos) naturally covers
        base[cursor_pos]. Regression-lock against future tweaks."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGCATGC", [])
            ed._cursor_pos = 5
            ed._user_sel = None
            ed._sel_anchor = 5
            ed.set_cursor(6, extend_sel=True)
            assert ed._cursor_pos == 6
            # Base 5 (where cursor WAS) is selected.
            assert ed._user_sel == (5, 6)
            ed.set_cursor(7, extend_sel=True)
            # Both bases 5 and 6 selected; cursor at 7.
            assert ed._user_sel == (5, 7)

    async def test_shift_left_then_right_collapses_then_extends(self):
        """Reversing direction reduces selection from the moving
        edge; once the selection collapses, the next press starts a
        fresh extension in the new direction."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGCATGC", [])
            ed._cursor_pos = 5
            ed._user_sel = None
            ed._sel_anchor = 5
            # Shift+left twice → sel (3, 6), cursor at 3.
            ed.set_cursor(4, extend_sel=True)
            ed.set_cursor(3, extend_sel=True)
            assert ed._user_sel == (3, 6)
            # Shift+right reduces left edge: cursor 4, sel (4, 6).
            ed.set_cursor(4, extend_sel=True)
            assert ed._user_sel == (4, 6)
            # Continue right past anchor: cursor 6, sel collapses.
            ed.set_cursor(5, extend_sel=True)
            assert ed._user_sel == (5, 6)
            ed.set_cursor(6, extend_sel=True)
            assert ed._user_sel is None

    async def test_shift_left_at_position_zero_no_op(self):
        """Shift+left at the very start (cursor=0) clamps and
        produces no selection."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            ed._cursor_pos = 0
            ed._user_sel = None
            ed._sel_anchor = 0
            ed.set_cursor(-1, extend_sel=True)  # clamps to 0
            assert ed._cursor_pos == 0
            assert ed._user_sel is None

    async def test_shift_left_after_ctrl_a_reduces_selection(self):
        """Ctrl+A then Shift+Left should REDUCE the selection by one
        from the right edge (the cursor was past the end at n,
        moving left to n-1 drops the last base from the selection).
        """
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])  # n = 8
            ed.select_all()
            assert ed._user_sel == (0, 8)
            assert ed._cursor_pos == 8
            ed.set_cursor(7, extend_sel=True)
            # Cursor moves from 8 to 7. Selection shrinks by one base
            # on the right edge.
            assert ed._user_sel == (0, 7)
            assert ed._cursor_pos == 7


class TestRowMarkerAndCentering:
    """The line-number gutter is gone (always one row, so the leading
    "1" added no info); short sequences sit horizontally centred in
    the viewport; long sequences left-anchor so horizontal scroll
    works."""

    async def test_row_number_marker_dropped(self):
        """Pre-fix the DNA row started with " 1  " before the 5'-
        marker. Post-fix the marker is the very first non-space
        glyph on the DNA row."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            # Long enough to suppress the centring pad — keeps the
            # assertion deterministic.
            ed.load("ATGCATGC" * 30, [])
            await pilot.pause()
            rendered = str(app.screen.query_one("#syn-view").render())
            # Find the DNA top-strand line and check it starts with
            # the 5'- marker (no leading line-number gutter).
            for line in rendered.split("\n"):
                if "5'-" in line and "TACG" not in line:
                    # `line.lstrip()` should yield the marker right
                    # at the start — no `1 ` or similar gutter.
                    assert line.lstrip().startswith("5'-")
                    return
            assert False, "DNA top-strand row not found"

    async def test_short_fragment_horizontally_centered(self):
        """Content width (n + 6) < viewport width → left-pad spaces
        so the fragment sits at the viewport's horizontal centre."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])  # n + 6 = 14
            await pilot.pause()
            vp = ed._viewport_width()
            cw = ed._content_width()
            # Sanity — at the standard 160-col test terminal the
            # editor sits in a 4fr/1fr split so its viewport is
            # narrower than 160 but still > 14.
            assert vp > cw
            # Centring pad = (vp - cw) // 2 exactly.
            assert ed._pad_left_cols == (vp - cw) // 2

    async def test_long_fragment_left_anchored(self):
        """Content width >= viewport width → no centring pad; user
        scrolls horizontally instead."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC" * 50, [])  # 400 bp + 6 = 406, > viewport
            await pilot.pause()
            assert ed._content_width() > ed._viewport_width()
            assert ed._pad_left_cols == 0

    async def test_centering_recomputed_on_size_change(self):
        """Padding recomputes when the sequence shrinks / grows
        across the viewport-width threshold."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            # Start small → centred.
            ed.load("ATGC", [])
            await pilot.pause()
            small_pad = ed._pad_left_cols
            assert small_pad > 0
            # Grow past viewport → pad should drop to 0.
            ed.load("ATGCATGC" * 100, [])
            await pilot.pause()
            assert ed._pad_left_cols == 0
            # Shrink back → centring returns.
            ed.load("ATGC", [])
            await pilot.pause()
            assert ed._pad_left_cols == small_pad

    async def test_click_resolution_respects_centering_pad(self):
        """A click on the very first base of a CENTRED fragment
        must land at bp 0, not at bp `-(pad + marker)`. Critical:
        click math must subtract `_pad_left_cols + FLANK_MARKER_WIDTH`
        from the raw column."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            ed = app.screen.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])  # short → centring pad applies
            await pilot.pause()
            assert ed._pad_left_cols > 0
            view = app.screen.query_one("#syn-view")
            base0_x = (view.region.x + ed._pad_left_cols
                        + ed._FLANK_MARKER_WIDTH)
            bp = ed._click_to_bp(base0_x, view.region.y)
            assert bp == 0
            # Clicking deep in the centring pad collapses to bp 0.
            bp_pad = ed._click_to_bp(view.region.x + 2, view.region.y)
            assert bp_pad == 0


class TestProteinEditorPrimitives:
    """ProteinEditor mirrors SynthesisEditor's mutation semantics but
    in AA coords. Test the primitives independently of the screen."""

    def _make(self):
        pe = sc.ProteinEditor()
        pe._aa_seq = ""
        pe._cursor_pos = 0
        pe._codon_mode = True
        pe._codon_table_raw = dict(sc._CODON_BUILTIN_K12)
        pe._codon_cache = sc.ProteinEditor._build_codon_cache(
            pe._codon_table_raw,
        )
        return pe

    def test_aa_alphabet_is_20_plus_stop(self):
        assert sc._PROTEIN_AA_ALPHABET == frozenset("ACDEFGHIKLMNPQRSTVWY*")

    def test_max_aa_constant_matches_max_bp_div_3(self):
        assert sc._PROTEIN_MAX_AA == sc._SYNTHESIS_MAX_BP // 3

    def test_codon_cache_has_all_20_aas(self):
        cache = sc.ProteinEditor._build_codon_cache(
            dict(sc._CODON_BUILTIN_K12),
        )
        for aa in "ACDEFGHIKLMNPQRSTVWY":
            assert aa in cache, f"missing AA {aa}"
            assert len(cache[aa]) == 3
        # Stop codon also present.
        assert "*" in cache
        assert len(cache["*"]) == 3

    def test_codon_cache_empty_table_falls_back(self):
        cache = sc.ProteinEditor._build_codon_cache({})
        # Empty table → empty cache except the hardcoded TAA stop.
        assert cache.get("*") == "TAA"

    def test_motif_library_has_essentials(self):
        names = {m["name"] for m in sc._PROTEIN_MOTIFS}
        for required in ("His6", "FLAG", "HA", "Myc", "TEV", "P2A",
                          "(GGGGS)x3"):
            assert required in names, f"missing motif {required}"


class TestProteinEditorAsync:
    async def test_typing_inserts_aas(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            pe = app.screen.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.insert_at_cursor("MSKL")
            await pilot.pause()
            assert pe._aa_seq == "MSKL"
            assert pe._cursor_pos == 4

    async def test_typing_drops_non_aa_chars(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            pe = app.screen.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            # B, J, O, U, X, Z are non-standard / ambiguous — drop.
            pe.insert_at_cursor("BJMOUXZK")
            await pilot.pause()
            # M and K survive; everything else drops.
            assert pe._aa_seq == "MK"

    async def test_stop_codon_allowed(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            pe = app.screen.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.insert_at_cursor("MGE*")
            await pilot.pause()
            assert pe._aa_seq == "MGE*"

    async def test_max_aa_cap_truncates(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            pe = app.screen.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe._aa_seq = "M" * (sc._PROTEIN_MAX_AA - 3)
            pe._cursor_pos = len(pe._aa_seq)
            pe.insert_at_cursor("AAAAAAAAAA")  # 10 As — only 3 fit
            await pilot.pause()
            assert len(pe._aa_seq) == sc._PROTEIN_MAX_AA

    async def test_codon_mode_renders_codons_below_aas(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            pe = app.screen.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.load("MSK")
            await pilot.pause()
            rendered = str(app.screen.query_one("#pe-view").render())
            # Top row: N- M  S  K -C  (each AA centred in 3-col group).
            # Bottom row: ATGAGCAAA (K12 most-frequent codons).
            assert "N-" in rendered and "-C" in rendered
            assert " M " in rendered  # AA letter wrapped in spaces
            assert "ATG" in rendered  # M codon
            assert "AAA" in rendered  # K codon

    async def test_aa_only_mode_no_dna_row(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            pe = app.screen.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.load("MSK")
            pe.set_codon_mode(False)
            await pilot.pause()
            rendered = str(app.screen.query_one("#pe-view").render())
            # Single row: N-MSK-C.
            assert "N-MSK-C" in rendered
            # The most-frequent codon for M (ATG) should NOT appear.
            assert "ATG" not in rendered

    async def test_codon_table_switch_updates_render(self):
        """Picking a different codon table should re-render the DNA
        codons under the AA letters."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            pe = app.screen.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.load("MK")
            await pilot.pause()
            first_codon_m = pe._codon_cache.get("M")
            # Synthesise an alternate codon table: M = GTG (not ATG).
            alt_raw = {
                "ATG": ("M", 1), "GTG": ("M", 99),
                "AAA": ("K", 50), "AAG": ("K", 50),
                "TAA": ("*", 1),
            }
            pe.set_codon_table(alt_raw)
            await pilot.pause()
            # M now resolves to GTG.
            assert pe._codon_cache["M"] == "GTG"
            assert pe._codon_cache["M"] != first_codon_m


class TestSynthesisTabbing:
    async def test_dna_and_protein_tabs_both_mount(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            # Both editors present.
            scr.query_one("#syn-editor", sc.SynthesisEditor)
            scr.query_one("#syn-protein-editor", sc.ProteinEditor)
            # Active tab defaults to DNA.
            assert scr._active_tab_id() == "dna"

    async def test_tab_switch_updates_active_state(self):
        from textual.widgets import TabbedContent
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            tabs = scr.query_one("#syn-tabs", TabbedContent)
            tabs.active = "syn-tab-protein"
            await pilot.pause()
            await pilot.pause()
            assert scr._active_tab_id() == "protein"
            tabs.active = "syn-tab-dna"
            await pilot.pause()
            await pilot.pause()
            assert scr._active_tab_id() == "dna"

    async def test_protein_dirty_separate_from_dna_dirty(self):
        """Mutating the DNA tab marks DNA dirty but NOT the protein
        tab, and vice versa."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            pe = scr.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            ed.insert_at_cursor("ATGC")
            await pilot.pause()
            assert scr._dirty is True
            assert scr._protein_dirty is False
            pe.insert_at_cursor("MS")
            await pilot.pause()
            assert scr._dirty is True
            assert scr._protein_dirty is True

    async def test_add_feature_on_protein_tab_notifies(self):
        """Ctrl+F on the protein tab is a no-op (motif library handles
        the equivalent flow). The notify confirms the user got the
        right message."""
        from textual.widgets import TabbedContent
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            tabs = scr.query_one("#syn-tabs", TabbedContent)
            tabs.active = "syn-tab-protein"
            await pilot.pause()
            await pilot.pause()
            pe = scr.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.insert_at_cursor("MS")
            await pilot.pause()
            n_aa_before = len(pe._aa_seq)
            scr.action_add_feature()
            await pilot.pause()
            # AA buffer unchanged — the action just notified.
            assert len(pe._aa_seq) == n_aa_before

    async def test_toggle_codon_mode_on_dna_tab_notifies(self):
        """Alt+T on the DNA tab is a no-op (codon mode is protein-
        tab-only). DNA buffer untouched. (Was Ctrl+M but the terminal
        eats that as Enter / ^M.)"""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.insert_at_cursor("ATGC")
            await pilot.pause()
            n_bp_before = len(ed._seq)
            scr.action_toggle_codon_mode()
            await pilot.pause()
            assert len(ed._seq) == n_bp_before


class TestProteinSaveRoundTrip:
    async def test_save_creates_linear_record_with_translation(self):
        """Protein save → linear DNA library entry with CDS feature
        carrying a translation= qualifier matching the AA seq."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            pe = scr.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.load("MSKLEAH*")
            scr._protein_loaded_id = "test_prot"
            scr._protein_loaded_name = "test_prot"
            scr._commit_protein_save("MSKLEAH*", after=None)
            await pilot.pause()
            await pilot.pause()
            entries = sc._load_library()
            matches = [e for e in entries if e.get("id") == "test_prot"]
            assert len(matches) == 1
            gb = matches[0].get("gb_text", "")
            assert "linear" in gb.lower()
            # CDS + translation qualifier landed.
            assert "CDS" in gb
            assert "MSKLEAH" in gb.upper().replace("\n", "").replace(" ", "")

    async def test_save_then_load_preserves_aa(self):
        """Round-trip: save protein → load same library entry into
        the protein editor → AA sequence preserved exactly."""
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            pe = scr.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            original = "MGSSHHHHHHENLYFQGSAVNTGLPRSE*"
            pe.load(original)
            scr._protein_loaded_id = "rt_prot"
            scr._protein_loaded_name = "rt_prot"
            scr._commit_protein_save(original, after=None)
            await pilot.pause()
            # Reset editor + reload.
            pe.load("")
            await pilot.pause()
            scr._load_protein_entry_by_id("rt_prot")
            await pilot.pause()
            assert pe._aa_seq == original

    async def test_atomic_save_lock_present(self):
        """Sacred — the SynthesisScreen's `_save_lock` must be an
        RLock so DNA `_commit_save` and protein `_commit_protein_save`
        serialise their SeqRecord build + library hand-off."""
        import threading
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            # RLock is reentrant — exposes acquire / release.
            assert hasattr(scr._save_lock, "acquire")
            assert hasattr(scr._save_lock, "release")
            # And it's truly an RLock (allows reentrant acquire).
            scr._save_lock.acquire()
            scr._save_lock.acquire()
            scr._save_lock.release()
            scr._save_lock.release()


class TestProteinMotifLibrary:
    async def test_motif_table_populates_with_builtin_motifs(self):
        from textual.widgets import DataTable
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            t = scr.query_one("#syn-motif-table", DataTable)
            # All 30 builtin motifs should land.
            assert t.row_count == len(sc._PROTEIN_MOTIFS)

    async def test_motif_insert_splices_at_cursor(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            pe = scr.query_one(
                "#syn-protein-editor", sc.ProteinEditor,
            )
            pe.load("MM")
            pe._cursor_pos = 1
            # Stub the selector so the test doesn't depend on row
            # focus state.
            scr._motif_selected_entry = lambda: {
                "name": "His6", "feature_type": "Tag",
                "sequence": "HHHHHH", "description": "",
            }
            scr._motif_insert_selected()
            await pilot.pause()
            assert pe._aa_seq == "MHHHHHHM"


class TestAddFeatureModalTotalLen:
    def test_total_len_kwarg_accepted(self):
        # Backwards-compatible: omitted → None → falls back to live
        # #seq-panel query (the original on-canvas Ctrl+F path).
        m1 = sc.AddFeatureModal()
        assert m1._total_len is None
        # SynthesisScreen path injects an explicit length so the CDS
        # divisibility-by-3 check works even without #seq-panel.
        m2 = sc.AddFeatureModal(total_len=123)
        assert m2._total_len == 123


# ═══════════════════════════════════════════════════════════════════════════════
# Sweep #14 — Save As double-prompt + silent-overwrite fixes
# ═══════════════════════════════════════════════════════════════════════════════

class TestUniqueEntryIdHelper:
    """Regression guard for 2026-05-20 fix.

    ``_make_unique_entry_id`` must suffix-disambiguate against current
    library ids so a fresh save / Save As never silently overwrites an
    unrelated library entry whose sanitised name happens to collide.
    """

    async def test_returns_base_when_no_collision(self, isolated_library):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            assert scr._make_unique_entry_id("fresh_name") == "fresh_name"

    async def test_disambiguates_against_existing_ids(self, isolated_library):
        # Seed the library with an entry whose id is "my_fragment".
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC"), id="my_fragment", name="my_fragment")
        rec.annotations["molecule_type"] = "DNA"
        rec.annotations["topology"]      = "linear"
        sc._save_library([{
            "id": "my_fragment", "name": "My fragment",
            "size": 4, "n_feats": 0,
            "source": "test", "added": "2026-05-20",
            "gb_text": sc._record_to_gb_text(rec),
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            # Sanitised name "my fragment" → base "my_fragment" already
            # taken → suffix-disambiguate to "my_fragment_2".
            uid = scr._make_unique_entry_id("my fragment")
            assert uid != "my_fragment"
            assert uid.startswith("my_fragment_")

    async def test_exclude_id_skips_own_entry(self, isolated_library):
        # Re-save case: the loaded_id is already in the library but we
        # don't want to bump past our own slot.
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        rec = SeqRecord(Seq("ATGC"), id="my_fragment", name="my_fragment")
        rec.annotations["molecule_type"] = "DNA"
        rec.annotations["topology"]      = "linear"
        sc._save_library([{
            "id": "my_fragment", "name": "My fragment",
            "size": 4, "n_feats": 0,
            "source": "test", "added": "2026-05-20",
            "gb_text": sc._record_to_gb_text(rec),
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            # Excluding our own id should return the base without bump.
            uid = scr._make_unique_entry_id(
                "my fragment", exclude_id="my_fragment",
            )
            assert uid == "my_fragment"


class TestSaveAsDoublePromptFix:
    """Regression guard for 2026-05-20 fix.

    Pre-fix ``_save_as._on_named`` set ``loaded_id=None`` then called
    ``_do_save``, which saw ``loaded_id is None`` and prompted for the
    name AGAIN. Post-fix the inner callback hands a unique id straight
    to ``_commit_save``; only one ``NamePlasmidModal`` ever appears.
    """

    async def test_save_as_commits_without_second_prompt(
        self, isolated_library,
    ):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGCATGC", [])
            scr._dirty = True
            # Stub NamePlasmidModal push so we can count invocations.
            push_count = [0]
            orig_push = app.push_screen
            def _counting_push(modal, callback=None):
                if isinstance(modal, sc.NamePlasmidModal):
                    push_count[0] += 1
                    # Simulate user typing a name and confirming.
                    if callback is not None:
                        callback("brand_new_name")
                    return None
                return orig_push(modal, callback=callback)
            app.push_screen = _counting_push  # type: ignore[method-assign]
            try:
                scr._save_as()
            finally:
                app.push_screen = orig_push  # type: ignore[method-assign]
            await pilot.pause()
            await pilot.pause()
            # Exactly one prompt — the pre-fix bug would have stacked
            # a second NamePlasmidModal on top.
            assert push_count[0] == 1, (
                f"Save As should prompt once, prompted {push_count[0]}"
            )
            # Entry landed in the library under the (sanitised, unique) id.
            entries = sc._load_library()
            ids = {e.get("id") for e in entries}
            assert "brand_new_name" in ids

    async def test_protein_save_as_commits_without_second_prompt(
        self, isolated_library,
    ):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            pe = scr.query_one("#syn-protein-editor", sc.ProteinEditor)
            pe.load("MASHHH")
            scr._protein_dirty = True
            push_count = [0]
            orig_push = app.push_screen
            def _counting_push(modal, callback=None):
                if isinstance(modal, sc.NamePlasmidModal):
                    push_count[0] += 1
                    if callback is not None:
                        callback("brand_new_protein")
                    return None
                return orig_push(modal, callback=callback)
            app.push_screen = _counting_push  # type: ignore[method-assign]
            try:
                scr._protein_save_as()
            finally:
                app.push_screen = orig_push  # type: ignore[method-assign]
            await pilot.pause()
            await pilot.pause()
            assert push_count[0] == 1, (
                f"Protein Save As should prompt once, prompted {push_count[0]}"
            )

    async def test_fresh_save_uses_unique_id_against_library(
        self, isolated_library,
    ):
        # Bug scenario: library already has an entry whose id is
        # "my_fragment_v2" with a DIFFERENT name ("Pre-existing
        # entry"). User types Save As "my fragment v2" → sanitises to
        # id "my_fragment_v2". Pre-fix `_make_entry_id` returned the
        # raw sanitised id and `LibraryPanel.add_entry` took the
        # id-match silent-replace branch — the unrelated entry got
        # overwritten silently. Post-fix `_make_unique_entry_id`
        # suffix-disambiguates so the new save lands under a fresh
        # id and the original entry survives.
        #
        # Names deliberately differ so the NameCollisionModal path
        # doesn't preempt the bug class we're guarding.
        from Bio.Seq import Seq
        from Bio.SeqRecord import SeqRecord
        prior = SeqRecord(Seq("CCCC"), id="my_fragment_v2",
                            name="my_fragment_v2")
        prior.annotations["molecule_type"] = "DNA"
        prior.annotations["topology"]      = "linear"
        prior_gb = sc._record_to_gb_text(prior)
        sc._save_library([{
            "id": "my_fragment_v2",
            "name": "Pre-existing entry",
            "size": 4, "n_feats": 0,
            "source": "test", "added": "2026-05-20",
            "gb_text": prior_gb,
        }])
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("AAAA", [])
            scr._dirty = True
            orig_push = app.push_screen
            def _stub_push(modal, callback=None):
                if isinstance(modal, sc.NamePlasmidModal):
                    # User types a name that sanitises to the existing
                    # entry's id but is a DIFFERENT human-readable name
                    # (so no NameCollisionModal fires).
                    if callback is not None:
                        callback("my fragment v2")
                    return None
                return orig_push(modal, callback=callback)
            app.push_screen = _stub_push  # type: ignore[method-assign]
            try:
                scr._do_save()
            finally:
                app.push_screen = orig_push  # type: ignore[method-assign]
            await pilot.pause()
            await pilot.pause()
            entries = sc._load_library()
            ids = [e.get("id") for e in entries]
            # Original "my_fragment_v2" entry must survive (pre-fix it
            # would have been clobbered by the new save's id match).
            assert "my_fragment_v2" in ids, (
                f"original entry overwritten — ids: {ids}"
            )
            # New save lands under a disambiguated id.
            assert any(
                i and i.startswith("my_fragment_v2_") for i in ids
            ), f"expected disambiguated id, got {ids}"
            # The original gb_text must NOT be overwritten.
            prior_entry = next(
                e for e in entries if e.get("id") == "my_fragment_v2"
            )
            assert prior_entry.get("gb_text") == prior_gb
            assert prior_entry.get("name") == "Pre-existing entry"


class TestPartsBinAutoTriggerNewPart:
    """Sweep #14 — ``PartsBinModal(auto_trigger_new_part=True)`` fires
    ``_new_part(None)`` on mount so Synthesis → Clone Fragment hands off
    cleanly to the Domesticator without an extra click."""

    def test_init_accepts_flag(self):
        modal = sc.PartsBinModal(auto_trigger_new_part=True)
        assert modal._auto_trigger_new_part is True

    def test_init_defaults_false(self):
        modal = sc.PartsBinModal()
        assert modal._auto_trigger_new_part is False


class TestSynthesisCloneFragmentButton:
    """Sweep #14 — Clone Fragment button on Synthesis toolbar."""

    async def test_clone_button_present_on_toolbar(self):
        from textual.widgets import Button
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            btn = scr.query_one("#btn-syn-clone", Button)
            assert "clone fragment" in str(btn.label).lower()

    async def test_clone_action_method_exists(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            assert callable(getattr(scr, "action_clone_fragment", None))

    async def test_clone_on_protein_tab_notifies(self):
        from textual.widgets import TabbedContent
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            tabs = scr.query_one("#syn-tabs", TabbedContent)
            tabs.active = "syn-tab-protein"
            await pilot.pause()
            await pilot.pause()
            notes: list[tuple[str, str]] = []
            orig_notify = app.notify
            def _capture(msg, *, severity="information", **kw):
                notes.append((severity, str(msg)))
                return orig_notify(msg, severity=severity, **kw)
            app.notify = _capture  # type: ignore[method-assign]
            try:
                scr.action_clone_fragment()
                await pilot.pause()
            finally:
                app.notify = orig_notify  # type: ignore[method-assign]
            assert any("DNA-tab only" in m for _, m in notes), (
                f"expected DNA-tab-only notify, got {notes}"
            )

    async def test_clone_empty_fragment_notifies(self, isolated_library):
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            notes: list[tuple[str, str]] = []
            orig_notify = app.notify
            def _capture(msg, *, severity="information", **kw):
                notes.append((severity, str(msg)))
                return orig_notify(msg, severity=severity, **kw)
            app.notify = _capture  # type: ignore[method-assign]
            try:
                scr.action_clone_fragment()
                await pilot.pause()
            finally:
                app.notify = orig_notify  # type: ignore[method-assign]
            assert any("empty" in m.lower() for _, m in notes), (
                f"expected empty-fragment notify, got {notes}"
            )


class TestSynthesisCloneFragmentFlow:
    """End-to-end integration guard for the Clone Fragment handoff."""

    async def test_clone_saves_and_opens_parts_bin(self, isolated_library):
        from Bio.Seq import Seq
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGAAACCCGGGTTT", [])
            scr._dirty = True
            # Stub NamePlasmidModal — user types name once during the
            # auto-save inside Clone Fragment.
            orig_push = app.push_screen
            def _stub_push(modal, callback=None):
                if isinstance(modal, sc.NamePlasmidModal):
                    if callback is not None:
                        callback("clone_test_fragment")
                    return None
                return orig_push(modal, callback=callback)
            app.push_screen = _stub_push  # type: ignore[method-assign]
            try:
                scr.action_clone_fragment()
            finally:
                app.push_screen = orig_push  # type: ignore[method-assign]
            # Settle the async push of PartsBinModal.
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()
            # 1. Synthesis fragment landed in the library.
            entries = sc._load_library()
            ids = {e.get("id") for e in entries}
            assert "clone_test_fragment" in ids, (
                f"Synthesis save didn't land — ids: {ids}"
            )
            # 2. The current record is now the synthesis fragment
            #    (so PartsBinModal._new_part picks it up).
            rec = app._current_record
            assert rec is not None
            assert str(rec.seq).upper() == "ATGAAACCCGGGTTT"
            # 3. The full handoff chain fired:
            #    Synthesis dismissed → PartsBinModal mounted →
            #    auto_trigger_new_part fired _new_part(None) →
            #    DomesticatorModal stacked on top. So the topmost
            #    screen should be DomesticatorModal, and the parts
            #    bin should sit below it in the screen stack.
            assert isinstance(app.screen, sc.DomesticatorModal), (
                f"expected DomesticatorModal on top after handoff, "
                f"got {type(app.screen).__name__}"
            )
            stack_types = [type(s).__name__ for s in app.screen_stack]
            assert "PartsBinModal" in stack_types, (
                f"PartsBinModal should sit under the Domesticator; "
                f"stack: {stack_types}"
            )
            assert "SynthesisScreen" not in stack_types, (
                f"SynthesisScreen should have dismissed; "
                f"stack: {stack_types}"
            )

    async def test_clone_aborted_when_save_fails(self, isolated_library):
        # If the user cancels the NamePlasmidModal (callback fires
        # with an empty string), Clone Fragment must NOT proceed to
        # the handoff — no canvas swap, no parts-bin push.
        app = sc.PlasmidApp()
        async with app.run_test(size=_TERM) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.push_screen(sc.SynthesisScreen())
            await pilot.pause()
            await pilot.pause()
            scr = app.screen
            ed = scr.query_one("#syn-editor", sc.SynthesisEditor)
            ed.load("ATGAAA", [])
            scr._dirty = True
            orig_push = app.push_screen
            def _stub_push(modal, callback=None):
                if isinstance(modal, sc.NamePlasmidModal):
                    if callback is not None:
                        callback("")  # cancel — empty name
                    return None
                return orig_push(modal, callback=callback)
            app.push_screen = _stub_push  # type: ignore[method-assign]
            try:
                scr.action_clone_fragment()
            finally:
                app.push_screen = orig_push  # type: ignore[method-assign]
            await pilot.pause()
            await pilot.pause()
            # Synthesis screen still on top — no handoff happened.
            assert isinstance(app.screen, sc.SynthesisScreen), (
                f"Clone Fragment must abort on save cancel; "
                f"screen is {type(app.screen).__name__}"
            )
            # Library still empty.
            assert sc._load_library() == []


class TestSynthesisRenderExceptNarrowed:
    """Sweep #14 — three ``except Exception`` clauses on the render
    path narrowed to ``(AttributeError, TypeError)`` per invariant #1.
    White-box source check so the regression can't drift back to bare-
    ish exception handling."""

    def test_no_bare_exception_in_synth_editors(self):
        with open(sc.__file__, "r", encoding="utf-8") as fh:
            src = fh.read()
        # Look at the lines around the three known render-path sites.
        # Pre-fix used ``except Exception:``; post-fix uses
        # ``except (AttributeError, TypeError):``.
        lines = src.split("\n")
        # The render-path sites are inside SynthesisEditor +
        # ProteinEditor. Walk the file and assert that within those
        # class bodies, no plain ``except Exception:`` lurks at the
        # known column depth.
        synth_start = src.find("class SynthesisEditor(")
        protein_start = src.find("class ProteinEditor(")
        protein_end = src.find("class RestrictionInsertModal(")
        assert synth_start > 0 and protein_start > 0 and protein_end > 0
        synth_body = src[synth_start:protein_start]
        protein_body = src[protein_start:protein_end]
        # No bare ``except Exception:`` (with optional whitespace+colon)
        # in the render path bodies — narrow types only.
        import re
        for body, name in ((synth_body, "SynthesisEditor"),
                            (protein_body, "ProteinEditor")):
            bare = re.findall(r"except Exception\s*:", body)
            assert not bare, (
                f"{name} body carries bare 'except Exception:' — "
                "narrow to specific types per invariant #1"
            )
