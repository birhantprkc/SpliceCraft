"""Command palette (Ctrl+P) — re-enabled 2026-06-13 with SpliceCraft's tools
surfaced via `get_system_commands`, Footer indicator kept hidden."""
from __future__ import annotations

import splicecraft as sc


class TestCommandPalette:
    def test_palette_enabled(self):
        assert sc.PlasmidApp.ENABLE_COMMAND_PALETTE is True

    async def test_system_commands_include_tools(self):
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            cmds = list(app.get_system_commands(app.screen))
            titles = {c.title for c in cmds}
            # SpliceCraft tools present...
            for t in ("Open file", "Fetch from NCBI", "Mutato — mutagenesis + Scrub",
                      "Synthesis + operon", "Constructor", "Primer design",
                      "BLAST", "Simulator", "Parts bin", "Sequencing",
                      "Experiments", "Settings"):
                assert t in titles, (t, sorted(titles))
            # ...alongside Textual's built-ins (super() preserved).
            assert len(titles) > 12
            # every command has a callable
            assert all(callable(c.callback) for c in cmds)

    async def test_footer_indicator_stays_hidden(self):
        # The palette works WITHOUT the "^p palette" Footer indicator that the
        # original disable was about — Footer is rendered show_command_palette=False.
        from textual.widgets import Footer
        app = sc.PlasmidApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            foot = app.query_one(Footer)
            assert foot.show_command_palette is False
