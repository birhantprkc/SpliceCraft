# CLAUDE.md — AI Agent Context for SpliceCraft

Agent handoff. Read before touching the codebase.

Developed by a human bioinformatician + Claude. **Single-file architecture** — entire app is `splicecraft.py` (~39,000 lines). Intentional: keeps the codebase greppable.

## What is SpliceCraft?

Terminal-based circular plasmid map viewer, sequence editor, and cloning/mutagenesis workbench. Python 3.10+ / Textual / Biopython. Unicode braille-dot maps, per-base sequence panel, restriction overlays, collection-driven plasmid library, Golden Braid L0 + MoClo grammars, Primer3-backed primer design, SOE-PCR mutagenesis, in-process BLASTN/BLASTP/HMMscan via pyhmmer.

**Repo:** `github.com/Binomica-Labs/SpliceCraft` · **PyPI:** `splicecraft` · `__version__` lives in `splicecraft.py` and `pyproject.toml`.

## How to run

```bash
python3 splicecraft.py                       # empty canvas
python3 splicecraft.py L09137                # fetch pUC19 from NCBI
python3 splicecraft.py myplasmid.gb          # local GenBank (.gb/.gbk/.dna)
python3 -m pytest -n auto -q                 # full suite (2,250+ tests, ~5–6 min on 8 cores)
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
7. **Data-file saves always back up.** Always go through `_safe_save_json` (`.bak` + `tempfile.mkstemp` + `os.fsync` + `os.replace`). Schema envelope `{"_schema_version": 1, "entries": [...]}`; `_extract_entries` accepts legacy bare-list (pre-0.3.1). Never bypass. **`_safe_save_json` re-raises on failure** (disk-full, RO mount, permission denied) so callers can `notify` the user — silent swallow used to desync UI state from disk.
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
17. **Cache contracts (deepcopy on BOTH read AND save).** `_load_library` / `_load_collections` / `_load_features` / `_load_custom_grammars` / `_load_parts_bin` / `_load_primers` deepcopy on read so caller-side mutations of returned dicts can't poison the cache. The corresponding `_save_*` helpers also deepcopy when re-seating the cache (`_library_cache = deepcopy(entries)`, etc.) — without this, a caller that keeps editing the list it just saved would leak post-save mutations into the next reader. New persisted libraries with mutable callers must follow both halves of this convention.
18. **Trademark scrub.** `.dna` is the popular commercial plasmid editor's binary format. The trademarked name has been scrubbed from source — code identifiers use `CommercialSaaS` / `commercialsaas` / `_BIOPYTHON_DNA_FMT`. The BioPython API contract string (`"commercialsaas"`) and the 8-byte cookie magic (`b"CommercialSaaS"`) are stored hex-encoded as `_BIOPYTHON_DNA_FMT` and `_COMMERCIALSAAS_COOKIE_MAGIC` so the trademarked text never appears verbatim. User-facing prose says "popular commercial plasmid editor file format". Don't reintroduce the trademarked name in any new code.
19. **Untrusted XML routes through `_safe_xml_parse`.** Sacred for NCBI responses AND `.dna` history packets — `_parse_commercialsaas_history` is the latest entry on this list. Don't add a new XML ingest path that calls `ET.fromstring` directly.
20. **Network reads are size-capped.** PyPI (`_PYPI_MAX_RESPONSE_BYTES`), NCBI (`_NCBI_MAX_RESPONSE_BYTES`), Kazusa (`_KAZUSA_MAX_RESPONSE_BYTES`). Any new HTTP fetch must follow the `resp.read(MAX + 1)` + bail-if-exceeded pattern.
21. **`_extract_commercialsaas_history_xml` uses streaming LZMA decompress** with `max_length=cap+1` so a compressed bomb that would expand to GB never materialises.
22. **`_dna_sidecar_path` strips `..` / dot-only / NUL** via `Path(...).name` after replacing separators. Don't loosen — the entry_id can be user-controlled.
23. **`_safe_load_json` is size-capped at `_SAFE_LOAD_JSON_MAX_BYTES` (50 MB).** A corrupted / mis-restored / hostile shared library file can't OOM the loader.
24. **`_h_load_file` agent endpoint is size-capped at `_BULK_IMPORT_MAX_BYTES` (50 MB)** with `force=true` override. Other agent endpoints' size limits are documented inline.
25. **`_excise_fragment_pair` enforces exactly-2 cuts on circular plasmids.** ≥3 cuts surfaces an error rather than silently returning ambiguous fragments. Sacred invariant — restriction-cloning correctness depends on this.
26. **GFF3 export off-by-one.** `_record_to_gff3` converts SpliceCraft's 0-based half-open `[start, end)` to GFF3's 1-based inclusive: `start+1`, `end` (unchanged because GFF3 end is inclusive and we use exclusive). Wrap features emit two rows sharing one `ID=` (the GFF3 split-feature convention); circular records carry `Is_circular=true` on a synthesised `region` row at the top. Source features are filtered (the region row already covers the whole record).
27. **Annotation transfer is exact-match only.** `_find_annotation_transfers` does verbatim substring matching on both strands; no fuzzy / BLAST. Skips features below `_ANNOT_TRANSFER_MIN_LEN` (default 30 bp) to silence primer-binding-site noise. Wrap-aware on circular targets — `target_end < target_start` represents wrap. The whole-plasmid case (`feat_len == n_tgt`) is special-cased to emit a single `[0, n)` transfer instead of a degenerate wrap with `t_e == t_s`.
28. **Pairwise alignment cap + cancellability.** `_pairwise_align` caps at `_PAIRWISE_MAX_LEN = 200_000` bp per side. The PairwiseAligner C loop **cannot be cancelled mid-flight** — `_diff_align_worker` uses `exclusive=True` to drop superseded requests once the C loop returns, but in-flight work continues to completion. Workers must capture `_record_load_counter` at entry and refuse to apply if the canvas has moved on (mirrors `_restr_scan_worker` and `_seed_default_library`).
29. **Cross-collection search skips id-less entries.** `_search_collections_library` filters out plasmid entries whose `id` is missing or empty — without one, the dismiss payload `(collection, "")` would alias every untagged entry to the first one in the active library on load. Same reason `LibrarySearchModal` row keys carry the (collection, id) pair.
30. **Agent endpoints `transfer-annotations` and `diff-plasmid` look up the `*_id` against the active library only** (via `_load_library()`), not all collections. Cross-collection lookup is the `search-library` endpoint's job; agents should call that first, then `set-active-collection`, then the transfer/diff. Documented in each handler's docstring.
31. **Four-layer JSON data-safety net.** Every `_safe_save_json` write produces: (a) `<file>.bak` single-gen copy (back-compat with `_safe_load_json` recovery); (b) timestamped `<file>.bak.YYYYMMDD-HHMMSS` rotation (`_BACKUP_RETENTION_COUNT = 10`); (c) daily `<DATA_DIR>/snapshots/<stem>-YYYY-MM-DD.json` (`_SNAPSHOT_RETENTION_DAYS = 30`, via `_snapshot_data_files` at launch); (d) suspicious-shrink guard (>50% loss + ≥5 prior entries) spills discarded entries to `<DATA_DIR>/lost_entries/` BEFORE overwrite. **Never bypass `_safe_save_json`**. Restore UI: `Settings → Restore … from backup…` (`RestoreFromBackupModal`); helpers `_list_recoverable_backups` + `_restore_from_backup` are reusable from the agent path.
32. **`_skip_snapshot: bool = True`** on `PlasmidApp` is the test default so async tests don't fan out to disk on every launch; `main()` flips it False. Same pattern as `_skip_seed` and `_skip_update_check`.
33. **Natural-sort row mapping is symmetric.** Any screen that sorts a `DataTable` for display (currently: `LibraryPanel`, `FeatureLibraryScreen`, `PartsBinModal`, `MutagenizeModal`, `PrimerDesignScreen`, `PlasmidPickerModal`, `TraditionalCloningPane`, `_palette_rows_for_grammar`) MUST resolve every `cursor_row` lookup against the SAME sort. Mismatched sort/lookup is the bug class behind 0.7.4.5: `TraditionalCloningPane._record_for_table_row` AND `_current_source_entries` both load + sort identically to `_populate_library_tables`; otherwise the click on display row N digests one plasmid while the history XML records a different one. `FeatureLibraryScreen` keeps this honest with `_row_to_entry_idx` (display→entry) + `_entry_idx_to_row` (entry→display) reverse dict; `PrimerDesignScreen` uses `_row_to_primer_idx` for the same reason. `PlasmidPickerModal` sidesteps the problem by dismissing the entry's `id` (via `key=e.get("id")` on `add_row`) — preferred pattern for new pickers.
34. **`_classify_part_from_plasmid` is grammar-by-grammar Type IIS digest.** Loops over `_all_grammars()` in registry order, runs `_excise_fragment_pair` for each grammar's enzyme, picks the first 2-fragment digest whose smaller fragment's `(left.overhang_seq, right.overhang_seq)` matches a position in that grammar's table. Smaller fragment = insert; larger = vector. Linear records skipped (digest can't cleanly excise). The Parts Bin "Load Part" button (`PartsBinModal._load_part`) calls this from a `@work` thread (`_load_part_worker`) — running synchronously on the click handler froze the UI for 200–500 ms on plasmids with many grammars. New per-click work that touches `_excise_fragment_pair` should follow the same `@work` pattern with `call_from_thread` for any UI updates / `notify` calls.
35. **CommercialSaaS `.dna` writer emits the editor's full default packet inventory.** `_write_commercialsaas_dna_bytes` writes 0x00 (sequence) + 0x0A (features) + 0x06 (notes) + 0x08 (`AdditionalSequenceProperties`, default-blunt + 5'-phosphorylated, 289 bytes) + 0x05 (`Primers` with default `HybridizationParams`, 217 bytes) + optional history packet. The 0x05/0x08 defaults match what real CommercialSaaS files carry even when the editor has no user-tracked primers / no meaningful end-stickiness on circular plasmids. Don't drop these — `CommercialSaaS Viewer`'s Sequence Properties + Primers panels fall back to "(empty)" if missing. The byte-for-byte assertions in `tests/test_commercialsaas_io.py::TestWriteCommercialSaaSDnaBytes` are the regression target; if you ever change the defaults, change the test alongside.
36. **Future-proofing scaffolding.** Six additive mechanisms to absorb future schema bumps without breaking existing data:
    * `_ENTRY_MIGRATIONS` per-label `(from_v, to_v) → Callable[[dict], dict]` registry. `_extract_entries` runs every load through `_migrate_entries(entries, from_version, _CURRENT_SCHEMA_VERSION, label)`. Failed migrators preserve the entry + warn (never drop user data). To add: bump `_CURRENT_SCHEMA_VERSION`, register `(N, N+1)` under the file label, write a regression test.
    * `$SPLICECRAFT_PYPI_URL` env override (http/https only, ≤2048 chars). No caching — resolved every fetch.
    * Pre-update snapshots record `from_python_version` + `from_platform`. **`_RUNTIME_PLATFORM` is cached at import** because `platform.platform()` shells out via subprocess on some OSes, conflicting with tests that monkeypatch `subprocess.run`.
    * `--dry-run` exercises detection/PyPI/snapshot then bails. Mutex with `--check`.
    * `<DATA_DIR>/.splicecraft-data-version` stamp; `_check_and_stamp_data_version()` warns to stderr on downgrade. Atomic write via `_atomic_write_text`, read capped at 128 bytes.
    * `_PLUGINS_DIR = _DATA_DIR / "plugins"` + `_RESERVED_ENTRY_FIELDS = ("_plugin_data",)`. Tested by `TestFutureProofingFeatures`.
37. **Robustness pass (0.7.6).** Ten safety-nets:
    * `_acquire_data_dir_lock` (POSIX `fcntl.flock` / Win `msvcrt.locking`) at `<DATA_DIR>/splicecraft.lock`. Lockfile carries holder PID; `$SPLICECRAFT_SKIP_LOCK=1` bypasses with loud warning (per #41).
    * `threading.excepthook` in `main()` routes unhandled worker exceptions to `_log.error` with full traceback.
    * `_chmod_user_only` sets 0o600 on logs + bundles (POSIX-only no-op on Win).
    * `_SETTINGS_SCHEMA` + `_validate_settings` from `_load_settings`. **Strict bool-vs-int** — `True` does NOT coerce into `int` fields. Unknown keys pass through.
    * `_drain_in_flight_workers(timeout_s=2.0)` in `main()` finally (daemons skipped).
    * Network retry: 1 retry + 250 ms backoff on `_fetch_latest_pypi_version` + `fetch_genbank`.
    * 4-tier `_copy_to_clipboard_with_fallback(app, text, label) → (mode, detail)`: Textual → OSC 52 → `<DATA_DIR>/clipboard/<ts>-<label>.txt` → log-only.
    * `_MODAL_STACK_SOFT_CAP = 12` on `push_screen`; cap-fallback dispatches `callback(None)` so parents don't deadlock.
    * `_apply_record` notifies when record > `_LARGE_PLASMID_BP = 5_000_000` bp.
    * `_snapshot_data_files` skips files > `_SNAPSHOT_FILE_SIZE_CAP = 50 MB`. Tested by `TestRobustnessHardening`.
38. **Diagnostic logging + UI snapshot + bundle.** Three surfaces for bug-report archives:
    * Rotating log at `<DATA_DIR>/logs/splicecraft.log` (override `$SPLICECRAFT_LOG`). `RotatingFileHandler`, 5 MB × 4 backups, 8-char `_SESSION_ID` prefix per line. **NEVER log sequence content** — `_repr_for_log` truncates / summarises.
    * **`Alt+D`** App-priority → `action_capture_ui_snapshot` → `<DATA_DIR>/ui_snapshots/ui-snapshot-<ts>.md` (version, Python, platform, screen stack, focused widget, terminal size, record metadata excluding sequence, settings, active collection/grammar, 200-line log tail with `/home/<user>` → `~`). `_collect_ui_snapshot` is defensive. Retention `_UI_SNAPSHOT_RETENTION = 20`. Old `alt+d` hover-debug moved to `alt+shift+d`.
    * `splicecraft logs --bundle [--out PATH]` atomically zips logs + last 5 UI snapshots + sanitized settings + system info + README. `_scrub_path` handles `/home/<user>`, `/Users/<user>`, `C:\Users\<user>`, `Path.home()`. Default `splicecraft-debug-<sessionID>-<ts>.zip`. **Sacred privacy invariant: sequence content MUST never leak.**
39. **`splicecraft update` snapshots user data before any install subprocess.** All upgrade paths (pipx/uv-tool/uv-venv/pixi-global/pip-user/pip-venv) call `_create_pre_update_snapshot(__version__)` AFTER user confirm BEFORE `subprocess.run`. Covers `_USER_DATA_FILE_ATTRS` (10 files: library, collections, parts_bin, primers, features, feature_colors, grammars, entry_vectors, codon_tables, settings) + `_USER_DATA_DIR_ATTRS` (crash_recovery, dna_originals). **Atomic**: built in `<backup_dir>/.tmp-<rand>/`, fsynced, sealed by `os.replace` to `<backup_dir>/<ts>-<rand>__from-<version>/`. Failure → staging removed + `OSError`/`shutil.Error` raised → `_run_update_subcommand` exits 1. Location is **sibling** `<DATA_DIR>/../<DATA_DIR.name>-update-backups/` (override `$SPLICECRAFT_UPDATE_BACKUP_DIR`) so a recursive-wipe can't kill recovery. Refuses when `_data_dir_inside_install_path()`. Restore: `splicecraft update --restore-pre-update [<id>|latest]` takes pre-restore snapshot first. Retention `_PRE_UPDATE_SNAPSHOT_RETENTION = 5`; rmtree restricted to `_PRE_UPDATE_NAME_RE`. **Sacred four restore checks**: `schema_version` ≤ `_PRE_UPDATE_SCHEMA_VERSION`, `attr` in whitelist, `name` rejects separators/`..`, SHA-256 re-verified before `os.replace`. Refusal paths (editable/source/pixi-project/pip-system) + `--check` MUST NOT snapshot. Tested by `TestUpdateDataSafety*` in `tests/test_smoke.py`.

40. **Overhang pair is the sacred source of truth for part classification.** `_classify_part_from_plasmid` resolves the part type / level / position **purely** from the (oh5, oh3) pair released by digesting the plasmid with each grammar's primary or secondary Type IIS enzyme. Feature labels, plasmid name, source filename, etc. are NEVER consulted — the user's biological molecule has exactly one legal position per overhang pair, so the lookup is mechanical and unambiguous. If the digest produces overhangs that don't match any position, the classifier returns `None` (with a "couldn't classify — use New Part to set type manually" notify upstream). When you tweak a grammar's position table or add new positions, the user-facing impact is "this overhang pair now / no longer classifies"; never re-route via heuristics. Adding the GB 2.0 expanded grammar (`Promoter` GGAG/AATG combined PromUTR + `Promoter-only` GGAG/CCAT separate + `5' UTR` CCAT/AATG) was a position-table change, not a classifier change — `_classify_part_from_plasmid` itself is unchanged.

41. **Robustness sweep #2 (2026-05-10).** Key helpers + conventions established:
    * **`_feat_bounds(feat, total) → (start, end, strand)`** canonical wrap-aware extractor; `end < start` = origin-spanning. Routes formerly-raw `int(loc.start)/.end` sites in `_find_annotation_transfers`, `_assembly_fragment_from_source`, `_assemble_construct`, `_feats_for_domesticator`, `TraditionalCloningPane`, `PrimerDesignScreen._parse_features_from_record`. Wrap labels via `_feat_span_label`. Regression: `tests/test_circular_math.py::TestFeatBounds`.
    * **`_dispatch_restr_scan`** wraps all 6 sync `_scan_restriction_sites` callers; captures `(min_len, unique_only, circular, entry_counter)` on UI thread → `@work(exclusive=True, group="restr_scan") _restr_scan_worker`.
    * **Worker deepcopy at entry.** `_save_worker` + `_do_autosave` deepcopy `_current_record` at entry (was bare reference).
    * **JSON persistence.** `_codon_tables_load`/`_save` deepcopy (invariant #17). `_restore_library_from_active_collection` re-seats with deepcopy. `_check_data_files` extended to features/feature_colors/grammars/codon_tables. `_SNAPSHOT_TOTAL_SIZE_CAP = 500 MB`. `_safe_save_json` + `_atomic_write_text` fsync parent via `_fsync_parent_dir`. `_OBSERVED_SCHEMA_VERSIONS` (per-absolute-path) preserves higher-than-current schema stamps.
    * **Lock hardening.** PID metadata fsynced before lock returns. `_pid_alive(pid)` (`os.kill(pid, 0)`) retakes lock if recorded PID is dead.
    * **Modal / concurrency.** `push_screen` cap-fallback dispatches `callback(None)`. `PlasmidsaurusAlignModal._go` + `BlastModal._do_run`/`_do_run_hmmscan` got `exclusive=True, group=...`. `_action_undo`/`_redo` check `_undo_blocked_by_modal`; modal opts in via `_blocks_undo: bool = True`. `SplashScreen._safe_dismiss` `_dismissing` flag.
    * **XML / zip / FS safety.** `_safe_xml_parse` streaming prologue scan rejects DOCTYPE/ENTITY regardless of padding. `_is_safe_zip_member_name` rejects `..`/absolute/NUL/ANSI-escape. `_extract_gbk_member` caps decompressed read at `MAX+1`. `_safe_file_size_check` lstat + S_ISREG rejects symlinks (`/dev/zero` reports `st_size=0`); used by `_safe_load_json`, `load_genbank`, `_h_load_file`.
    * **Other.** `_copy_strand` → 4-tier `_copy_to_clipboard_with_fallback`. `_notify_save_failure(app, label, exc)` canonical helper for `_safe_save_json` re-raise paths.

42. **Robustness sweep #3 (2026-05-11).** 22-finding audit + worker-conversion pass:
    * **Worker dispatches established.** Per-modal/screen `@work(exclusive=True, group=...)` flows: `_do_export_worker` (GenBankExportModal/GffExportModal/FastaExportModal), `_design_worker` (PrimerDesignScreen, fans out detection/cloning/goldenbraid/generic), `_optimize_worker` + `_design_mut_worker` (MutagenizeModal), `_trad_simulate_worker` (TradCloningPane), `_save_to_library_worker` (ConstructorModal), `_fasta_collection_worker` (OpenFileModal multi-FASTA). All emit `_on_*_failed` + `_apply_*_result` callbacks; worker bodies never call `widget.update` directly.
    * **Pre-capture pattern.** `_collect_simulate_inputs` (TradCloning) reads every UI field on the UI thread into a dict, then the worker runs pure off-thread. `_build_*_fragment` helpers refactored to take pre-captured records + return `(frag, err_msg)` tuples instead of `widget.update(...)`.
    * **Stale-record guards on modal/screen workers.** F13/F17/F15 workers capture `_record_load_counter` at entry, refuse to apply if the canvas reloaded mid-flight — extends invariant #28 from canvas-mutating workers to modal/screen workers. Export modals (F18) don't need it (their record is captured at modal construction).
    * **`source_record` param on `_rebuild_record_with_edit`.** Agent-API `_h_replace_sequence` now deepcopies `app._current_record` via a UI-thread call (atomic snapshot), passes it as `source_record`, then rebuilds off-thread. Eliminates the feature-read race with concurrent UI edits. `_apply` also checks `_record_load_counter` and returns 409 if the canvas paged mid-rebuild.
    * **Modal Ctrl+Z guards.** `_blocks_undo: bool = True` opt-ins added on ConstructorModal / DomesticatorModal / MutagenizeModal / PrimerDuplicatesModal. The attr must come AFTER the docstring — placing it before breaks Python's docstring detection (first statement rule).
    * **`PrimerDuplicatesModal` + `_check_primer_duplicates`.** Splash-dismiss scan offers one-click cleanup of legacy duplicate-sequence primers (from `.dna` imports before `_dedupe_primers_by_sequence` ran on save). Modal defaults to KEEP (focus + Esc → Keep) so a stray Enter on splash dismiss can't delete data. Scan builds the deduped list once and passes it through the callback. `_skip_primer_dedupe_check` test flag matches the `_skip_seed` / `_skip_update_check` / `_skip_snapshot` pattern. Pushed BEFORE `WhatsNewModal` so WhatsNew (if shown) ends up on top of the screen stack.
    * **Primer dedup UX.** `PrimerDesignScreen._save_primers_btn` warning now names the existing primer (`'P-new-F' matches existing 'P-amp-1-F', saved 2026-04-15`). `_show_result` displays a yellow "⚠ Already in primer library — Save will be refused" hint at design time so users see the collision before naming.
    * **`is_mounted` guards on worker dismiss.** Export workers wrap `self.dismiss(summary)` in a `_dismiss_if_mounted()` closure dispatched via `call_from_thread`. A user cancelling mid-export no longer races a successful-export dismiss into a stale modal. Mirrors the `FetchModal._do_fetch` is_mounted pattern.
    * **Snapshot + restore.** `_snapshot_data_files` default now iterates `_USER_DATA_FILE_ATTRS` via `globals()` lookup (10 files vs 4 prior). `_restore_from_backup` stamps `_OBSERVED_SCHEMA_VERSIONS[str(target_path)]` from the source's `_schema_version` if higher than `_CURRENT_SCHEMA_VERSION` — restoring a v2 backup onto v1 SpliceCraft no longer silently demotes on next save.
    * **Wrap-aware `.dna` primer_bind import.** `_augment_dna_record_from_packets` routes through `_feat_bounds`; origin-spanning primer_bind features now splice `seq[s:] + seq[:e]` instead of `seq[0:n]` (the silently-corrupting flatten over a `CompoundLocation`).
    * **GFF3 per-part strand.** `_record_to_gff3` probes each `part.strand` separately inside the parts loop so mixed-strand compound joins emit correct ±/. per arc — same-strand and wrap features unchanged.
    * **Other hardening.** OpenFileModal probe uses `lstat` + `S_ISLNK` to refuse symlinks at the large-file confirm step (matches `_safe_file_size_check` policy in `load_genbank`). `_save_settings` cache uses `deepcopy` on read AND save (invariant #17 compliance). 3 unwrapped `_save_library` callsites (TradCloning save, picker collection-swap, status picker) now wrap in try/except + `_notify_save_failure`. 2 direct save-failed notifies on FeatureLibraryScreen + GrammarEditorModal switched to the canonical helper. `FetchModal._do_fetch` got `exclusive=True, group="ncbi_fetch"`. `_load_part_worker` adds `is_mounted` check before `call_from_thread(self._populate)`. Trademark scrub closed 5 verbatim comment/docstring regressions.

## Persistent user preferences

User-preference toggles persist across sessions via `settings.json`.
Adding one is mechanical:

1. Class-level annotation on `PlasmidApp` with the default value (e.g. `_my_setting: bool = True`).
2. Hydrate in `PlasmidApp.compose()` next to the existing block — `self._my_setting = bool(_get_setting("my_setting", True))`. **`compose()` not `on_mount`** because Textual fires mount events leaves→root, so by the time `on_mount` runs the children have already read stale defaults.
3. In `action_toggle_my_setting`, call `_set_setting("my_setting", self._my_setting)` after flipping.
4. Surface in the Settings menu (`MenuBar.MENUS` between File and Edit; populated by the `Settings` entry in `PlasmidApp.open_menu`'s `menus` dict).

Currently persisted user toggles: `show_feature_tooltips`, `click_debug`, `check_updates`, `show_restr`, `restr_unique_only`, `restr_min_len`, `min_primer_binding`, `show_connectors`, `linear_layout`, `active_collection`, `active_grammar`. `map_mode` is deliberately NOT persisted (re-derived from each record's `topology` field on load). `show_connectors` and `linear_layout` need a deferred apply via `_pending_show_connectors` / `_pending_linear_layout` because their target widgets aren't composed yet when `compose()` runs; `on_mount` reads the pending values once the children exist.

Persisted infrastructure (not user-facing toggles): `last_seen_version` (drives the What's New auto-push), `last_known_latest` + `last_update_check_ts` (24 h cache for the PyPI update probe), `hmm_db_path` (last-used HMM database path).

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
