"""test_keybinding_terminal_safety — guard against terminal-hostile key
bindings ([PIT-14]).

Two byte-level traps make a binding silently never fire on terminals without
the Kitty keyboard protocol (VTE/Ptyxis, macOS Terminal, basic xterm):

  * `Ctrl+Shift+<letter>` ≡ `Ctrl+<letter>` — the Shift byte is dropped.
  * The control-code keys `Ctrl+I`≡Tab, `Ctrl+H`≡Backspace,
    `Ctrl+M`/`Ctrl+J`≡Enter — Textual delivers these as tab/backspace/enter.

Either way the intended binding never matches and some other action runs
(e.g. Ctrl+Shift+A → Ctrl+A = Select-all; Ctrl+I → Tab = focus-next).

Rule enforced: any action reachable via a hostile key must ALSO be reachable
via a terminal-safe key — the hostile key may only ever be a *bonus alias*.
"""
from __future__ import annotations

import re

import pytest
from textual.binding import Binding

import splicecraft as sc

# Keys that a normal terminal cannot deliver as-typed.
_HOSTILE = re.compile(r"^(ctrl\+shift\+[a-z]|ctrl\+[himj])$")


def _action_keys(cls) -> "dict[str, set[str]]":
    """action name -> set of every key bound to it in cls's OWN BINDINGS
    (expanding the comma form `"c,ctrl+shift+c"`)."""
    out: "dict[str, set[str]]" = {}
    for b in cls.__dict__.get("BINDINGS", []):
        if isinstance(b, Binding):
            keys, action = b.key, b.action
        elif isinstance(b, tuple):
            keys, action = b[0], b[1]
        else:  # bare "key" string form — irrelevant here
            continue
        for k in str(keys).split(","):
            out.setdefault(action, set()).add(k.strip())
    return out


def _own_binding_classes():
    """Every class DEFINED in splicecraft that declares its own BINDINGS."""
    seen = []
    for name in dir(sc):
        obj = getattr(sc, name, None)
        if (isinstance(obj, type)
                and getattr(obj, "__module__", "") == "splicecraft"
                and "BINDINGS" in obj.__dict__):
            seen.append(obj)
    return seen


def test_discovers_binding_classes():
    # Sanity: the sweep actually finds the app + its panels (not an empty set).
    names = {c.__name__ for c in _own_binding_classes()}
    assert {"PlasmidApp", "LibraryPanel"} <= names, names


@pytest.mark.parametrize("cls", _own_binding_classes(),
                         ids=lambda c: c.__name__)
def test_no_action_relies_solely_on_a_hostile_key(cls):
    for action, keys in _action_keys(cls).items():
        hostile = {k for k in keys if _HOSTILE.match(k)}
        if not hostile:
            continue
        safe = keys - hostile
        assert safe, (
            f"{cls.__name__}: action {action!r} is reachable only via "
            f"{sorted(hostile)} — those keys never fire on non-Kitty terminals "
            f"(Ctrl+Shift+<letter> collapses to Ctrl+<letter>; Ctrl+I/H/M/J are "
            f"Tab/Backspace/Enter). Add a terminal-safe primary (plain letter, "
            f"Alt+…, F-key). See [PIT-14]."
        )


def test_add_to_library_primary_is_alt_k():
    keys = _action_keys(sc.PlasmidApp).get("add_to_library", set())
    assert "alt+k" in keys, f"add_to_library lost its Alt+K primary: {keys}"


def test_clear_marks_primary_is_plain_c():
    for cls in (sc.LibraryPanel, sc._BabsModelTable):
        keys = _action_keys(cls).get("clear_marks", set())
        assert "c" in keys, f"{cls.__name__}.clear_marks lost plain 'c': {keys}"


def test_attach_image_primary_is_alt_i():
    keys = _action_keys(sc.ExperimentsScreen).get("attach_image", set())
    assert "alt+i" in keys, f"attach_image lost its Alt+I primary: {keys}"


def test_ctrl_a_is_still_select_all_not_shadowed():
    # Ctrl+A must remain Select-all (the collapse target Ctrl+Shift+A hijacked).
    assert "ctrl+a" in _action_keys(sc.PlasmidApp).get("select_all", set())


def test_help_modal_advertises_terminal_safe_keys():
    """The `?` help must show the keys that WORK, not the collapsed ones — the
    help drifting out of sync with the bindings is what this whole file guards.
    """
    h = sc._HELP_BODY_MD
    # Working primaries the fixes introduced must be present…
    for key in ("Alt+K", "Ctrl+Y", "F6", "`c`"):
        assert key in h, f"help modal no longer shows {key!r}"
    # …and the dead-on-most-terminals keys must not be advertised as primaries.
    for dead in ("Ctrl+Shift+A", "Ctrl+Shift+Z", "Ctrl+Shift+E"):
        assert dead not in h, (
            f"help modal advertises {dead!r}, which silently fails on most "
            f"terminals (see [PIT-14])"
        )


@pytest.mark.asyncio
async def test_alt_k_dispatches_add_to_library():
    """Live Pilot check: pressing Alt+K actually routes to add_to_library.
    (Also proves Textual parses the comma-form `alt+k,ctrl+shift+a` — a
    binding string that never matched "alt+k" would fail here.)"""
    import types

    app = sc.PlasmidApp()
    fired: "dict[str, bool]" = {}
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        app.action_add_to_library = types.MethodType(
            lambda self: fired.__setitem__("k", True), app)
        await pilot.press("alt+k")
        await pilot.pause()
        assert fired.get("k") is True, "Alt+K did not dispatch add_to_library"
        app.exit()


@pytest.mark.asyncio
async def test_plain_c_clears_marks_only_when_table_focused():
    """Plain `c` must (a) fire clear_marks when the library table has focus,
    and (b) NOT hijack the letter when the search box has focus — else you
    couldn't type a 'c' into a plasmid-name search."""
    from textual.widgets import DataTable, Input

    app = sc.PlasmidApp()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        panel = app.query_one("#library", sc.LibraryPanel)

        # (a) table focused → `c` clears the marks
        table = panel.query_one("#lib-table", DataTable)
        panel._marked_ids.add("someid")
        app.set_focus(table)
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert not panel._marked_ids, "plain 'c' did not clear marks under table focus"

        # (b) search input focused → `c` types, marks survive
        search = panel.query_one("#lib-search", Input)
        search.value = ""
        panel._marked_ids.add("someid")
        app.set_focus(search)
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert search.value == "c", f"'c' did not reach the search box: {search.value!r}"
        assert panel._marked_ids == {"someid"}, "'c' cleared marks while typing in search"
        app.exit()
