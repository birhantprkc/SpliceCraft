# SpliceCraft release checklist

The bits `pytest -n auto` can't catch. Walk top-to-bottom before any
substantial release tag. Anything that fails here that wasn't already in
`tests/` should land as a regression guard in the same PR as the fix.

## Per-terminal smoke matrix

The 0.5.5.x churn (Ctrl+digit → Alt+digit → F1-F5) was caused by terminals
that intercept keystrokes before they reach the app. Every terminal in this
matrix should be exercised on a clean install of the published package
(`pipx install splicecraft==X.Y.Z`) — not the dev tree.

For each terminal, load pUC19 (`splicecraft L09137`) and verify:

| Action                              | Expected                                          |
|-------------------------------------|---------------------------------------------------|
| `?`                                 | HelpModal opens                                   |
| `F1` / `F2` / `F3` / `F4` / `F5`    | Cycles to single-panel layouts; `F5` restores      |
| `r`                                 | RE sites toggle on/off                             |
| `+` / `-` / `0` / `[` / `]`         | Linear-view zoom + pan (after `v` to switch view)  |
| `Alt+M`                             | Click-debug toast; modifiers reported correctly    |
| Shift+click on a feature            | Selection extends from anchor                      |
| Ctrl+click on a feature             | Same as Shift+click (synonym for terminals that intercept Shift) |
| `Alt+C` after selecting bases       | RC of selection lands in clipboard                 |
| `Ctrl+S` after a feature add        | Saves to original `.gb` (or `_source_path`)        |
| `Ctrl+Z` / `Ctrl+Y`                 | Undo / redo round-trip                             |
| Hover over a `primer_bind` feature  | Tooltip shows label + range; weak primers also show "⚠ Weak binding" |
| Settings → Min primer binding       | Cycles 15 → 18 → 20 → 10 → 12 → 15; weak ⚠ glyphs update on the seq panel without a record reload |

Terminals to cover (mark each row):

- [ ] macOS Terminal.app (Sonoma+)
- [ ] iTerm2 (latest stable)
- [ ] Windows Terminal (latest, Powerline glyphs enabled)
- [ ] GNOME Terminal (Ubuntu 24.04 LTS)
- [ ] Konsole (KDE Plasma 6)
- [ ] Alacritty (cross-platform; tab bar disabled — Ctrl+digit reaches the app)
- [ ] WSL2 default terminal (Windows host, glibc Ubuntu)

If any terminal fails on a Ctrl/Shift/Alt binding, capture the exact
keystroke that arrived via `Alt+M` click-debug and either rebind to a
non-conflicting key (precedent: 0.5.5.x → F1-F5) or document the
limitation in `?` Help.

## Chromosome-scale eyeball

A 5 MB record is in `tests/test_smoke.py::TestLazyChunkRender` but only
under `pytest`. Verify the user-facing flow:

1. Download a bacterial chromosome (e.g. `splicecraft U00096.3` →
   E. coli K-12 MG1655, ~4.6 Mb). Confirm `LargeFileConfirmModal`
   appears and "No" focused by default.
2. Press Yes. First-render budget: < 5 s on a 2018-vintage laptop;
   linear viewport opens auto-fogged to ~50 kb window.
3. `+` / `-` zoom; `[` / `]` pan; `0` reset. No frozen frames.
4. Switch to circular (`v`); confirm the map renders without hanging
   (large records pay a one-time braille-canvas cost).
5. Switch to seq panel only (`F4`). Cursor moves should still feel
   instant (lazy chunk render kicks in).
6. Open the feature sidebar (`F3`); scroll. Sort by length, then by
   start position. No noticeable lag.
7. Add a primer at a random position via Primers menu; verify it
   appears + the seq panel paints the bound bar.
8. `Ctrl+S` and confirm a `.gb` round-trips without corruption (open
   the saved file in another tool — the popular commercial plasmid
   editor's free viewer is the canonical second pair of eyes).

## Pre-tag release-script dry run

```bash
git diff master..HEAD                              # eyeball the changelog delta
python3 -m pytest -n auto -q                       # one final pass on a clean checkout
pip install --user dist/splicecraft-X.Y.Z-*.whl    # install + run the published wheel
```

## Documentation freeze

- [ ] `CHANGELOG.md` section for the release reads as a coherent
      "what's in this release" overview
- [ ] `README.md` screenshot is current (status field, weak-primer ⚠,
      Plasmidsaurus modal)
- [ ] `CLAUDE.md` line-count + persisted-settings list are up to date
      (drift fix landed 2026-05-05)
- [ ] PyPI long_description renders in Markdown (verified at
      `https://pypi.org/project/splicecraft/` after upload)

## Agent-API surface — sanity checks

The agent surface is now 60+ endpoints. Smoke a representative
slice on a real running server (not just pytest):

- `POST /list-plasmid-statuses` — vocabulary discovery
- `POST /set-plasmid-status` `{name, status}` — round-trip a workflow tag
- `POST /list-entry-vectors` — empty by default
- `POST /set-entry-vector` `{grammar_id, name, gb_text}` — set + verify via `get-entry-vector`
- `POST /update-primer` `{idx, label, primer_seq}` — reject on non-primer feature
- `POST /get-settings` — every allowlisted toggle present, no infrastructure caches
- `POST /set-setting` `{key: "min_primer_binding", value: 18}` — round-trips through
  `_get_setting`
