# CLAUDE.md — AI Agent Context for SpliceCraft

Agent handoff. Read before touching the codebase.

Bioinformatician + Claude. **Near-single-file architecture** — `splicecraft.py` (~65k lines) + extracted biology module `splicecraft_biology.py` + stdlib-only sidecar `splicecraft_cli.py`. Single-file constraint is intentional (greppable); biology extraction is the first deliberate exception (pure functions/constants, no `PlasmidApp` coupling). See `CONTRIBUTING.md` three-test rule.

## What is SpliceCraft?

Terminal-based circular plasmid map viewer, sequence editor, cloning/mutagenesis workbench. Python 3.10+ / Textual / Biopython. Unicode braille-dot maps, per-base sequence panel, restriction overlays, collection-driven library, Golden Braid L0 + MoClo grammars, Primer3, SOE-PCR mutagenesis, in-process BLASTN/BLASTP/HMMscan via pyhmmer.

**Repo:** `github.com/Binomica-Labs/SpliceCraft` · **PyPI:** `splicecraft` · `__version__` in `splicecraft.py` and `pyproject.toml`.

## How to run

```bash
python3 splicecraft.py                       # empty canvas (or auto-loads first library entry)
python3 splicecraft.py L09137                # fetch pUC19 from NCBI
python3 splicecraft.py myplasmid.gb          # local GenBank (.gb/.gbk/.dna)
python3 -m pytest -n auto -q                 # full suite (~5–6 min on 8 cores)
python3 -m pytest tests/test_dna_sanity.py   # biology only (<2 s, fast inner loop)
./release.py X.Y.Z                           # bump, test, build, tag, push (PyPI via OIDC)
```

End users: `pipx install splicecraft && splicecraft`.

No-arg launch shows empty canvas (or first library entry). Demo plasmid (`_make_demo_record` / `_DEMO_PLASMID_SEQ`) kept in source for tests but `main()` no longer pre-sets `_preload_demo_record`. First-run NCBI seed (`_seed_default_library` → MW463917.1) suppressed via `_skip_seed = True`. Dev builds flip `_skip_seed = False` for auto-seed.

Logs: `~/.local/share/splicecraft/logs/splicecraft.log` (override `$SPLICECRAFT_LOG`). 8-char session ID prefix per line.

## Where to find more (grep first, ahead of dispatch)

The long-form sweep history and subsystem deep-dives live in split files. **Each entry has a `[INV-NN]` or `[SUB-xxx]` tag — grep before editing the matching subsystem.** Both files start with a tag→topic table for fast lookup.

| File | Holds | Grep when touching |
|---|---|---|
| `docs/invariants.md` | Sweep history [INV-36]…[INV-63]; every sweep #5–#22 fix, feature-area sacred contracts | concurrency races, agent endpoints, restore/backup, master delete, synthesis composer, protein tab, collision modals, entry-vector auto-detect, cross-platform |
| `docs/subsystems.md` | [SUB-plasmidsaurus], [SUB-experiments], [SUB-gels] | sequencing zip ingestion, lab notebook, gel snapshots |
| `docs/PLATFORMS.md` | Supported OS / terminal matrix | cross-platform behaviour, terminal capability checks |
| `docs/agent-api.md`, `docs/features.md` | User-facing reference docs | adding/renaming endpoints, user-visible features |

**Rule:** before any non-trivial edit, run `grep -ni '<keyword>' docs/invariants.md docs/subsystems.md`. If a relevant `[INV-NN]` / `[SUB-xxx]` exists, read it first. Dispatching a sub-agent? Quote the matching tag in the prompt so it knows where to look.

## Sacred invariants (DO NOT BREAK)

Each has at least one test in `tests/`. Touching `_scan_restriction_sites`, `_rc`, `_iupac_pattern`, `_translate_cds`, `_bp_in`, `_feat_len`, the wrap-midpoint formula, or `_rebuild_record_with_edit` trips tests immediately.

1. **Palindromic enzymes scanned forward only.** Bottom-strand hit emitted as `recut`. Scanning both strands double-counts.
2. **Reverse-strand resite positions use forward coordinate.** Reverse hit at `p` (after RC) stored as `p`, not `n - p - site_len`. Cut maps via `site_len - 1 - fwd_cut`.
3. **`_rc()` handles full IUPAC** via `_IUPAC_COMP`, not just ACGT.
4. **IUPAC regex patterns cached** in `_PATTERN_CACHE`.
5. **Circular wrap midpoint:** `arc_len = (end - start) % total; mid = (start + arc_len // 2) % total`. Naive form puts label opposite actual arc.
6. **Circular wrap RE scan** scans `seq + seq[:max_site_len-1]`. Each wrap hit emits **two resite pieces** (labeled tail `[p, n)` + unlabeled head `[0, (p+site_len) - n)`) and **one recut** at `(p + fwd_cut) % n`. Resite-counting code must count only labeled pieces.
7. **Data-file saves always back up.** Always go through `_safe_save_json` (`.bak` + `tempfile.mkstemp` + `os.fsync` + `os.replace`). Schema envelope `{"_schema_version": 1, "entries": [...]}`; `_extract_entries` accepts legacy bare-list (pre-0.3.1). **`_safe_save_json` re-raises on failure** so callers can notify — silent swallow used to desync UI from disk.
8. **Wrap-aware feature length.** Use `_feat_len(start, end, total)` — returns `(total - start) + end` when `end < start`. All sort keys, length displays, biology checks route through it.
9. **Wrap-feature integrity in record edits.** `int(CompoundLocation.start)` returns `min(parts.start)` and silently flattens. `_rebuild_record_with_edit` per-part shifts wrap features and only collapses to FeatureLocation when 1 part survives.
10. **Undo snapshots deepcopied.** `_push_undo`, `_action_undo`, `_action_redo` all `deepcopy(self._current_record)`.

## Known pitfalls

1. **Bare `except` forbidden.** Use narrow types (`NoMatches`, `ET.ParseError`, `(OSError, json.JSONDecodeError)`). Bare `except Exception` reserved for `@work` thread bodies — always `_log.exception` there.
2. **User-facing errors:** `self.notify(...)` or `Static.update("[red]...[/]")`. Never raw tracebacks. Diagnostic detail → `_log.exception`.
3. **Wrapped features (`end < start`) first-class.** Use `_bp_in()` / `_feat_len()` for any distance, midpoint, or membership check.
4. **Cache keys use `id(...)` of feature lists.** Correct only because lists are *reassigned* on load, not mutated. Don't start mutating `self._feats` in-place.
5. **Textual reactive auto-invalidation requires assignment, not mutation.** `self._feats = new_list` triggers refresh; `.append(x)` does not.
6. **Primer3 is linear-only.** For wrap regions, rotate template to `seq[start:] + seq[:start]`, then unrotate via `(coord + rotation) % total`. See `_design_detection_primers`.
7. **`_source_path` survives in-place edits.** Cleared only when `clear_undo=True` (fresh loads). `_discard_changes` explicitly stashes/restores `_source_path`.
8. **NCBI XML routes through `_safe_xml_parse`.** Rejects DOCTYPE/ENTITY before `ET.fromstring`.
9. **Migration runs in `App.compose()`, not `on_mount`.** Textual mount fires leaves→root; `App.on_mount` runs AFTER `LibraryPanel.on_mount`. Collections / active-collection setup must happen before children mount.
10. **`_save_library` mirrors to active collection.** Every panel CRUD writes BOTH `plasmid_library.json` and `collections.json`. Routing around `_save_library` (e.g. `_restore_library_from_active_collection`) bypasses the mirror; do that only when the collection IS the source.
11. **Wrap-CDS rendering uses `_orig_start`/`_orig_end`.** `_feats_in_chunk` splits wrap features into linear half-features; CDS halves carry original coords as `_orig_start` / `_orig_end`. Codon-midpoint math, AA translation, AA-click detection must read `f.get("_orig_start", f["start"])`. Half-local `f["start"]` (= 0 for head halves) gives wrong reading frame.
12. **`_re_highlight` schema (0.4.5+):** `start, end, top_cut_bp, bottom_cut_bp, color, name`. Legacy `fwd_cut_bp`/`rev_cut_bp` gone. Resites with `cut == -1` fall back to plain `black on white`.
13. **Map rotation keys live on `PlasmidMap.BINDINGS`.** Not `App.BINDINGS` — rotations would fire from modals. App-level `on_key` skips arrow / Enter when `DataTable`, `Input`, or `PlasmidMap` is focused.
14. **Ctrl+Shift+C is functionally an alias for Ctrl+C** (both ETX, 0x03). Alt+C is the actual RC-copy trigger.
15. **`PlasmidApp.on_key`/`on_click` early-return when `len(screen_stack) > 1`** so seq-panel cursor / RE-highlight clears can't fire under modal. Ctrl+Z / Ctrl+Y above this guard.
16. **`_blast_get_db` LRU invalidated by `_save_collections`** via `globals().get("_blast_clear_cache")()`. Any new collection-mutation path not going through `_save_collections` must call `_blast_clear_cache()` manually.
17. **Cache contracts (deepcopy on BOTH read AND save).** `_load_library` / `_load_collections` / `_load_features` / `_load_custom_grammars` / `_load_parts_bin` / `_load_primers` deepcopy on read; corresponding `_save_*` deepcopy when re-seating the cache. Without both halves, a caller editing the list it just saved leaks post-save mutations into the next reader.
18. **Trademark scrub.** `.dna` is the popular commercial plasmid editor's binary format. Code identifiers use `CommercialSaaS` / `commercialsaas` / `_BIOPYTHON_DNA_FMT`. BioPython API string and 8-byte cookie magic stored hex-encoded as `_BIOPYTHON_DNA_FMT` and `_COMMERCIALSAAS_COOKIE_MAGIC` so trademarked text never appears verbatim. User-facing prose says "popular commercial plasmid editor file format".
19. **Untrusted XML routes through `_safe_xml_parse`.** Includes NCBI responses AND `.dna` history packets (`_parse_commercialsaas_history`).
20. **Network reads size-capped.** PyPI (`_PYPI_MAX_RESPONSE_BYTES`), NCBI (`_NCBI_MAX_RESPONSE_BYTES`), Kazusa (`_KAZUSA_MAX_RESPONSE_BYTES`). Any new HTTP fetch must follow `resp.read(MAX + 1)` + bail-if-exceeded.
21. **`_extract_commercialsaas_history_xml` uses streaming LZMA decompress** with `max_length=cap+1`.
22. **`_dna_sidecar_path` strips `..`/dot-only/NUL** via `Path(...).name` after replacing separators. Don't loosen — `entry_id` is user-controlled.
23. **`_safe_load_json` size-capped at `_SAFE_LOAD_JSON_MAX_BYTES` (1 GB).** Distinct from the 50 MB `_BULK_IMPORT_MAX_BYTES` agent-API cap (different threat models).
24. **`_h_load_file` agent endpoint size-capped at `_BULK_IMPORT_MAX_BYTES` (50 MB)** with `force=true` override.
25. **`_excise_fragment_pair` enforces exactly-2 cuts on circular plasmids.** ≥3 cuts surfaces error rather than ambiguous fragments. Sacred — restriction-cloning correctness depends on this.
26. **GFF3 export off-by-one.** `_record_to_gff3` converts 0-based half-open to 1-based inclusive: `start+1`, `end` (unchanged because GFF3 end is inclusive). Wrap features emit two rows sharing one `ID=`; circular records carry `Is_circular=true` on synthesised `region` row. Source features filtered.
27. **Annotation transfer exact-match only.** `_find_annotation_transfers` does verbatim substring on both strands; no fuzzy / BLAST. Skips below `_ANNOT_TRANSFER_MIN_LEN` (30 bp). Wrap-aware; whole-plasmid case (`feat_len == n_tgt`) special-cased to single `[0, n)` transfer.
28. **Pairwise alignment cap + cancellability.** `_pairwise_align` caps at `_PAIRWISE_MAX_LEN = 200_000` bp per side. PairwiseAligner C loop **cannot be cancelled mid-flight** — `_diff_align_worker` uses `exclusive=True`. Workers capture `_record_load_counter` at entry and refuse if canvas moved on (mirrors `_restr_scan_worker`, `_seed_default_library`).
29. **Cross-collection search skips id-less entries.** `_search_collections_library` filters entries lacking `id` to avoid aliasing dismiss payload `(collection, "")`. Same reason `LibrarySearchModal` row keys carry the `(collection, id)` pair.
30. **Agent endpoints `transfer-annotations`/`diff-plasmid` look up against active library only** (via `_load_library()`). Cross-collection lookup is the `search-library` endpoint's job; agents call that first, then `set-active-collection`, then transfer/diff.
31. **Four-layer JSON data-safety net.** Every `_safe_save_json` write produces: (a) `<file>.bak` single-gen (back-compat with `_safe_load_json` recovery); (b) timestamped `<file>.bak.YYYYMMDD-HHMMSS` (`_BACKUP_RETENTION_COUNT = 10`); (c) daily `<DATA_DIR>/snapshots/<stem>-YYYY-MM-DD.json` (`_SNAPSHOT_RETENTION_DAYS = 30`, via `_snapshot_data_files` at launch); (d) suspicious-shrink guard (>50% loss + ≥5 prior entries) spills to `<DATA_DIR>/lost_entries/` BEFORE overwrite. Restore UI: `Settings → Restore … from backup…` (`RestoreFromBackupModal`); helpers `_list_recoverable_backups` + `_restore_from_backup` reusable from agent path.
32. **`_skip_snapshot: bool = True`** on `PlasmidApp` (test default); `main()` flips False. Same pattern as `_skip_seed`, `_skip_update_check`.
33. **Natural-sort row mapping symmetric.** Every screen sorting a `DataTable` for display (`LibraryPanel`, `FeatureLibraryScreen`, `PartsBinModal`, `MutagenizeModal`, `PrimerDesignScreen`, `PlasmidPickerModal`, `TraditionalCloningPane`, `_palette_rows_for_grammar`) MUST resolve every `cursor_row` lookup against the SAME sort. Mismatched sort/lookup is the 0.7.4.5 bug class. `FeatureLibraryScreen` uses `_row_to_entry_idx` + `_entry_idx_to_row`; `PrimerDesignScreen` uses `_row_to_primer_idx`. `PlasmidPickerModal` sidesteps via `key=e.get("id")` on `add_row` (preferred pattern for new pickers).
34. **`_classify_part_from_plasmid` is grammar-by-grammar Type IIS digest.** Loops `_all_grammars()`, runs `_excise_fragment_pair`, picks first 2-fragment digest whose smaller fragment's `(left.overhang_seq, right.overhang_seq)` matches a position. Smaller = insert; larger = vector. Linear records skipped. Parts Bin "Load Part" runs in `@work` thread (sync froze UI 200–500 ms on plasmids with many grammars).
35. **CommercialSaaS `.dna` writer emits the editor's full default packet inventory.** `_write_commercialsaas_dna_bytes` writes 0x00 (sequence) + 0x0A (features) + 0x06 (notes) + 0x08 (`AdditionalSequenceProperties`, 289 bytes) + 0x05 (`Primers` with `HybridizationParams`, 217 bytes) + optional history. Defaults match real CommercialSaaS files even when no user primers / no meaningful end-stickiness on circular plasmids — Viewer's panels fall back to "(empty)" if missing. Byte-for-byte assertions in `tests/test_commercialsaas_io.py::TestWriteCommercialSaaSDnaBytes`.

> Pitfalls #36–#63 live in `docs/invariants.md` as `[INV-36]`…`[INV-63]`. See "Where to find more" above.

## Persistent user preferences

`settings.json` via `_get_setting`/`_set_setting`. To add:

1. Class-level annotation on `PlasmidApp` with default (e.g. `_my_setting: bool = True`).
2. Hydrate in `PlasmidApp.compose()` — `self._my_setting = bool(_get_setting("my_setting", True))`. **`compose()` not `on_mount`** (mount fires leaves→root, so by `on_mount` children read stale defaults).
3. `action_toggle_my_setting` calls `_set_setting("my_setting", self._my_setting)` after flipping.
4. Surface in Settings menu (`MenuBar.MENUS` between File and Edit; `Settings` entry in `PlasmidApp.open_menu`'s `menus` dict).

Persisted toggles: `show_feature_tooltips`, `click_debug`, `check_updates`, `show_restr`, `restr_unique_only`, `restr_min_len`, `min_primer_binding`, `show_connectors`, `linear_layout`, `active_collection`, `active_grammar`. `map_mode` is **per-plasmid** on each library entry's `map_mode` field. `_library_load` stashes as `_tui_map_mode`; `pm.load_record` honours over topology default; `action_toggle_map_view` + `_register_alignment` write through `_persist_map_mode_for_active`. Sequencing-aligned plasmids auto-tag `linear`. `show_connectors`/`linear_layout` need deferred apply via `_pending_show_connectors`/`_pending_linear_layout` (targets not composed yet in `compose()`).

Persisted infrastructure: `last_seen_version` (What's New auto-push), `last_known_latest` + `last_update_check_ts` (24 h PyPI cache), `hmm_db_path`, `active_parts_bin`, `active_project`, `experiments_custom_dict`.

## Architecture pointers

`splicecraft.py` top-to-bottom: imports + persistence helpers → enzyme catalog + IUPAC + scanner + 2D feature packer + seq-panel renderer → GenBank I/O → `_Canvas` / `_BrailleCanvas` / `PlasmidMap` / `FeatureSidebar` → `LibraryPanel` → `SequencePanel` → core modals → grammars + settings → codon registry + Kazusa + mutagenesis → feature-library → parts bin → domesticator + constructor → mutagenize → primer design → small modals → `PlasmidApp` → `main()`.

Use `grep -n "^class \|^def " splicecraft.py` for live map. Test files 1:1 named after the subsystem.

## Conventions

- **Workers:** `@work(thread=True)`, `try / except Exception as exc / _log.exception`, friendly message via `call_from_thread`. Stale-record guard: capture `self._current_record` identity at entry, compare in callback.
- **JSON libraries:** envelope schema v1. Filter `isinstance(entry, dict)` after load. Add new files to `_protect_user_data` in `tests/conftest.py` and `_check_data_files`. Cover corruption recovery in `test_data_safety.py`.
- **Modals:** subclass `ModalScreen[ReturnType]`. Add row to `test_modal_boundaries.py::_MODAL_CASES` (fits 160×48).
- **Tests:** cross-validate against Biopython where biological. No network, no real files (autouse `_protect_user_data` monkeypatches every `_*_FILE` path). Async: `async with app.run_test(size=...)` + double `await pilot.pause()` for `call_after_refresh`.
- **Regression guards** cite date in docstring (`# Regression guard for 2026-MM-DD fix`).

## Sister project (ScriptoScope)

`/home/seb/proteoscope/scriptoscope.py` (~8,600 lines) — same author, same single-file convention. Patterns to crib if seq-panel renders blow 33 ms/frame: thread-local `Console` for `_text_to_content`; two-level render cache (`_seq_render_cache` + `_content_cache`, LRU via `OrderedDict.move_to_end`); `@lru_cache(1)` availability probes.

User is undecided whether to merge SpliceCraft / ScriptoScope / MitoShift / RefHunter / molCalc into one Textual app with modes. Single-file convention keeps the option open.

## Borrow before respinning

When building a new feature, look at this map FIRST. Almost every category has a working sibling in-tree whose patterns + invariants are already debugged. Respinning from scratch re-discovers bug classes that earlier sweeps already fixed. **Before writing new code: `grep -ni '<area>' docs/invariants.md docs/subsystems.md`** — the relevant `[INV-NN]` / `[SUB-xxx]` likely already encodes the bug class.

**New modal:**
* Subclass `ModalScreen[ReturnType]` (Textual base).
* Hosts an `Input` / `TextArea`? → `_blocks_undo: bool = True` AFTER docstring (`[INV-41]`).
* Mutates `_current_record`? → wrap dismiss callback via `self._guard_callback(cb, "Label")` so a canvas reload mid-modal drops the edit (`[INV-62]`).
* Looks up an item by idx on dismiss? → use **identity-based lookup** like `PartEditModal._on_result` does with `(name, sequence)` tuple — refuses + notifies on miss. Better than counter-based for in-place mutations.
* Double-click race? → `_dismissed: bool` flag + `_dismiss_once(payload)` helper, applied to every exit path (`[INV-50]`).
* Add a row to `tests/test_modal_boundaries.py::_MODAL_CASES` (must fit 160×48).

**New persisted JSON file (cache + reload semantics):**
1. `_<NAME>_FILE = _DATA_DIR / "<name>.json"` constant.
2. `_<name>_cache: "list | dict | None" = None` module-level global.
3. `_load_<name>()` returns `_typed_clone(_<name>_cache)` (pitfall #17, deepcopy on read).
4. `_save_<name>()` wraps `_safe_save_json` + cache reseat inside `with _cache_lock:` (`[INV-41]` — concurrency).
5. Register cache name in `_MASTER_DELETE_CACHE_ATTRS` (`[INV-48]`).
6. Register file attr in `_USER_DATA_FILE_ATTRS` (`[INV-39]`).
7. Add the `(file_attr, cache_attr)` tuple to `tests/conftest.py::_protect_user_data::_DATA_FILES`.
8. Add to `_check_data_files` launch-check.
9. Add the attr name to `RestoreFromBackupModal._TARGETS` so the Restore-from-backup UI covers it (`[INV-43]`).
10. Settings keys → add to `_SETTINGS_SCHEMA` with explicit type tuple + default (`[INV-43]`).

**New save action:**
* All writes go through `_safe_save_json` (sacred #7) — never raw `json.dump`.
* On failure call `_notify_save_failure(app, label, exc)` — fires the `save.failed` structured event AND surfaces a user toast (`[INV-41]`).
* Agent endpoints use `_agent_save_or_500(save_fn, label)` for uniform 500 shape.
* If the save has a downstream mirror (active collection, active project, etc.), the mirror call MUST live inside the `_cache_lock` block (`[INV-50]`).

**New `action_*` method:**
* Decorate `@_action_log("app.<area>.<verb>")` for the user-intent event (`[INV-42]`). Decorator AND body events can co-exist — intent vs outcome are different signals.
* Destructive? Use a confirm modal with default-focus on `No` (mirror `LibraryDeleteConfirmModal`). Stray Enter should never delete.

**New `@work` worker:**
* `@work(thread=True, exclusive=True, group="<name>")` for heavy / cancellable ops.
* Capture `entry_counter = self._record_load_counter` at entry; bail in `_apply` callback if it shifted (pitfall #28).
* `except Exception as exc / _log.exception("...")` — pitfall #1's explicit carve-out for worker bodies.
* `try / finally` to drop any "in-flight" sentinels so an exception can't wedge the worker permanently (`[INV-41]` example: `_settings_flush_running`).

**New agent endpoint:**
* `@_agent_endpoint("name", write=True/False)` — `_AGENT_HANDLERS` registry is auto-populated.
* Write endpoints: route through `_agent_save_or_500`; check `_agent_dirty_guard(app, payload)` if the canvas dirty state matters.
* Inputs use `_sanitize_label`, `_sanitize_bases`, `_sanitize_accession`, etc. — never trust raw payload values.
* Idx-based payloads: capture `_record_load_counter`, check in `_apply` (pitfall #28).
* Listing endpoints with large payloads (library, search): hard-cap at endpoint-specific limit; `_AGENT_RESPONSE_MAX_BYTES = 50 MB` is the global backstop.
* Add a row to `test_agent_api.py` covering happy + error paths.

**New picker / DataTable:**
* Same sort for display AND for cursor → idx resolution (pitfall #33). Easiest: `key=` parameter on `add_row` (pre-empts the bug class — `PlasmidPickerModal` is the reference).
* If using `cursor_row` int + sort, build explicit `_row_to_entry_idx` + `_entry_idx_to_row` maps.

**New feature-list iteration:**
* Use `_feat_bounds(feat, total) → (start, end, strand)` for wrap-aware extraction (`[INV-41]`).
* Use `_smallest_enclosing_feature(bp)` not O(N) `_feat_at` for bp-lookup (`[INV-41]`).
* `_feat_len(start, end, total)` for wrap-aware distance — naive `end - start` is wrong on origin-spanning features (sacred #8).

**New HTTP fetch:**
* `resp.read(MAX + 1)` + bail-if-exceeded — never `resp.read()` raw (pitfall #20).
* Existing caps: `_PYPI_MAX_RESPONSE_BYTES`, `_NCBI_MAX_RESPONSE_BYTES`, `_KAZUSA_MAX_RESPONSE_BYTES`, `_PLASMIDSAURUS_*_MAX_BYTES`.
* Retry pattern: 1 try + 250 ms backoff (mirrors `_fetch_latest_pypi_version`, `fetch_genbank`).

**New XML parsing:**
* Route through `_safe_xml_parse` — rejects DOCTYPE / ENTITY (pitfall #19). Includes NCBI responses AND `.dna` history packets.

**New logging point:**
* User actions: `@_action_log("app.area.verb")` decorator.
* State changes: `_log_event("<noun>.<verb>", **fields)`.
* Heavy ops: `@_timed("op.area.name", threshold_ms=50)` wrapper.
* **SACRED: never log sequence content** — `_repr_for_log` truncates/summarises automatically (`[INV-38]`). `seq.chunk_dump` and similar route through structured events that hash or length-only the payload.

**New file-system traversal:**
* `path.lstat()` + `stat.S_ISREG(st.st_mode)` — refuses symlinks outright (`[INV-50]`).
* `_safe_save_json` already covers symlink refusal for writes.
* Bulk imports: walk via `path.iterdir()` not `os.walk(followlinks=True)`.

## For future agents

1. Read this file first, then **grep `docs/invariants.md` + `docs/subsystems.md` for the area you're touching** before any edit, then `git log --oneline` for recent context.
2. `python3 -m pytest -n auto -q` before and after any change. `tests/test_dna_sanity.py` (<2 s) is the fast inner loop.
3. Don't break sacred invariants. Don't bypass `_safe_save_json`. Don't add bare `except`.
4. Eyeball real-world behaviour on pUC19 (`L09137`) and pACYC184 (`MW463917.1`).
5. Past fix history is in git — `git show <hash>` beats stale prose.
6. **Dispatching a sub-agent?** Quote the relevant `[INV-NN]` / `[SUB-xxx]` tag in its prompt so it greps the right file before working.
