# CLAUDE.md — AI Agent Context for SpliceCraft

Agent handoff. Read before touching the codebase.

Developed by a human bioinformatician + Claude. **Single-file architecture** — entire app is `splicecraft.py` (~23,000 lines). Intentional: keeps the codebase greppable.

## What is SpliceCraft?

Terminal-based circular plasmid map viewer, sequence editor, and cloning/mutagenesis workbench. Python 3.10+ / Textual / Biopython. Unicode braille-dot maps, per-base sequence panel, restriction overlays, collection-driven plasmid library, Golden Braid L0 + MoClo grammars, Primer3-backed primer design, SOE-PCR mutagenesis, in-process BLASTN/BLASTP/HMMscan via pyhmmer.

**Repo:** `github.com/Binomica-Labs/SpliceCraft` · **PyPI:** `splicecraft` · `__version__` lives in `splicecraft.py` and `pyproject.toml`.

## How to run

```bash
python3 splicecraft.py                       # empty canvas
python3 splicecraft.py L09137                # fetch pUC19 from NCBI
python3 splicecraft.py myplasmid.gb          # local GenBank (.gb/.gbk/.dna)
python3 -m pytest -n auto -q                 # full suite (~3 min on 8 cores)
python3 -m pytest tests/test_dna_sanity.py   # biology only (< 2 s — fast inner loop)
./release.py X.Y.Z                           # bump, test, build, tag, push (PyPI via OIDC)
```

End users: `pipx install splicecraft && splicecraft`.

Logs: `~/.local/share/splicecraft/logs/splicecraft.log` (override `$SPLICECRAFT_LOG`). Every line prefixed with 8-char session ID.

## Sacred invariants (DO NOT BREAK)

Each has at least one test in `tests/`. Touching `_scan_restriction_sites`, `_rc`, `_iupac_pattern`, `_translate_cds`, `_bp_in`, `_feat_len`, the wrap-midpoint formula, or `_rebuild_record_with_edit` will trip the relevant tests immediately.

1. **Palindromic enzymes are scanned forward only.** Bottom-strand hit emitted as a `recut`. Scanning both strands double-counts every site.
2. **Reverse-strand resite positions use the forward coordinate.** A reverse hit at `p` (after RC) is stored as `p`, not `n - p - site_len`. Cut maps via `site_len - 1 - fwd_cut`.
3. **`_rc()` handles full IUPAC** — translates R/Y/W/S/M/K/B/D/H/V/N via `_IUPAC_COMP`, not just ACGT.
4. **IUPAC regex patterns are cached** in `_PATTERN_CACHE`. Don't recompile per-scan.
5. **Circular wrap midpoint:** `arc_len = (end - start) % total; mid = (start + arc_len // 2) % total`. Naive `(start + (end - start) // 2) % total` puts the label opposite the actual arc.
6. **Circular wrap RE scan** scans `seq + seq[:max_site_len-1]`. Each wrap hit emits **two resite pieces** (labeled tail `[p, n)` + unlabeled head `[0, (p+site_len) - n)`) and **one recut** at `(p + fwd_cut) % n`. Filtering code that counts resites must count only labeled pieces.
7. **Data-file saves always back up.** Always go through `_safe_save_json` (`.bak` + `tempfile.mkstemp` + `os.fsync` + `os.replace`). Schema envelope `{"_schema_version": 1, "entries": [...]}`; `_extract_entries` accepts legacy bare-list (pre-0.3.1). Never bypass.
8. **Wrap-aware feature length.** Use `_feat_len(start, end, total)` — returns `(total - start) + end` when `end < start`, else `end - start`. All sort keys, length displays, biology checks must route through it.
9. **Wrap-feature integrity in record edits.** `int(CompoundLocation.start)` returns `min(parts.start)` and silently flattens wrap features. `_rebuild_record_with_edit` per-part shifts wrap features and only collapses to FeatureLocation when 1 part survives.
10. **Undo snapshots are deepcopied.** `_push_undo`, `_action_undo`, `_action_redo` all `deepcopy(self._current_record)`.

## Known pitfalls

1. **Bare `except` is forbidden.** Use narrow types (`NoMatches`, `ET.ParseError`, `(OSError, json.JSONDecodeError)`). Bare `except Exception` is reserved for `@work` thread bodies — and always `_log.exception` there.
2. **User-facing errors:** `self.notify(...)` or `Static.update("[red]...[/]")`. Never raw tracebacks. Diagnostic detail goes to `_log.exception`.
3. **Wrapped features (`end < start`) are first-class.** Use `_bp_in()` / `_feat_len()` for any distance, midpoint, or "is bp inside" check. See invariants #5, #6, #8, #9.
4. **Cache keys use `id(...)` of feature lists.** Correct only because lists are *reassigned* on load, not mutated. Don't start mutating `self._feats` in-place.
5. **Textual reactive auto-invalidation requires assignment, not mutation.** `self._feats = new_list` triggers refresh; `self._feats.append(x)` does not.
6. **Primer3 is linear-only.** For wrap regions, rotate template to `seq[start:] + seq[:start]`, then unrotate via `(coord + rotation) % total`. See `_design_detection_primers`.
7. **`_source_path` survives in-place edits.** Cleared only when `clear_undo=True` (fresh loads). Otherwise Ctrl+S after primer-add or **Discard-from-library** still targets the original `.gb` file. `_discard_changes` explicitly stashes/restores `_source_path`.
8. **NCBI XML responses go through `_safe_xml_parse`.** Rejects DOCTYPE/ENTITY before `ET.fromstring`. Don't add a new NCBI endpoint without it.
9. **Migration runs in `App.compose()`, not `on_mount`.** Textual mount fires leaves→root, so `App.on_mount` runs AFTER `LibraryPanel.on_mount`. Collections + active-collection setup must be done before children mount or the panel reads stale state.
10. **`_save_library` mirrors to the active collection.** Every panel CRUD writes BOTH `plasmid_library.json` and `collections.json`. Routing a write around `_save_library` (e.g. `_restore_library_from_active_collection`) bypasses the mirror; do that only when the collection IS the source.
11. **Wrap-CDS rendering uses `_orig_start`/`_orig_end`.** `_feats_in_chunk` splits wrap features into linear half-features for chunk rendering; CDS halves carry the original coords as `_orig_start` / `_orig_end`. Codon-midpoint math, AA translation, AA-click detection must read `f.get("_orig_start", f["start"])`. Reading the half-local `f["start"]` (= 0 for head halves) gives the wrong reading frame.
12. **`_re_highlight` schema (0.4.5+):** `start, end, top_cut_bp, bottom_cut_bp, color, name`. Legacy `fwd_cut_bp` / `rev_cut_bp` keys are gone. Resites with `cut == -1` fall back to plain `black on white`.
13. **Map rotation keys live on `PlasmidMap.BINDINGS`, not `App.BINDINGS`.** Don't add `priority=True` at App level — rotations would fire from modal screens. App-level `on_key` skips arrow / Enter when a `DataTable`, `Input`, or `PlasmidMap` is focused.
14. **Ctrl+Shift+C is functionally an alias for Ctrl+C** in most terminals (both ETX, 0x03). Alt+C is the actual reverse-complement-copy trigger.
15. **`PlasmidApp.on_key` and `on_click` early-return when `len(screen_stack) > 1`** so seq-panel cursor moves / RE-highlight clears can't fire underneath a modal. Ctrl+Z / Ctrl+Y are above this guard.
16. **`_blast_get_db` LRU is invalidated by `_save_collections`** via `globals().get("_blast_clear_cache")()`. Any new collection-mutation path that doesn't go through `_save_collections` must call `_blast_clear_cache()` manually.
17. **Cache contracts.** `_load_collections`, `_load_features`, `_load_custom_grammars` deepcopy on read so caller-side mutations of returned dicts don't poison the cache. New persisted libraries with mutable callers should follow the same convention.

## Persistent user preferences

User-preference toggles persist across sessions via `settings.json`.
Adding one is mechanical:

1. Class-level annotation on `PlasmidApp` with the default value (e.g. `_my_setting: bool = True`).
2. Hydrate in `PlasmidApp.compose()` next to the existing block — `self._my_setting = bool(_get_setting("my_setting", True))`. **`compose()` not `on_mount`** because Textual fires mount events leaves→root, so by the time `on_mount` runs the children have already read stale defaults.
3. In `action_toggle_my_setting`, call `_set_setting("my_setting", self._my_setting)` after flipping.
4. Surface in the Settings menu (`MenuBar.MENUS` between File and Edit; populated by the `Settings` entry in `PlasmidApp.open_menu`'s `menus` dict).

Currently persisted: `show_feature_tooltips`, `click_debug`, `show_restr`, `restr_unique_only`, `restr_min_len`, `show_connectors`, `map_mode`, `active_collection`, `active_grammar`. The `show_connectors` and `map_mode` toggles need a deferred apply via `_pending_show_connectors` / `_pending_map_mode` because their target widgets aren't composed yet when `compose()` runs; `on_mount` reads the pending values once the children exist.

## Pairwise alignment + Plasmidsaurus ingestion (0.5.3+)

Two-stage pipeline:

1. **Zip ingestion** — `_list_gbk_members_in_zip(path)` lists `.gbk` / `.gb` / `.genbank` members; `_extract_gbk_member(path, name)` reads one out as text. Both are size-capped (`_PLASMIDSAURUS_ZIP_MAX_BYTES = 500 MB`, `_PLASMIDSAURUS_MEMBER_MAX_BYTES = 50 MB`, `_PLASMIDSAURUS_MAX_MEMBERS = 2000`) so a malformed archive can't OOM the picker. Dotfile members and directories are filtered.
2. **Alignment** — `_pairwise_align(query, target, mode='global'|'local')` wraps `Bio.Align.PairwiseAligner`. Returns `{mode, score, identity_pct, aligned_q, aligned_t, n_matches, n_mismatches, n_gaps, q_len, t_len}`. Length-capped at `_PAIRWISE_MAX_LEN = 200_000`. **Aligned strings come from `Alignment[0]` / `Alignment[1]`**, NOT `format()`-parsing — the text format wraps at 60 cols with coordinate prefixes which is fragile to parse.

Entry point: `File → Align sequencing run (Plasmidsaurus .zip)…` → `PlasmidsaurusAlignModal` → on submit pushes `AlignmentScreen` (full-screen viewer with target features lane + parallel target / query rows + match track + mismatch-red highlighting). Both modal and screen are in `splicecraft.py` near the FASTA file picker.

Future expansion (already designed for): a Plasmidsaurus API key tab in the same modal that downloads run zips directly. Same downstream alignment + visualisation pipeline; only the ingestion source changes.

## Architecture pointers

`splicecraft.py` is laid out top-to-bottom roughly: imports + persistence helpers → enzyme catalog + IUPAC + scanner + 2D feature packer + seq-panel renderer → GenBank I/O → `_Canvas` / `_BrailleCanvas` / `PlasmidMap` / `FeatureSidebar` → `LibraryPanel` → `SequencePanel` → core modals → grammars + settings → codon registry + Kazusa + mutagenesis → feature-library workbench → parts bin → domesticator + constructor → mutagenize modal → primer design → small modals → `PlasmidApp` (controller, keybindings, undo stashes, autosave, `@work` threads) → `main()`.

Use `grep -n "^class \|^def " splicecraft.py` for an authoritative live map. Test files are 1:1 named after the subsystem they cover.

## Conventions

- **Workers:** `@work(thread=True)`, `try / except Exception as exc / _log.exception`, push friendly message via `call_from_thread`. Stale-record guard: capture `self._current_record` identity at entry, compare in callback.
- **JSON libraries:** envelope schema v1. Filter `isinstance(entry, dict)` after load. Add new files to `_protect_user_data` in `tests/conftest.py` and to `_check_data_files`. Cover corruption recovery in `test_data_safety.py`.
- **Modals:** subclass `ModalScreen[ReturnType]`, dismiss with result. Add a row to `test_modal_boundaries.py::_MODAL_CASES` (every modal must fit in 160×48).
- **Tests:** cross-validate against Biopython where biological. No network, no real files (autouse `_protect_user_data` fixture monkeypatches every `_*_FILE` path). Async tests use `async with app.run_test(size=...)` with a double `await pilot.pause()` for `call_after_refresh`.
- **Regression guards** cite the date in their docstring (`# Regression guard for 2026-MM-DD fix`).

## Sister project (ScriptoScope)

`/home/seb/proteoscope/scriptoscope.py` (~8,600 lines) — same author, same single-file convention. Patterns to crib if seq-panel renders blow the 33 ms/frame budget: thread-local `Console` for `_text_to_content`; two-level render cache (`_seq_render_cache` + `_content_cache`, LRU via `OrderedDict.move_to_end`); `@lru_cache(1)` availability probes for optional CLI tools.

User is undecided whether to merge SpliceCraft / ScriptoScope / MitoShift / RefHunter / molCalc into one Textual app with modes. Either is viable — single-file convention keeps the option open.

## For future agents

1. Read this file first, then `git log --oneline` for recent context.
2. `python3 -m pytest -n auto -q` before and after any change. `tests/test_dna_sanity.py` (< 2 s) is the fast inner loop.
3. Don't break sacred invariants. Don't bypass `_safe_save_json`. Don't add bare `except`.
4. Eyeball real-world behaviour on pUC19 (`L09137`) and pACYC184 (`MW463917.1`).
5. Past fix history is in git — `git show <hash>` beats stale prose in this file.
