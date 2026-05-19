# CLAUDE.md — AI Agent Context for SpliceCraft

Agent handoff. Read before touching the codebase.

Developed by a human bioinformatician + Claude. **Near-single-file architecture** — the application lives in `splicecraft.py` (~65,000 lines) plus a small extracted biology module `splicecraft_biology.py` and the stdlib-only sidecar `splicecraft_cli.py`. The single-file constraint is intentional (keeps the codebase greppable); the biology extraction is the first deliberate exception, scoped to pure functions / constants with no `PlasmidApp` coupling. See the three-test rule in `CONTRIBUTING.md` for the criteria any future extraction must satisfy.

## What is SpliceCraft?

Terminal-based circular plasmid map viewer, sequence editor, and cloning/mutagenesis workbench. Python 3.10+ / Textual / Biopython. Unicode braille-dot maps, per-base sequence panel, restriction overlays, collection-driven plasmid library, Golden Braid L0 + MoClo grammars, Primer3-backed primer design, SOE-PCR mutagenesis, in-process BLASTN/BLASTP/HMMscan via pyhmmer.

**Repo:** `github.com/Binomica-Labs/SpliceCraft` · **PyPI:** `splicecraft` · `__version__` lives in `splicecraft.py` and `pyproject.toml`.

## How to run

```bash
python3 splicecraft.py                       # empty canvas (or auto-loads first library entry)
python3 splicecraft.py L09137                # fetch pUC19 from NCBI
python3 splicecraft.py myplasmid.gb          # local GenBank (.gb/.gbk/.dna)
python3 -m pytest -n auto -q                 # full suite (2,250+ tests, ~5–6 min on 8 cores)
python3 -m pytest tests/test_dna_sanity.py   # biology only (< 2 s — fast inner loop)
./release.py X.Y.Z                           # bump, test, build, tag, push (PyPI via OIDC)
```

End users: `pipx install splicecraft && splicecraft`.

Shipped no-arg launch shows an empty canvas (or auto-loads the first library entry if any). The 1 kb synthetic demo plasmid (`_make_demo_record` / `_DEMO_PLASMID_SEQ`) is **kept in the source for tests / ad-hoc development** but `main()` no longer pre-sets `_preload_demo_record`. Similarly, the first-run NCBI seed (`_seed_default_library` → MW463917.1) is suppressed in releases: `main()` flips `_skip_seed = True`. Both used to fire on `splicecraft` no-arg launches in earlier versions; shipping them confused users into thinking the demo was one of their saved plasmids. Dev / demo builds wanting the historical "auto-seed on empty library" behaviour can flip `_skip_seed = False` before `app.run()`.

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
23. **`_safe_load_json` is size-capped at `_SAFE_LOAD_JSON_MAX_BYTES` (1 GB).** A corrupted / mis-restored / hostile shared library file can't OOM the loader. (Not to be confused with the 50 MB `_BULK_IMPORT_MAX_BYTES` cap on the agent-API `load-file` endpoint — those are different constants for different threat models.)
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
37. **Robustness pass (0.7.6).** Ten safety-nets, tested by `TestRobustnessHardening`:
    * `_acquire_data_dir_lock` (POSIX `fcntl.flock` / Win `msvcrt.locking`) at `<DATA_DIR>/splicecraft.lock`; PID-carrying; `$SPLICECRAFT_SKIP_LOCK=1` bypass.
    * `threading.excepthook` → `_log.error`. `_chmod_user_only` 0o600 on logs/bundles. `_drain_in_flight_workers(timeout_s=2.0)` in `main()` finally (daemons skipped).
    * `_SETTINGS_SCHEMA` + `_validate_settings` — **strict bool-vs-int** (`True` does NOT coerce into `int` fields). Unknown keys pass through.
    * Network retry: 1 try + 250 ms backoff on `_fetch_latest_pypi_version` + `fetch_genbank`. 4-tier `_copy_to_clipboard_with_fallback`: Textual → OSC 52 → `<DATA_DIR>/clipboard/<ts>-<label>.txt` → log.
    * `_MODAL_STACK_SOFT_CAP = 12` on `push_screen` with `callback(None)` fallback. `_apply_record` notifies > `_LARGE_PLASMID_BP = 5_000_000` bp. `_snapshot_data_files` skips > `_SNAPSHOT_FILE_SIZE_CAP = 50 MB`.
38. **Diagnostic logging + UI snapshot + bundle.** Three surfaces for bug-report archives:
    * Rotating log at `<DATA_DIR>/logs/splicecraft.log` (override `$SPLICECRAFT_LOG`). `RotatingFileHandler`, 5 MB × 4 backups, 8-char `_SESSION_ID` prefix per line. **NEVER log sequence content** — `_repr_for_log` truncates / summarises.
    * **`Alt+D`** App-priority → `action_capture_ui_snapshot` → `<DATA_DIR>/ui_snapshots/ui-snapshot-<ts>.md` (version, Python, platform, screen stack, focused widget, terminal size, record metadata excluding sequence, settings, active collection/grammar, 200-line log tail with `/home/<user>` → `~`). `_collect_ui_snapshot` is defensive. Retention `_UI_SNAPSHOT_RETENTION = 20`. Old `alt+d` hover-debug moved to `alt+shift+d`.
    * `splicecraft logs --bundle [--out PATH]` atomically zips logs + last 5 UI snapshots + sanitized settings + system info + README. `_scrub_path` handles `/home/<user>`, `/Users/<user>`, `C:\Users\<user>`, `Path.home()`. Default `splicecraft-debug-<sessionID>-<ts>.zip`. **Sacred privacy invariant: sequence content MUST never leak.**
39. **`splicecraft update` snapshots user data before any install subprocess.** All upgrade paths (pipx/uv-tool/uv-venv/pixi-global/pip-user/pip-venv) call `_create_pre_update_snapshot(__version__)` AFTER user confirm BEFORE `subprocess.run`. Covers `_USER_DATA_FILE_ATTRS` (10 files: library, collections, parts_bin, primers, features, feature_colors, grammars, entry_vectors, codon_tables, settings) + `_USER_DATA_DIR_ATTRS` (crash_recovery, dna_originals). **Atomic**: built in `<backup_dir>/.tmp-<rand>/`, fsynced, sealed by `os.replace` to `<backup_dir>/<ts>-<rand>__from-<version>/`. Failure → staging removed + `OSError`/`shutil.Error` raised → `_run_update_subcommand` exits 1. Location is **sibling** `<DATA_DIR>/../<DATA_DIR.name>-update-backups/` (override `$SPLICECRAFT_UPDATE_BACKUP_DIR`) so a recursive-wipe can't kill recovery. Refuses when `_data_dir_inside_install_path()`. Restore: `splicecraft update --restore-pre-update [<id>|latest]` takes pre-restore snapshot first. Retention `_PRE_UPDATE_SNAPSHOT_RETENTION = 5`; rmtree restricted to `_PRE_UPDATE_NAME_RE`. **Sacred four restore checks**: `schema_version` ≤ `_PRE_UPDATE_SCHEMA_VERSION`, `attr` in whitelist, `name` rejects separators/`..`, SHA-256 re-verified before `os.replace`. Refusal paths (editable/source/pixi-project/pip-system) + `--check` MUST NOT snapshot. Tested by `TestUpdateDataSafety*` in `tests/test_smoke.py`.

40. **Overhang pair is the sacred source of truth for part classification.** `_classify_part_from_plasmid` resolves the part type / level / position **purely** from the (oh5, oh3) pair released by digesting the plasmid with each grammar's primary or secondary Type IIS enzyme. Feature labels, plasmid name, source filename, etc. are NEVER consulted — the user's biological molecule has exactly one legal position per overhang pair, so the lookup is mechanical and unambiguous. If the digest produces overhangs that don't match any position, the classifier returns `None` (with a "couldn't classify — use New Part to set type manually" notify upstream). When you tweak a grammar's position table or add new positions, the user-facing impact is "this overhang pair now / no longer classifies"; never re-route via heuristics. Adding the GB 2.0 expanded grammar (`Promoter` GGAG/AATG combined PromUTR + `Promoter-only` GGAG/CCAT separate + `5' UTR` CCAT/AATG) was a position-table change, not a classifier change — `_classify_part_from_plasmid` itself is unchanged.

41. **Robustness sweeps #2–#6 (0.7.5→0.8.10).** Cumulative hardening; full per-fix detail in git. Key invariants future code must respect:
    * **`_feat_bounds(feat, total) → (start, end, strand)`** is the canonical wrap-aware extractor (`end < start` = origin-spanning). Use instead of raw `int(loc.start)/.end` everywhere.
    * **`_smallest_enclosing_feature(bp)`** (bisect on `_feats_starts_sorted` + wrap second pass) replaces O(N) `_feat_at` scans — use it for new bp-lookup callers.
    * **Worker pattern:** modal/screen heavy ops use `@work(exclusive=True, group=...)`. UI thread pre-captures inputs (`_collect_*_inputs`); worker emits `_on_*_failed` + `_apply_*_result` callbacks; worker body never touches `widget.update`. Workers capture `_record_load_counter` at entry and refuse on canvas reload (extends invariant #28). `_index_usage_worker` extends the same guard along the active-collection axis.
    * **Modal Ctrl+Z:** `_blocks_undo: bool = True` opts a modal out of app-level undo. **Attr must come AFTER docstring** or Python's first-statement detection breaks. Applied to Constructor/Domesticator/Mutagenize/PrimerDuplicates/PrimerDesignScreen/PartsBinModal/FeatureLibraryScreen.
    * **Primer dedup modal** defaults to KEEP (focus+Esc → Keep so stray Enter can't delete data); `_skip_primer_dedupe_check` is the test flag.
    * **Atomic writes:** `_atomic_write_bytes(path, data)` is the byte-mode counterpart to `_atomic_write_text`; all `.bak` / `.bak.<ts>` / daily-snapshot copies route through it (invariant #31 depends on this). `.dna`/bundle/token writers call `_fsync_parent_dir` after `os.replace` (POSIX rename only durable once dir-entry update is journalled). Same-second collision protector bumps `.bak.<ts>.{N}.json`.
    * **Symlink refusal:** `_safe_save_json` refuses up front via `path.is_symlink()`; `_check_agent_read_dir` via `lstat`+`S_ISDIR`; `_check_agent_write_path` walks FULL ancestor chain via `resolve()` divergence + per-segment `is_symlink()` (immediate-parent check used to let a `parent.parent` symlink redirect every agent write).
    * **`.dna` sidecar (case-collision fix, HIGH).** `_dna_sidecar_path` case-folds basename + appends 8-char SHA-1 prefix of raw `entry_id` — pre-0.8.9 a case-insensitive FS silently collided `pUC19`/`puc19`, emitting wrong molecule on export. `_dna_sidecar_legacy_path` migrates existing sidecars on first read. `_DNA_SIDECAR_BASENAME_MAX = 200`.
    * **Pre-update restore.** `_restore_pre_update_snapshot` REFUSES when manifest `sha256` missing/empty (invariant #39's sacred-four is mandatory). Manifest reads capped at `_PRE_UPDATE_MANIFEST_MAX_BYTES = 4 MB`. `_restore_from_backup` staging uses `tempfile.mkstemp` (not deterministic `.restoring`).
    * **Agent-API save uniformity.** `_LIVE_APP_REF` single-slot soft pointer to running app (set in `on_mount` + `_agent_dispatch`, cleared in `finally`). `_agent_save_or_500(save_fn, label)` wraps every agent-endpoint `_save_*` (OSError/RuntimeError → 500 + notify); `_bg_notify_save_failure(label, exc)` is the daemon-thread counterpart. Prevents silent in-memory/disk desync.
    * **Concurrency.** Module-level `_cache_lock = threading.RLock()` wraps `_safe_save_json` + cache-reassignment in every JSON save helper. Pre-fix: two concurrent saves could land `os.replace` A→B while cache reassignments landed B→A. RLock because chains nest (`_save_library` ⇒ `_sync_active_collection_plasmids` ⇒ `_save_collections`). Reads don't take the lock — `_typed_clone`-on-return + GIL protect (pairs with invariant #17). `_settings_flush_worker` try/finally so unforeseen exception can't wedge `_settings_flush_running=True`. HMMscan/BLAST run/BLAST build split into distinct `@work(group=...)` so a build can't cancel an in-flight search.
    * **Defence-in-depth size caps.** `_gb_text_to_record` rejects > `_GB_TEXT_MAX_BYTES = 64 MB`. `_h_hmmscan` routes `hmm_path` through `_safe_file_size_check` (2 GB). `_backup_info` + `_restore_from_backup` + `_safe_load_json`'s `.bak` fallback apply `_safe_file_size_check` (1 GB) symmetrically. `_h_diff_plasmid` pre-caps both seqs at `_PAIRWISE_MAX_LEN`. Export endpoints whitelist extensions via `_check_export_extension` (agent could otherwise write `.bashrc` as GenBank). `_sanitize_path` refuses `~user` (user-enumeration oracle). `splicecraft_cli.py` caps response body (50 MB) + token file (1 KB).
    * **Hygiene.** `_sweep_orphan_tmp_files` collects leftover `.tmp`/`.migrating`/`.restoring` >1 h old when lock acquired. Lockfile uses `O_EXCL` (so contention failure cleans up only the lockfile WE created). PID-alive recheck (`os.kill(pid, 0)`) on stale lock.
    * **`MultiAlignPickerModal`** ships `(entry_id, gb_text)` tuples to worker so multi-Mb-target dismiss doesn't block UI thread.
    * **Regressions.** `tests/test_sweep5.py` (21 cases) locks in sidecar case-collision, `.bak` recovery atomicity, SHA-256 mandatory, manifest size cap, backup-glob, symlink refusal, `.dna` size cap, orphan tmp sweep.

42. **Structured event logging (0.8.7+).** Design target: **user pastes log → AI parses → patch shipped same loop**. `_log_event(event, *, _stacklevel=2, **fields)` emits `event <namespaced.name> {JSON}`. Field sanitisation extends invariant #38: strings >200 chars truncated with `…[+N]`; `SeqRecord`/`Seq`/`MutableSeq`/`bytes`/`bytearray` rendered as opaque tags (`<SeqRecord id=X len=Y>`) via class-name match (no BioPython import) — accidental `_log_event('e', rec=record)` cannot leak BioPython's `__repr__`-embedded bases. `seq.chunk_dump` only logs via the structured event (raw DNA was previously also in `_log.info`); `seq.hover_copy` logs `text_len` not `text`; UI snapshot routes settings through `_scrub_path`. `@_action_log(event_name)` decorates every `action_*` (exceptions swallowed — **logging must never break the underlying action**). `@_timed(path, threshold_ms=0)` wraps heavy ops; emits `op.timed {"path": ..., "elapsed_ms": ...}`. Decorators pass `_stacklevel=3` so `funcName:lineno` lands on the wrapped method. Name convention: `app.<area>.<verb>` (user actions), `op.<area>.<verb>` (ops), `<noun>.<verb>` (state — `save.ok`/`save.failed`, `undo.*`, `redo.*`, `record.loaded`, `collection.switched`, `settings.changed`, `migration.step/failed`, `net.retry`, `lock.acquired/contended/stale/released`, `shutdown.drain.ok/timeout`). `SPLICECRAFT_DEBUG=1` bumps `_log` to DEBUG.

43. **Adversarial sweep #9 (2026-05-19, pre-v1.0.0 hardening).** Six parallel audit agents (concurrency / data-integrity / security / biology / UI / robustness) examined the 0.9.4–0.9.6 surface. 11 HIGH + 10 MEDIUM fixes shipped; regression coverage in `tests/test_sweep9.py` (50 cases). Key invariants future code must respect:
    * **Cache-bust on pre-update restore** (`_h_restore_pre_update_snapshot`) must enumerate **every** persisted-state cache including `_experiments_cache`, `_experiment_projects_cache`, `_gels_cache`. Without these the next UI mutation post-restore silently overwrites the restored data from stale in-memory state. `RestoreFromBackupModal._TARGETS` mirrors the same list for the UI path.
    * **Project-switch atomicity.** `ExperimentProjectsPickerModal._open` AND the `_do_delete` active-promotion path MUST: (a) update the active-pointer in memory via `_set_active_project_name`, (b) FORCE a synchronous flush via `_settings_flush_sync()` so disk-settings reflects the new active project, (c) write `experiments.json` for the promoted project. The old ordering (async flush before sync write) left a power-loss window where settings.json said OLD and experiments.json held NEW's entries, so the next UI save would mirror NEW into OLD's project field — silent corruption.
    * **App-level Ctrl+Z routes through the public `action_undo`** (NOT the private `_action_undo`) so modal `_blocks_undo` actually fires. Pre-fix `on_key` bypassed the guard via the private method, making invariant #41's modal-block dead code via the keyboard shortcut.
    * **`_save_experiments` mirror inside `_cache_lock`.** The `_sync_active_project_experiments(entries)` call MUST be inside the `with _cache_lock:` block — RLock allows the nested `_save_experiment_projects` re-acquire. Pre-fix release-then-mirror could persistently desync experiments.json from experiment_projects.json under future concurrent writers.
    * **`_check_data_files` covers every persisted file** (extended in sweep #9 to include experiments / experiment_projects / gels / parts_bin_collections). Without launch-time validation, corruption surfaces only on lazy first-load when a log warning may not reach the user.
    * **`_sweep_orphan_tmp_files` walks `_EXPERIMENTS_DIR` per-entry subdirs** (one level deep, symlink-refusal at each entry). Per-entry `_atomic_write_bytes` crashes otherwise leak `.tmp` files that count toward `_EXPERIMENT_DIR_MAX_BYTES` and silently squeeze legitimate attaches out over time.
    * **All new modals over an editor or text input carry `_blocks_undo: bool = True`** (attr AFTER docstring per invariant #41). Sweep #9 added it to `ExperimentProjectsPickerModal`, `ExperimentDeleteConfirmModal`, `ExperimentUnsavedChangesModal`, `ExperimentRenameModal`, `GelLibraryModal`, `ActionsPickerModal`, `ImageAttachModal`. After H3 (Ctrl+Z routing fix), missing `_blocks_undo` becomes the actual hole.
    * **Tag regex atomic-group hardening.** `_PLASMID_REF_RE` / `_ACTIONS_REF_RE` / `_GEL_REF_RE` use the `(?=([A-Za-z][\w.\-]{0,63}))\1(?![;=])` lookahead+backref idiom to prevent backtracking. Python 3.10 has no possessive quantifier or atomic group; this idiom is the portable stand-in. Without atomic-group protection the trailing `(?![;=])` reject would just shorten the match (e.g. `&amp;` would match `&am`). With it, HTML entities (`&amp;`, `&nbsp;`, `&copy;`) and URL params (`?foo=bar`) reject ENTIRELY. The captured id is still `m.group(1)`; the full match (sigil + id) is still `m.group(0)`.
    * **Windows-zip separator resilience.** Plasmidsaurus zips built on Windows can ship `category\\sample.gbk` member names in their central directory; downstream `zf.getinfo()` is exact-match. `_zf_get_member_info(zf, name)` (sweep #9) tries the exact name then falls back to a normalised scan of `zf.infolist()`. Every zip-member-lookup site (`_extract_gbk_member`, `_parse_plasmidsaurus_zip` summary + perbase reads, `_batch_extract_gbk_meta`) routes through it.
    * **SequencingScreen same-path cache invalidates on content drift.** `_zip_signature: tuple[mtime_ns, size]` augments the path-equality short-circuit so a re-run of Plasmidsaurus that overwrites the local zip with new data forces a fresh parse instead of silently showing stale samples.
    * **Lockfile staleness via argv.** `_pid_is_splicecraft(pid)` reads `/proc/<pid>/cmdline` on POSIX to detect PID-recycle-to-unrelated. Pre-fix the staleness check was `_pid_alive` only, so on a long-uptime system whose PID counter has wrapped the user could be locked out indefinitely by an unrelated bash / sshd / vim process. Returns None on platforms where the check is unimplementable (macOS, etc. — pessimistic "assume live" preserved there). Stale-lock log carries `reason="no_live_process"` or `"pid_recycled_to_unrelated"`.
    * **Clipboard image megapixel cap.** `_EXPERIMENT_CLIP_MAX_PIXELS = 50_000_000` (50 MP) — `ImageAttachModal._clip` checks `w * h` BEFORE `img.save(tmp, "PNG")` so a multi-monitor screenshot / hostile clipboard content can't bloat `/tmp` with a multi-GB PNG before the byte cap rejects. The save itself is now wrapped in try/finally so a failure unlinks the tmpfile (pre-fix, an `img.save` exception leaked the empty tempfile).
    * **`_summarize_perbase_tsv` float-aware header probe.** First-line numeric test uses `float()` not `int()`; value extraction uses `int(float(cols[2]))`. A TSV that ships fractional `reads_all` (sub-sampled assay) no longer silently discards its first data row.
    * **Legacy tag migration runs on every save** (`_normalise_experiment_entry` triggers `_migrate_legacy_tag_format` when `@plasmid:` / `@actions:` substrings are present). Pre-fix the migration ran only on disk-load, so pasted-after-load legacy tokens persisted with the old format and stayed unhighlighted until next launch.
    * **`_SETTINGS_SCHEMA` covers every persisted key** (`active_parts_bin`, `active_project`, `experiments_custom_dict` added in sweep #9). The unknown-key forward-compat passthrough caught them today, but the schema is the documented contract for "what's persisted".
    * **Highlight-map per-line caps.** `_HIGHLIGHT_LINE_LEN_CAP = 50_000` chars / `_HIGHLIGHT_PER_LINE_TAG_CAP = 500` per line on `_ExperimentMarkdownTextArea._build_highlight_map`. A 1 MB body of `@a @a @a ...` would otherwise rebuild ~750k regex matches per keystroke and saturate the editor. Body cap of 1 MB still applies; these only affect the visual coloring on pathological lines.
    * **`_confirm_delete` (Experiments) resolves cursor row FIRST**, falling back to loaded entry only when no cursor exists. Pre-fix the loaded-entry-first ordering deleted the wrong entry when the user arrow-keyed to row A while entry B was loaded (arrow keys don't fire `_on_entry_selected` — that's RowSelected = Enter).
    * **Plasmid cross-ref click prompts on unsaved canvas edits.** `_open_plasmid_ref` wraps the switch+load with `UnsavedNavigateModal` when `app._unsaved` is True (same pattern as `LibraryPanel._btn_back`). Pre-fix click-through silently discarded in-progress plasmid work.

## Persistent user preferences

User-preference toggles persist across sessions via `settings.json`.
Adding one is mechanical:

1. Class-level annotation on `PlasmidApp` with the default value (e.g. `_my_setting: bool = True`).
2. Hydrate in `PlasmidApp.compose()` next to the existing block — `self._my_setting = bool(_get_setting("my_setting", True))`. **`compose()` not `on_mount`** because Textual fires mount events leaves→root, so by the time `on_mount` runs the children have already read stale defaults.
3. In `action_toggle_my_setting`, call `_set_setting("my_setting", self._my_setting)` after flipping.
4. Surface in the Settings menu (`MenuBar.MENUS` between File and Edit; populated by the `Settings` entry in `PlasmidApp.open_menu`'s `menus` dict).

Currently persisted user toggles: `show_feature_tooltips`, `click_debug`, `check_updates`, `show_restr`, `restr_unique_only`, `restr_min_len`, `min_primer_binding`, `show_connectors`, `linear_layout`, `active_collection`, `active_grammar`. `map_mode` is **per-plasmid**, persisted on each library entry's `map_mode` field (not in `settings.json`). `_library_load` stashes the entry's preference onto the record as `_tui_map_mode`; `pm.load_record` honours the stash over the topology default; `action_toggle_map_view` + `_register_alignment` write through `_persist_map_mode_for_active` so the user's choice sticks across reloads. Sequencing-aligned plasmids auto-tag `linear` (so re-opens default to the diff-friendly view). `show_connectors` and `linear_layout` need a deferred apply via `_pending_show_connectors` / `_pending_linear_layout` because their target widgets aren't composed yet when `compose()` runs; `on_mount` reads the pending values once the children exist.

Persisted infrastructure (not user-facing toggles): `last_seen_version` (drives the What's New auto-push), `last_known_latest` + `last_update_check_ts` (24 h cache for the PyPI update probe), `hmm_db_path` (last-used HMM database path).

## Pairwise alignment + Plasmidsaurus ingestion (0.5.3+, sub-tabs 0.9.5+)

Two-stage pipeline:

1. **Zip ingestion** — `_list_gbk_members_in_zip(path)` lists `.gbk` / `.gb` / `.genbank` members; `_extract_gbk_member(path, name)` reads one out as text. Both are size-capped (`_PLASMIDSAURUS_ZIP_MAX_BYTES = 500 MB`, `_PLASMIDSAURUS_MEMBER_MAX_BYTES = 50 MB`, `_PLASMIDSAURUS_MAX_MEMBERS = 2000`) so a malformed archive can't OOM the picker. Dotfile members and directories are filtered.

2. **Structured parse** — `_parse_plasmidsaurus_zip(path)` walks the zip and groups files per sample (`{gbk, fasta, summary, perbase, histogram, coverage_plot, interactive_map, ab1_files, summary_text, perbase_coverage}`). Run-level extras (`<run>_gel.png`, README) land in `run_files`. Category folders are matched on the `_<suffix>` anchor (`_genbank-files`, `_fasta-files`, `_summary-files`, `_per-base-data`, `_histograms`, `_coverage-plots`, `_interactive-map`, `_ab1-files`) so the run-ID prefix is inferred by majority vote on the prefix-before-suffix. Standalone `.gbk` files outside any category folder are still surfaced as samples (back-compat with the older `_list_gbk_members_in_zip` shape). Summary-file bodies (≤`_PLASMIDSAURUS_SUMMARY_MAX_BYTES = 4 KB`) are streamed inline so the QC tab parses k-mer + contamination without re-opening the zip; per-base TSVs are stream-summarised line-by-line into `{mean, min, max, n_pos, above_20x}`. `_parse_plasmidsaurus_summary(text)` extracts k-mer (moles/mass) percentages + contamination % + organism source from the per-sample `.txt`.

3. **Alignment** — `_pairwise_align(query, target, mode='global'|'local')` wraps `Bio.Align.PairwiseAligner`. Returns `{mode, score, identity_pct, aligned_q, aligned_t, n_matches, n_mismatches, n_gaps, q_len, t_len}`. Length-capped at `_PAIRWISE_MAX_LEN = 200_000`. **Aligned strings come from `Alignment[0]` / `Alignment[1]`**, NOT `format()`-parsing — the text format wraps at 60 cols with coordinate prefixes which is fragile to parse.

Entry point: `Sequencing → Plasmidsaurus` sub-tab on the `SequencingScreen` (full-screen toolbar). The Plasmidsaurus pane hosts a **nested `TabbedContent`** with 4 sub-sub-tabs:

* **General** (always enabled) — `_ZipAwareDirectoryTree` zip picker + run-overview Static (run ID, sample count, gbk / per-base coverage / run-level counts). Owns the load tooling.
* **Samples** (disabled until zip loaded) — per-sample DataTable (`#align-members`): name · bp · #features · cov mean · contam % · AB1 count. Row keys carry the gbk member name so `_on_member_selected` can pipe the pick directly through `_extract_gbk_member` without re-walking the parsed dict.
* **Quality** (disabled until zip loaded) — two stacked DataTables (`#plasmidsaurus-quality-table` + `#plasmidsaurus-runfiles-table`) for k-mer / contamination / coverage metrics + run-level files.
* **Align** (disabled until zip loaded) — query indicator + target Select (`#align-target`) + Align button (`#btn-align-go`). Reads `_selected_member` set on Samples-tab row select.

Sub-tab gating runs through `_apply_subtab_gating(enabled: bool)` which toggles the `disabled` attribute on Samples / Quality / Align panes AND redirects `tabs.active` back to `psaurus-sub-general` when disabling so the user can't be stranded on a disabled-now-empty pane. Same-path re-pick short-circuits at the top of `_on_zip_picked` to avoid paying the ~50–300 ms-per-sample parse cost twice. `PlasmidsaurusAlignModal` is kept as a module-level alias of `SequencingScreen` for back-compat with tests / agent paths.

Hardening sweep (0.9.4):
* **Per-base TSV zip-bomb defence** — `_PLASMIDSAURUS_PERBASE_MAX_BYTES = 100 MB` two-layer cap: refuses upfront when central-directory `file_size` overshoots, and `_summarize_perbase_tsv` chunked-reads (64 KB) via `codecs.getincrementaldecoder` so a hostile zip decompressing into a single multi-GB line without newlines can't OOM `io.TextIOWrapper`'s line buffer.
* **Single-pass zip-open for Samples table** — `_batch_extract_gbk_meta` reads every sample's gbk inside one `ZipFile` open instead of 50× re-opens. Test asserts the open count via `monkeypatch` on `zipfile.ZipFile.__init__`.
* **NUL-anchored sentinels** — `_NO_GBK_KEY_PREFIX = "\x00no-gbk\x00"` and `_EMPTY_LIBRARY_SENTINEL = "\x00no-library\x00"` replace ambiguous `"_no_gbk_"` / `"—"` strings. NUL is rejected by `_is_safe_zip_member_name` and never appears in LOCUS-safe ids → collision-proof against any real row key.

Future expansion (already designed for): a Plasmidsaurus API key tab in the same screen that downloads run zips directly. Same downstream parse + alignment pipeline; only the ingestion source changes.

## Experiments lab-notebook (0.9.6+, projects refactor 0.9.7+)

Top-level toolbar entry (Menu → Experiments) → opens `ExperimentProjectsPickerModal` first (refactor 2026-05-18 — mirrors the parts-bin flow `PartsBinPickerModal` → `PartsBinModal`). Picking a project sets it active and pushes the full-screen `ExperimentsScreen` for that project's entries. Split-pane layout:

* **Top row** — active project label + `Projects…  [^P]` button that opens `ExperimentProjectsPickerModal` (1:1 mirror of `PartsBinPickerModal` — Open / New / Rename / Duplicate / Delete / Close).
* **Left pane (entries list)** — always-visible `DataTable` of entries in the active project, two columns: `Updated` + `Title`, natural-sort by `updated_at` desc. Long titles overflow into the table's horizontal scrollbar (no ellipsis truncation). Width: 1fr (~20%) with `min-width: 24` so the pane stays usable on narrow terminals. New / Open / Rename / Delete buttons below.
* **Right pane** — `TabbedContent[Compose | Attachments]` for the selected entry. Both tabs disabled until an entry is loaded.
  * **Compose** — full-width `TextArea` (markdown source, language="markdown"). The live Markdown preview was dropped 2026-05-18 because the narrow right pane made the side-by-side source/preview split too cramped — `_render_plasmid_refs` is preserved for future re-add (and for export paths). `@plasmid:<id>` cross-refs are inserted via the `Plasmid ref` button / `^R` (no preview click-through until preview returns).
  * **Attachments** — per-entry image grid with filename + size + "inserted in body?" flag. Attach via `ImageAttachModal` (DirectoryTree filtered to image extensions); on Win/Mac the modal also has a "Paste from clipboard" button that uses `Pillow.ImageGrab.grabclipboard()` (disabled on Linux/WSL — no pure-Python clipboard image API there per the 2026-05-18 design call).

**Projects layer (2026-05-18 — projects:experiments :: collections:plasmids):** `experiment_projects.json` holds all named projects, each carrying its own `experiments: list[dict]`. Mirrors the parts-bin pattern exactly:
* `_load_experiment_projects` / `_save_experiment_projects` — cache + `_cache_lock` + deepcopy-on-read+save (invariants #17, #41).
* `_get_active_project_name` / `_set_active_project_name` — `settings["active_project"]`.
* `_ensure_default_project` — first-run migration: wraps existing `experiments.json` entries into `_DEFAULT_PROJECT_NAME = "Main Project"`. Called from `PlasmidApp.compose()` (NOT `on_mount`) per invariant #9.
* `_sync_active_project_experiments` — `_save_experiments` calls this after every save so the multi-project record never drifts from `experiments.json` (sacred contract — analogous to invariant #10).

**Sacred invariant — Experiments mirror:** every entry save MUST go through `_save_experiments`, which calls `_sync_active_project_experiments(entries)` to keep `experiment_projects.json`'s active-project `experiments` field in lockstep with `experiments.json`. Routing a write around `_save_experiments` bypasses the mirror — same threat model as invariant #10 (collections) and the parts-bin equivalent.

Persistence: `experiments.json` envelope-v1 with the full four-layer data-safety net (invariant #31). Per-entry attachments live as files under `<DATA_DIR>/experiments/<entry_id>/`. Schema:

```
{
  "id":                   "exp-<8 hex>",     # filesystem-safe id
  "title":                str,               # <= 200 chars
  "body_md":              str,               # markdown source, <= 1 MB
  "created_at":           ISO-8601 w/ tz,
  "updated_at":           ISO-8601 w/ tz,
  "tags":                 list[str],         # max 20, <= 60 chars each
  "attached_plasmid_ids": list[str],         # denormalised xref
  "image_paths":          list[str],         # relative to attach dir
}
```

**Plasmid cross-refs (single-sigil 2026-05-18).** `@<id>` tokens inline anywhere in `body_md` — clean editor display, no noisy prefix. The negative lookbehind `(?<![\w@])` rejects emails (`user@example.com`) and double-`@`; id must start with `[A-Za-z]` so prose like "rev 2 @ 5pm" doesn't tag. `_render_plasmid_refs` rewrites tokens into markdown links with `splicecraft://plasmid/<id>` href for any export / future-preview render path.

**Action cross-refs (single-sigil 2026-05-18).** `!<id>` tokens — distinct sigil from plasmid so the two tag kinds stay visually separable in the editor. `(?<![\w!])` lookbehind blocks word-adjacent and double-`!` matches; the regex requires next char to be a letter so markdown image syntax `![alt](url)` doesn't false-match. `_EXPERIMENT_ACTIONS` is a curated catalog (Design / PCR / Restriction / Assembly / Purification / Biological / Validation buckets, 19 entries) surfaced via `ActionsPickerModal`; free-form ids accepted (catalog is convenience, not enforcement). `_extract_action_refs` denormalises into the entry's `attached_actions` list.

**Gel cross-refs (2026-05-19).** `&<id>` tokens reference saved agarose-gel snapshots in `gels.json` (see Gels section below). Single-sigil format, orange chip (`_GEL_CHIP_COLOR = "#FFB347"`), `(?<![\w&])` lookbehind blocks word-adjacent / double-`&` matches. `_extract_gel_refs` denormalises into `attached_gel_ids`. Pick from saved gels via `Gel ref` button in the Compose pane (opens `GelLibraryModal`).

**Click-to-open / Ctrl+G (2026-05-19).** Cursor-position-aware tag dispatch:
* `Ctrl+G` (or `action_go_to_tag`) scans the cursor's line for `@<id>` / `!<id>` / `&<id>` tags spanning the cursor column.
* Double-click in the TextArea posts `_ExperimentMarkdownTextArea.TagOpenRequested` which routes to the same handler.
* Plasmid hit → auto-save dirty compose → search every collection → switch active + load via `_apply_record`. Dismiss the screen first so the user lands on the loaded plasmid.
* Gel hit → push `GelLibraryModal(initial_gel_id=<id>)` scrolled to that entry.
* Action hit → push `ActionsPickerModal(initial_action=<id>)` scrolled to that catalog row.
* No-tag-under-cursor / unknown id → friendly notify, screen stays put.

**Legacy tag migration.** The pre-2026-05-18 format `@plasmid:<id>` / `@actions:<id>` is rewritten to single-sigil on every `_load_experiments` call via `_migrate_legacy_tag_format`. One-way migration; once a body lands back on disk through `_save_experiments`, the old format is gone. `_render_plasmid_refs` also routes through the migration helper defensively so external callers (export paths) handle both formats.

**In-editor token coloring (2026-05-18, extended 2026-05-19).** `_ExperimentMarkdownTextArea` subclasses Textual's `TextArea` and overrides `_build_highlight_map` to inject regex-based highlights for `@<id>` (lime `_PLASMID_CHIP_COLOR = "#9AFF80"`), `!<id>` (purple `_ACTIONS_CHIP_COLOR = "#C77FFF"`), and `&<id>` (orange `_GEL_CHIP_COLOR = "#FFB347"`). The three highlight names `splicecraft.plasmid_ref` / `splicecraft.action_ref` / `splicecraft.gel_ref` get Rich-style mappings injected (via `setdefault`) into the active theme's `syntax_styles` on every build, so a user-swapped theme retains the coloring. Two byte-offset paths: an **ASCII fast-path** (the common case — byte == char so we skip UTF-8 encoding entirely, O(K) per line with K tags) and a **non-ASCII path** that builds a codepoint→byte position table once per line then O(1) lookups (O(L+K) instead of O(K×L)). The subclass also overrides `action_delete_left` — when the cursor sits at the end of any tag (matched by tail-anchored regex), backspace deletes the entire tag instead of one char. Mid-tag and prose backspaces fall through to default behaviour. `on_click` overrides only intercept double-clicks (event.chain ≥ 2) to post `TagOpenRequested` — single-click cursor placement is untouched.

**Sacred sizing caps** (added 2026-05-18 — never bypass):
* `_EXPERIMENT_BODY_MAX_BYTES = 1_000_000` per entry. Enforced in `_normalise_experiment_entry` via deterministic truncate (better than save-refusal-with-data-loss).
* `_EXPERIMENT_IMAGE_MAX_BYTES = 10_000_000` per attached image. Enforced in `_save_experiment_image` + `_safe_file_size_check` on the source.
* `_EXPERIMENT_DIR_MAX_BYTES = 100_000_000` cumulative per entry. Enforced via `_experiment_dir_size_bytes` precheck before write.

**Filesystem invariants** mirror the .dna sidecar handling (sweep #5):
* `_sanitize_experiment_id` rejects empty / NUL / `..` / `/` / `\` / `[shell metas]` / >64 chars. All path-joins go through it.
* `_experiment_attach_dir` walks the FULL ancestor chain via `is_symlink()` (audit sweep 2026-05-18; was 2-level pre-refactor). A symlink at any depth — `_EXPERIMENTS_DIR` itself, `_DATA_DIR`, or any ancestor up to root — refuses the path. No `resolve()` divergence check (would trip on macOS `/tmp` → `/private/tmp`).
* `_save_experiment_image` writes via `_atomic_write_bytes` (tempfile + fsync + replace + parent fsync). Filename is `img-<ts>-<rand>.<ext>` so concurrent attaches can't collide. Clipboard-paste tmp files (prefix `_EXPERIMENT_CLIP_TMP_PREFIX = "exp-clip-"`) are unlinked after the bytes are copied — pre-fix the OS tmpdir slowly accumulated orphan PNGs.
* `_save_experiments` takes `_cache_lock` for the save+cache-reassign pair (invariant #41 — concurrency), then calls `_sync_active_project_experiments` (Experiments mirror invariant).
* `_persist_current` detects body-over-cap BEFORE save and notifies the user (audit sweep 2026-05-18; was silent truncate). Save path also dedup-by-id replaces ALL matches, not just the first — defensive against hand-edited JSON.

**Spellcheck** — pyspellchecker-backed (pure-Python English wordlist, no network). F7 or "Spellcheck" button → `_spellcheck_body(body_md)` masks non-prose markdown regions (fenced + inline code, image links, markdown links, raw URLs, `@plasmid:` xrefs) and tokenises with `_SPELLCHECK_WORD_RE` (alphabetic + apostrophe + hyphen, ≥ 2 chars). `SpellcheckModal` lists misspellings + suggestions; per-row Replace / Add-to-dict / Skip. Custom dictionary persists via the `experiments_custom_dict` settings key; `_clear_spellcheck_engine` invalidates the cached engine after add-to-dict.

**Hard deps added 2026-05-18:** `Pillow>=10.0` (image bytes + Win/Mac clipboard grab), `pyspellchecker>=0.8.0` (English wordlist), `rich-pixels>=3.0.0` (Unicode half-block image render in any terminal — kitty/sixel/iTerm protocols NOT required). All pure-Python wheels, no external system shell-out.

**Modal `_blocks_undo=True`** on `ExperimentsScreen` + `SpellcheckModal` so app-level Ctrl+Z can't unwind plasmid edits while the user is composing notes. App-level `on_key` early-return for `screen_stack > 1` (invariant #15) already prevents cursor / RE-highlight interactions firing underneath.

**Unsaved-changes guard (2026-05-18).** `ExperimentsScreen.action_cancel` no longer silent-saves on Esc/Close when the compose buffer is dirty. Instead, `ExperimentUnsavedChangesModal` is pushed with three buttons (Save changes · Abandon and exit · Close); default focus on Close, Esc dismisses with `"cancel"`. The screen's callback stays on top if Save fails (`_persist_current` returns False after `_notify_save_failure`) so the user can retry without losing their buffer. Both delete paths (entry + project) use `ExperimentDeleteConfirmModal` with default focus on No (sacred — a stray Enter cannot delete data). `ExperimentProjectsPickerModal._do_delete` defensively re-checks the last-project guard inside the confirm callback so a concurrent shrink can't push the project list to empty.

**Logging events:** `experiments.new`, `experiments.save`, `experiments.delete`, `experiments.attach.image`, `experiments.remove.image`, `experiments.insert.plasmid_ref`, `experiments.insert.action_ref`, `experiments.insert.gel_ref`, `experiments.spellcheck.applied`, `experiments.tag.migrated`, `project.switched`, `project.created`, `project.renamed`, `project.duplicated`, `project.deleted`, `gel.created`, `gel.renamed`, `gel.deleted`, `gel.loaded`, `gel.ref.opened`, `plasmid.ref.opened`, `action.ref.opened`. Per invariant #42 — `_log_event` payload sanitises body content (200-char truncation) so notebook prose never leaks beyond the visible UI.

**Persistent toggles touched by the projects refactor (0.9.7):** `active_project` (mirrors `active_collection` / `active_parts_bin`). `experiments_custom_dict` (spellcheck add-to-dict words) is unchanged.

## Gels (saved agarose-gel snapshots, 2026-05-19+)

`gels.json` (`_GELS_FILE`) holds saved Simulator gel configurations — the user can name + save the current lane layout + agarose % from `SimulatorScreen.Gel` pane, then load it back later or reference it as `&<id>` in an Experiments entry.

Schema (envelope v1):

```
{
  "id":          "gel-<8 hex>",     # filesystem-safe id
  "name":        str,               # <= 200 chars
  "lanes":       list[dict],        # [{name, source, detail}, ...] cap 20
  "agarose_pct": float,             # clamped 0.3–5.0; NaN/inf rejected
  "notes":       str,               # <= 2000 chars
  "created_at":  ISO-8601 w/ tz,
  "updated_at":  ISO-8601 w/ tz,
}
```

Helpers mirror experiments / projects: `_load_gels` / `_save_gels` with `_cache_lock` + deepcopy-on-read+save (invariant #17), `_safe_save_json` for the atomic write (invariant #31). `_sanitize_gel_id` rejects empty / NUL / `..` / `/` / `\` / >64 chars. `_normalise_gel_entry` caps every string field, drops non-dict lanes, clamps agarose to a sane envelope, and replaces invalid ids with a fresh `gel-<hex>`. `_find_gel(id)` returns None for unsanitisable ids (defensive against `&../etc` tag tokens). `_gel_name_taken(name)` is the dup-name guard (strip-compare, case-sensitive).

`GelLibraryModal` is the dual-context picker:
* From **`SimulatorScreen.Gel`** (Library button → opens with `current_lanes` + `current_agarose_pct`) — Save current is enabled; on dismiss-with-id, the simulator restores those lanes + agarose and re-renders.
* From **`ExperimentsScreen`** (Gel ref button → opens with no current snapshot) — Save current is disabled; dismiss-with-id inserts `&<id>` into the body.
* From the **click-to-open** path (`Ctrl+G` / double-click) — opens with `initial_gel_id` scrolled to that row.

`SimulatorScreen` has no persistent state of its own — the live `self._lanes` + `self._agarose_pct` are in-memory, only written to disk through `_save_gels` when the user explicitly saves a snapshot. Delete-last-gel is allowed (unlike the active-project guard) because there's no "active gel" concept.

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
