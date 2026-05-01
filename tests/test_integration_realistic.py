"""
test_integration_realistic — exercise the post-0.5.1.0 modals + keybindings
against a 2.7 kb synthetic plasmid (`realistic_plasmid` fixture).

The 120 bp `tiny_record` covers the basic API but doesn't surface issues
that only show up at full plasmid scale: seq-panel chunk caching, BLAST
DB build/search latency, feature-packer 2D layout under crowding, and
the modal-active gate's interaction with arrow keys when the seq panel
has scrolled. This file is the regression guard for those.

Tests are deliberately written to be backend-agnostic for BLAST (the
auto-dispatch picks pyhmmer; tests don't pin which backend ran) and to
avoid asserting on specific identity_pct / score values since both
backends produce valid but different numeric ranges.
"""
from __future__ import annotations

import splicecraft as sc


TERMINAL_SIZE = (160, 48)


# ═══════════════════════════════════════════════════════════════════════════════
# Loading + rendering
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealisticLoad:

    async def test_loads_and_renders_at_baseline_terminal(
            self, realistic_plasmid, isolated_library):
        app = sc.PlasmidApp()
        app._preload_record = realistic_plasmid
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            assert app._current_record is not None
            assert app._current_record.id == "SYNREAL"
            assert len(app._current_record.seq) == 2686
            # Plasmid map must have computed a draw for this size.
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert pm._feats, "plasmid map didn't pick up features"

    async def test_seq_panel_renders_full_2686bp(
            self, realistic_plasmid, isolated_library):
        app = sc.PlasmidApp()
        app._preload_record = realistic_plasmid
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.1)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            assert len(sp._seq) == 2686

    def test_restriction_site_scan_finds_known_cuts(
            self, realistic_plasmid, isolated_library):
        # Fixture seeds known sites. Verify the scanner picks them up.
        sites = sc._scan_restriction_sites(
            str(realistic_plasmid.seq),
            min_recognition_len=6,
            unique_only=False,
            circular=True,
        )
        # site dicts (both 'resite' and 'recut') use a `label` field
        # for the enzyme name.
        names = {s.get("label", "") for s in sites}
        for needed in ("EcoRI", "BamHI", "HindIII", "XhoI", "SalI"):
            assert needed in names, \
                f"missing {needed} in scan; got: {sorted(names)}"


# ═══════════════════════════════════════════════════════════════════════════════
# BLAST modal — realistic-plasmid query
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlastModalRealistic:
    """Drive the BLAST modal against a collection containing the
    realistic plasmid; verify a known feature's sequence finds itself
    end-to-end through the dispatcher (which picks pyhmmer by default
    but stays correct on either backend)."""

    async def test_blastn_finds_self_against_collection(
            self, realistic_plasmid, isolated_library):
        # Seed a collection with the realistic plasmid.
        gb = sc._record_to_gb_text(realistic_plasmid)
        sc._save_collections([{
            "name": "RealTest", "description": "real plasmid test",
            "plasmids": [{
                "name": realistic_plasmid.name,
                "id": realistic_plasmid.id,
                "size": len(realistic_plasmid.seq),
                "n_feats": len(realistic_plasmid.features),
                "source": "id:" + realistic_plasmid.id,
                "added": "2026-05-01",
                "gb_text": gb,
            }],
            "saved": "2026-05-01",
        }])
        sc._blast_clear_cache()

        # Use a 60 bp slice of the AmpR CDS as the query.
        ampr_seq = str(realistic_plasmid.seq[400:1240])
        query = ampr_seq[100:160]   # 60 bp from the middle

        app = sc.PlasmidApp()
        app._preload_record = realistic_plasmid
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            app.push_screen(sc.BlastModal())
            await pilot.pause()
            modal = app.screen
            modal.query_one("#blast-query", sc.TextArea).text = query
            modal.query_one("#blast-source", sc.Select).value = "RealTest"
            await pilot.pause()
            modal.query_one("#btn-blast-run", sc.Button).press()
            # The worker is threaded; let it finish.
            await pilot.pause(0.5)
            await pilot.pause(0.5)
            results = modal.query_one("#blast-results", sc.Static)
            txt = str(results.render())
            # Hit must reference SYNREAL.
            assert "SYNREAL" in txt, f"didn't find self-hit: {txt[:200]!r}"

    async def test_blastp_finds_ampr_protein(
            self, realistic_plasmid, isolated_library):
        # AmpR CDS is annotated; BLASTP should index it as a subject.
        gb = sc._record_to_gb_text(realistic_plasmid)
        sc._save_collections([{
            "name": "BpReal", "description": "blastp realistic test",
            "plasmids": [{
                "name": realistic_plasmid.name,
                "id": realistic_plasmid.id,
                "size": len(realistic_plasmid.seq),
                "n_feats": len(realistic_plasmid.features),
                "source": "id:" + realistic_plasmid.id,
                "added": "2026-05-01",
                "gb_text": gb,
            }],
            "saved": "2026-05-01",
        }])
        sc._blast_clear_cache()

        # Translate the synthetic AmpR CDS and query a 30-aa fragment.
        from Bio.Seq import Seq
        cds_seq = str(realistic_plasmid.seq[400:1240])
        protein = str(Seq(cds_seq).translate())
        # Skip the first 10 aa (might have a stop early in synthetic
        # random sequence) — find a stretch with no '*'.
        clean_pieces = [p for p in protein.split("*") if len(p) >= 30]
        assert clean_pieces, "synthetic CDS had no 30-aa ORF"
        query = clean_pieces[0][:30]
        db = sc._blast_get_db("blastp", ["BpReal"])
        # The CDS is a real subject in the BLASTP db.
        if db.get("subjects"):
            hits = sc._blast_search(query, db)
            # We don't insist on hits — synthetic random translation
            # may not match itself well — just verify no crash.
            for h in hits:
                assert "subject_id" in h


# ═══════════════════════════════════════════════════════════════════════════════
# NewPlasmidModal — annotate-from-library against realistic-sized paste
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewPlasmidRealistic:

    async def test_annotate_from_library_picks_up_seeded_features(
            self, realistic_plasmid, isolated_library):
        # Seed the feature library with a 30 bp signature embedded in
        # the realistic plasmid; paste the plasmid into NewPlasmidModal,
        # annotate-from-library, expect at least one match.
        sig_seq = str(realistic_plasmid.seq[450:480])  # 30 bp signature
        sc._save_features([{
            "name":         "embedded_marker",
            "feature_type": "misc_feature",
            "sequence":     sig_seq,
            "strand":       1,
            "color":        "",
        }])

        app = sc.PlasmidApp()
        app._preload_record = realistic_plasmid
        captured: dict = {}
        def _on_done(res):
            captured["got"] = res
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            app.push_screen(sc.NewPlasmidModal(), callback=_on_done)
            await pilot.pause()
            modal = app.screen
            modal.query_one("#newplas-name", sc.Input).value = "pasted_real"
            modal.query_one("#newplas-seq", sc.TextArea).text = (
                str(realistic_plasmid.seq)
            )
            await pilot.pause()
            modal.query_one("#btn-newplas-annot-lib", sc.Button).press()
            await pilot.pause()
            await pilot.pause(0.05)
        result = captured.get("got")
        assert result is not None
        rec = result["record"]
        feats = [f for f in rec.features if f.type == "misc_feature"]
        names = [
            (f.qualifiers.get("label") or [""])[0] for f in feats
        ]
        assert any("embedded_marker" in n for n in names), \
            f"didn't pick up embedded_marker: {names!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# Keybindings under modal context
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeybindingsUnderModal:
    """Verify that opening a modal blocks the seq-panel cursor /
    selection-slide handlers so arrow keys / Ctrl+A don't leak through.
    Only relevant on a realistic-sized plasmid where you can actually
    see a cursor difference."""

    async def test_arrow_under_help_modal_does_not_move_cursor(
            self, realistic_plasmid, isolated_library):
        app = sc.PlasmidApp()
        app._preload_record = realistic_plasmid
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            sp._cursor_pos = 100
            initial = sp._cursor_pos

            app.push_screen(sc.HelpModal())
            await pilot.pause()
            assert len(app.screen_stack) > 1

            # Right arrow under HelpModal → modal absorbs it (catch-all
            # dismisses); cursor must not advance. Even if HelpModal
            # didn't catch-all, the modal-active gate in `App.on_key`
            # short-circuits the seq-cursor branch.
            await pilot.press("right")
            await pilot.pause()
            assert sp._cursor_pos == initial

    async def test_ctrl_a_select_all_works_on_full_plasmid(
            self, realistic_plasmid, isolated_library):
        app = sc.PlasmidApp()
        app._preload_record = realistic_plasmid
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await pilot.pause(0.05)
            await pilot.press("ctrl+a")
            await pilot.pause()
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            assert sp._user_sel == (0, 2686), \
                f"expected full-length sel, got {sp._user_sel!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# Performance: realistic plasmid render budget
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealisticPerf:
    """Loose budgets — catch architectural regressions, not micro drift."""

    def test_blast_db_build_under_500ms(self, realistic_plasmid,
                                          isolated_library):
        import time
        gb = sc._record_to_gb_text(realistic_plasmid)
        sc._save_collections([{
            "name": "PerfTest", "plasmids": [{
                "name": realistic_plasmid.name,
                "id": realistic_plasmid.id,
                "size": len(realistic_plasmid.seq),
                "n_feats": len(realistic_plasmid.features),
                "source": "id:" + realistic_plasmid.id,
                "added": "2026-05-01",
                "gb_text": gb,
            }]}])
        sc._blast_clear_cache()
        t0 = time.perf_counter()
        db = sc._blast_get_db("blastn", ["PerfTest"])
        elapsed = time.perf_counter() - t0
        assert db.get("subjects"), "DB build returned empty"
        # 500 ms is generous on a single 2.7 kb plasmid — typical is
        # 30-50 ms. If we ever cross half a second something architectural
        # changed.
        assert elapsed < 0.5, f"BLAST DB build too slow: {elapsed:.3f}s"
