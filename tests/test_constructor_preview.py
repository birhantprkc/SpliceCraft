"""
test_constructor_preview — E1 dry-run gate for ConstructorModal.

The modular "Save To Library" flow simulates the assembly BEFORE
prompting for a name, so a real IIS-digest failure surfaces
immediately instead of after the user has typed a name (a green
"READY TO CLONE" badge only means the lane's overhangs *validate* —
the real digest can still fail on extra enzyme sites in the entry
vector or a junction the overhang check can't model).

These tests exercise the reorder itself:
  * `_preview_assembly_worker` runs the pure
    `_clone_assembly_into_entry_vector` off the UI thread and routes
    success → the name modal, failure/crash → an error toast with NO
    modal (so the user never names an assembly that can't clone).
  * `_prompt_name_and_save` is the extracted name-prompt step.

The heavy assembly itself is covered on real plasmids by
test_traditional_cloning / test_agent_api; here it is stubbed so BOTH
gate branches are deterministic and the test can't flake on assembly
internals.
"""
from __future__ import annotations

import pytest
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

import splicecraft as sc
from tests.test_smoke import _build_app, TERMINAL_SIZE


def _fake_product(bp: int = 120) -> SeqRecord:
    rec = SeqRecord(Seq("ACGT" * (bp // 4)), id="ASM", name="ASM")
    rec.features = []
    return rec


_PREVIEW_ARGS = dict(
    gid="gb_l0",
    grammar=sc._BUILTIN_GRAMMARS["gb_l0"],
    entry_vector={"name": "TestVec", "gb_text": "LOCUS testvec"},
    parts=[{"name": "P1", "sequence": "ACGT" * 10,
            "oh5": "GGAG", "oh3": "CGCT", "type": "CDS",
            "grammar": "gb_l0", "level": 0}],
    source_level=0,
    bb_key="Alpha1",
    default_name="MyAssembly",
    target_label="TU",
    active_coll="Default",
)


async def _open_modal(app, pilot):
    modal = sc.ConstructorModal()
    await app.push_screen(modal)
    await pilot.pause()
    await pilot.pause(0.05)
    return modal


class TestConstructorPreviewGate:

    @pytest.mark.asyncio
    async def test_prompt_name_and_save_pushes_name_modal(
            self, tiny_record, isolated_library, isolated_parts_bin):
        """The extracted name-prompt step opens the NamePlasmidModal."""
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            modal = await _open_modal(app, pilot)
            modal._prompt_name_and_save(
                _PREVIEW_ARGS["gid"], _PREVIEW_ARGS["grammar"],
                _PREVIEW_ARGS["entry_vector"], _PREVIEW_ARGS["parts"],
                _PREVIEW_ARGS["source_level"], _PREVIEW_ARGS["bb_key"],
                _PREVIEW_ARGS["default_name"],
                _PREVIEW_ARGS["target_label"],
                _PREVIEW_ARGS["active_coll"], 120,
            )
            await pilot.pause()
            assert isinstance(app.screen, sc.NamePlasmidModal)

    @pytest.mark.asyncio
    async def test_preview_success_opens_name_modal(
            self, tiny_record, isolated_library, isolated_parts_bin,
            monkeypatch):
        """A previewable assembly (clone returns a product) opens the
        name prompt — the user only names it AFTER the dry-run passes."""
        monkeypatch.setattr(sc, "_clone_assembly_into_entry_vector",
                            lambda *a, **k: _fake_product())
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            modal = await _open_modal(app, pilot)
            modal._preview_assembly_worker(**_PREVIEW_ARGS)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, sc.NamePlasmidModal)

    @pytest.mark.asyncio
    async def test_preview_returns_none_shows_error_no_modal(
            self, tiny_record, isolated_library, isolated_parts_bin,
            monkeypatch):
        """A failed dry-run (clone returns None) surfaces an error and
        does NOT open the name prompt."""
        monkeypatch.setattr(sc, "_clone_assembly_into_entry_vector",
                            lambda *a, **k: None)
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            modal = await _open_modal(app, pilot)
            fired = []
            modal._on_constructor_save_failed = (
                lambda msg: fired.append(msg))
            modal._preview_assembly_worker(**_PREVIEW_ARGS)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert fired, "failure path did not fire"
            assert not isinstance(app.screen, sc.NamePlasmidModal)

    @pytest.mark.asyncio
    async def test_preview_crash_shows_error_no_modal(
            self, tiny_record, isolated_library, isolated_parts_bin,
            monkeypatch):
        """A crashing dry-run (clone raises) is caught, surfaces an
        error toast, and does NOT open the name prompt."""
        def _boom(*a, **k):
            raise ValueError("bad overhang")
        monkeypatch.setattr(sc, "_clone_assembly_into_entry_vector",
                            _boom)
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            modal = await _open_modal(app, pilot)
            fired = []
            modal._on_constructor_save_failed = (
                lambda msg: fired.append(msg))
            modal._preview_assembly_worker(**_PREVIEW_ARGS)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert fired and "crashed" in fired[0]
            assert not isinstance(app.screen, sc.NamePlasmidModal)
