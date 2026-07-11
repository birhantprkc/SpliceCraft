"""
test_agent_settings_refresh — #7: an agent `set-setting` that changes a
CACHED display flag takes effect on the live app immediately, without the
user having to flip the matching in-app checkbox.

`set-setting` persists settings.json directly and never runs the in-app
checkbox handlers (`_on_settings_show_restr` / `_on_settings_connectors`),
so the flags they normally keep in sync go stale:
  * `RestrictionController._show_restr` (+ filter values), and
  * the seq-panel / map `_show_connectors`.

`_agent_apply_live_refresh(app, "settings")` (fired after every set-setting
write) must re-read those flags before repainting. These tests drive the
refresh directly on the UI thread and assert the cached flags flip.
"""
from __future__ import annotations

import pytest

import splicecraft as sc
from tests.test_smoke import _build_app, TERMINAL_SIZE


class TestAgentSettingsLiveRefresh:

    @pytest.mark.asyncio
    async def test_show_restr_flag_resyncs(
            self, tiny_record, isolated_library):
        sc._set_setting("show_restr", False)   # deterministic start state
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            # Controller cached the default (False) when it was constructed.
            assert app.restr._show_restr is False
            # Agent writes the setting directly — no in-app checkbox fires.
            sc._set_setting("show_restr", True)
            sc._agent_apply_live_refresh(app, "settings")
            await pilot.pause()
            assert app.restr._show_restr is True

    @pytest.mark.asyncio
    async def test_show_connectors_flag_resyncs_both_panels(
            self, tiny_record, isolated_library):
        sc._set_setting("show_connectors", False)
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            sp = app.query_one("#seq-panel", sc.SequencePanel)
            pm = app.query_one("#plasmid-map", sc.PlasmidMap)
            assert sp._show_connectors is False
            assert pm._show_connectors is False
            sc._set_setting("show_connectors", True)
            sc._agent_apply_live_refresh(app, "settings")
            await pilot.pause()
            assert sp._show_connectors is True
            assert pm._show_connectors is True

    @pytest.mark.asyncio
    async def test_reload_from_settings_reads_filter_values(
            self, tiny_record, isolated_library):
        # The restriction filter values (min-len, unique-only) re-sync too,
        # so an agent that tightens the filter gets a correct next scan.
        app = _build_app(tiny_record, isolated_library)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause()
            sc._set_setting("restr_min_len", 4)
            sc._set_setting("restr_unique_only", False)
            app.restr.reload_from_settings()
            assert app.restr._restr_min_len == 4
            assert app.restr._restr_unique_only is False
