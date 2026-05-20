"""
test_sweep12 — UI audit sweep #12 regression coverage (2026-05-20).

Sweep #12 covers a four-phase UI audit:

  * Phase 1 (render & refresh perf): `action_aspect_inc/dec` early-exit
    when already at the 0.5/5.0 limit — pre-fix, holding `,` or `.`
    queued a Toast for every key tick AND fired reactive churn through
    `_aspect`'s setter even when the value was unchanged.

  * Phase 2 (modal & interaction flow): retrofit `_blocks_undo: bool =
    True` onto every modal that opens an Input/TextArea or commits a
    persistent mutation. Pre-fix Ctrl+Z inside any of these modals would
    fall through to the canvas undo stack instead of the Input's own
    undo, surprising the user mid-edit.

  * Phase 3 (reactive & widget hygiene): no actionable findings — the
    codebase already routes searches through debounced Input.Submitted,
    keeps every render path behind a cache key, and uses `query_one`
    consistently. Documented here so a future audit knows to skip the
    obvious paths.

  * Phase 4 (this file): regression coverage so the fixes stick.

See CLAUDE.md invariant #52 (added in this sweep) for the inventory.
"""
from __future__ import annotations

import inspect

import splicecraft as sc


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — action_aspect early-exit at limit
# ═══════════════════════════════════════════════════════════════════════════════

class TestAspectActionEarlyExitAtLimit:
    """Regression guard: holding `,` (aspect_inc) at the 5.0 cap or `.`
    (aspect_dec) at the 0.5 floor must NOT fire `notify()` repeatedly
    AND must NOT re-assign the reactive — both would queue Toast widgets
    on every tick and trigger spurious reactive watchers.
    """

    def _make_pm(self):
        pm = sc.PlasmidMap()
        # Stub `notify` so we can count calls without needing an App
        # context. Production code path also tolerates an absent App
        # via reactive-only behaviour.
        pm._notify_calls = []  # type: ignore[attr-defined]
        pm.notify = lambda *a, **k: pm._notify_calls.append((a, k))  # type: ignore[assignment]
        return pm

    def test_inc_at_max_does_not_notify(self):
        pm = self._make_pm()
        pm._aspect = 5.0
        pm.action_aspect_inc()
        assert pm._aspect == 5.0
        assert pm._notify_calls == []

    def test_dec_at_min_does_not_notify(self):
        pm = self._make_pm()
        pm._aspect = 0.5
        pm.action_aspect_dec()
        assert pm._aspect == 0.5
        assert pm._notify_calls == []

    def test_inc_below_max_increments_and_notifies(self):
        pm = self._make_pm()
        pm._aspect = 2.0
        pm.action_aspect_inc()
        assert pm._aspect == 2.05
        assert len(pm._notify_calls) == 1

    def test_dec_above_min_decrements_and_notifies(self):
        pm = self._make_pm()
        pm._aspect = 2.0
        pm.action_aspect_dec()
        assert pm._aspect == 1.95
        assert len(pm._notify_calls) == 1

    def test_inc_held_at_max_for_20_ticks_stays_quiet(self):
        # Simulates the user holding `,` for ~2 s while already at the
        # widest aspect. Pre-fix this queued 20 Toast widgets; post-fix
        # the first tick returns silently and every subsequent tick is
        # a no-op too (`new == self._aspect` short-circuits).
        pm = self._make_pm()
        pm._aspect = 5.0
        for _ in range(20):
            pm.action_aspect_inc()
        assert pm._aspect == 5.0
        assert pm._notify_calls == []

    def test_dec_held_at_min_for_20_ticks_stays_quiet(self):
        pm = self._make_pm()
        pm._aspect = 0.5
        for _ in range(20):
            pm.action_aspect_dec()
        assert pm._aspect == 0.5
        assert pm._notify_calls == []

    def test_inc_source_has_early_exit_guard(self):
        # White-box guard against re-introducing the spam: the source
        # must literally compute `new = round(...)`, check `new ==
        # self._aspect`, and return. A future contributor who naively
        # re-fuses the lines back together (or removes the equality
        # check) trips this.
        src = inspect.getsource(sc.PlasmidMap.action_aspect_inc)
        assert "new = round(" in src
        assert "if new == self._aspect:" in src
        assert "return" in src

    def test_dec_source_has_early_exit_guard(self):
        src = inspect.getsource(sc.PlasmidMap.action_aspect_dec)
        assert "new = round(" in src
        assert "if new == self._aspect:" in src
        assert "return" in src


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — `_blocks_undo` retrofit on 12 input-bearing modals
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlocksUndoOnInputBearingModals:
    """Regression guard: every modal with a user-facing Input or TextArea
    (or that commits a persistent mutation) must carry `_blocks_undo:
    bool = True`. Pre-fix Ctrl+Z under any of these modals fell through
    to canvas undo instead of triggering the Input/TextArea's own undo
    — the user typed `pUC19_v2`, hit Ctrl+Z to fix a typo, and lost a
    feature edit on the canvas underneath instead.

    See `PlasmidApp._undo_blocked_by_modal` (line ~71171) for the
    opt-in policy and CLAUDE.md invariant #41 for the placement rule
    ("attr must come AFTER docstring or first-statement detection
    breaks").
    """

    def test_multi_record_fasta_modal(self):
        assert getattr(sc.MultiRecordFastaModal, "_blocks_undo", False) is True

    def test_custom_enzyme_list_modal(self):
        assert getattr(sc.CustomEnzymeListModal, "_blocks_undo", False) is True

    def test_primer_edit_modal(self):
        assert getattr(sc.PrimerEditModal, "_blocks_undo", False) is True

    def test_color_picker_modal(self):
        assert getattr(sc.ColorPickerModal, "_blocks_undo", False) is True

    def test_part_edit_modal(self):
        assert getattr(sc.PartEditModal, "_blocks_undo", False) is True

    def test_species_picker_modal(self):
        assert getattr(sc.SpeciesPickerModal, "_blocks_undo", False) is True

    def test_collections_modal(self):
        assert getattr(sc.CollectionsModal, "_blocks_undo", False) is True

    def test_collection_name_modal(self):
        assert getattr(sc.CollectionNameModal, "_blocks_undo", False) is True

    def test_new_collection_modal(self):
        assert getattr(sc.NewCollectionModal, "_blocks_undo", False) is True

    def test_name_plasmid_modal(self):
        assert getattr(sc.NamePlasmidModal, "_blocks_undo", False) is True

    def test_rename_plasmid_modal(self):
        assert getattr(sc.RenamePlasmidModal, "_blocks_undo", False) is True

    def test_min_primer_binding_modal(self):
        assert getattr(sc.MinPrimerBindingModal, "_blocks_undo", False) is True


class TestBlocksUndoAttrPlacement:
    """Per CLAUDE.md invariant #41: the `_blocks_undo` attr MUST come
    after the docstring, otherwise Python's first-statement detection
    treats it as the docstring and the attribute never lands on the
    class.

    White-box source check on every new modal to catch a contributor
    who pastes the attr above the docstring.
    """

    _NEW_IN_SWEEP12 = (
        "MultiRecordFastaModal",
        "CustomEnzymeListModal",
        "PrimerEditModal",
        "ColorPickerModal",
        "PartEditModal",
        "SpeciesPickerModal",
        "CollectionsModal",
        "CollectionNameModal",
        "NewCollectionModal",
        "NamePlasmidModal",
        "RenamePlasmidModal",
        "MinPrimerBindingModal",
    )

    def test_attr_after_docstring(self):
        for cls_name in self._NEW_IN_SWEEP12:
            cls = getattr(sc, cls_name)
            src = inspect.getsource(cls)
            # The class body starts with the docstring's opening `"""`,
            # closes with `"""`, then the `_blocks_undo: bool = True`
            # line. A regression would have `_blocks_undo:` appear in
            # source BEFORE the docstring's closing triple-quote, or
            # appear inside the docstring text itself.
            # Robust check: split on `_blocks_undo: bool = True` and
            # confirm the preceding region carries a closed docstring.
            assert "_blocks_undo: bool = True" in src, (
                f"{cls_name} lost `_blocks_undo` attr"
            )
            pre = src.split("_blocks_undo: bool = True", 1)[0]
            # The pre-region should contain BOTH the opening and the
            # closing `"""` of the docstring — three triple-quotes
            # would mean the attr landed inside a docstring.
            n_quotes = pre.count('"""')
            assert n_quotes == 2, (
                f"{cls_name}: `_blocks_undo` placement smells off — "
                f"expected exactly 2 `\"\"\"` before the attr, got "
                f"{n_quotes}. Per invariant #41 the attr MUST come "
                f"AFTER the closing docstring quote, not inside it."
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 — reactive/widget hygiene (no source changes; document audit)
# ═══════════════════════════════════════════════════════════════════════════════

class TestReactiveHygieneInvariants:
    """Phase 3 of the sweep produced no code changes — these tests
    document the audited invariants so a regression on any of them
    surfaces immediately.
    """

    def test_only_one_watch_method(self):
        """The codebase relies on Textual reactive auto-rerender;
        explicit `watch_*` methods are reserved for genuine cross-
        widget cascades. Pre-fix audits found exactly ONE such method
        (`PlasmidMap.watch_origin_bp`). A burst of new `watch_*` methods
        is a smell — usually it means someone introduced a reactive
        when a plain attribute would have done.
        """
        import re
        src = inspect.getsource(sc)
        watch_methods = re.findall(r"^\s+def (watch_\w+)", src, flags=re.M)
        # `watch_origin_bp` is the only sanctioned one. If you add a
        # second, document the cascade rationale here.
        assert "watch_origin_bp" in watch_methods, (
            "PlasmidMap.watch_origin_bp is the canonical cross-panel "
            "rotation cascade — don't remove it."
        )
        # Soft cap: anything over 3 deserves audit. Single-purpose
        # watchers stay below this comfortably.
        assert len(watch_methods) <= 3, (
            f"Watch-method count crept to {len(watch_methods)}: "
            f"{watch_methods}. Each `watch_*` is a refresh trigger — "
            f"audit before adding more."
        )

    def test_query_one_outnumbers_query(self):
        """`query_one` should dominate over the generic `query` — the
        latter returns a DOMQuery iterable and is rarely the right
        primitive (caller usually wants the FIRST match, which is
        `query_one`). Pre-fix audit found ZERO `query()` callsites,
        which is the right state.
        """
        src = inspect.getsource(sc)
        n_query_one = src.count("self.query_one(")
        n_query = sum(1 for line in src.splitlines()
                      if "self.query(" in line)
        assert n_query_one >= 100, (
            f"`query_one` usage dropped to {n_query_one} — sanity "
            f"check the audit ran against the right file."
        )
        # `query()` returning a DOMQuery is occasionally legitimate
        # (multi-match iteration), but should stay below 10% of
        # `query_one` usage. Pre-sweep it was zero.
        assert n_query <= max(5, n_query_one // 20), (
            f"`self.query(` callsites jumped to {n_query} vs "
            f"{n_query_one} `query_one` — audit each new caller."
        )
