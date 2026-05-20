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
36. **Future-proofing scaffolding.** Six additive mechanisms:
    * `_ENTRY_MIGRATIONS` per-label `(from_v, to_v) → Callable[[dict], dict]`. `_extract_entries` runs every load through `_migrate_entries`. Failed migrators preserve entry + warn (never drop data).
    * `$SPLICECRAFT_PYPI_URL` env override (http/https only, ≤2048 chars). No caching.
    * Pre-update snapshots record `from_python_version` + `from_platform`. **`_RUNTIME_PLATFORM` cached at import** because `platform.platform()` shells out via subprocess.
    * `--dry-run` exercises detection/PyPI/snapshot then bails. Mutex with `--check`.
    * `<DATA_DIR>/.splicecraft-data-version` stamp; `_check_and_stamp_data_version()` warns to stderr on downgrade. Atomic write via `_atomic_write_text`, read capped at 128 bytes.
    * `_PLUGINS_DIR = _DATA_DIR / "plugins"` + `_RESERVED_ENTRY_FIELDS = ("_plugin_data",)`. Tested by `TestFutureProofingFeatures`.
37. **Robustness pass (0.7.6).** Tested by `TestRobustnessHardening`:
    * `_acquire_data_dir_lock` (POSIX `fcntl.flock` / Win `msvcrt.locking`) at `<DATA_DIR>/splicecraft.lock`; PID-carrying; `$SPLICECRAFT_SKIP_LOCK=1` bypass.
    * `threading.excepthook` → `_log.error`. `_chmod_user_only` 0o600 on logs/bundles. `_drain_in_flight_workers(timeout_s=2.0)` in `main()` finally.
    * `_SETTINGS_SCHEMA` + `_validate_settings` — **strict bool-vs-int** (`True` does NOT coerce into `int`). Unknown keys pass through.
    * Network retry: 1 try + 250 ms backoff on `_fetch_latest_pypi_version` + `fetch_genbank`. 4-tier `_copy_to_clipboard_with_fallback`: Textual → OSC 52 → `<DATA_DIR>/clipboard/<ts>-<label>.txt` → log.
    * `_MODAL_STACK_SOFT_CAP = 12` on `push_screen` with `callback(None)` fallback. `_apply_record` notifies > `_LARGE_PLASMID_BP = 5_000_000`. `_snapshot_data_files` skips > `_SNAPSHOT_FILE_SIZE_CAP = 50 MB`.
38. **Diagnostic logging + UI snapshot + bundle.** Three surfaces:
    * Rotating log `<DATA_DIR>/logs/splicecraft.log` (`RotatingFileHandler`, 5 MB × 4 backups, 8-char `_SESSION_ID` prefix). **NEVER log sequence content** — `_repr_for_log` truncates/summarises.
    * **`Alt+D`** App-priority → `action_capture_ui_snapshot` → `<DATA_DIR>/ui_snapshots/ui-snapshot-<ts>.md` (version, Python, platform, screen stack, focused widget, terminal size, record metadata excl. sequence, settings, 200-line log tail with `/home/<user>` → `~`). `_collect_ui_snapshot` defensive. Retention `_UI_SNAPSHOT_RETENTION = 20`. Old hover-debug moved to `alt+shift+d`.
    * `splicecraft logs --bundle [--out PATH]` zips logs + last 5 snapshots + sanitized settings + system info + README. `_scrub_path` handles `/home/<user>`, `/Users/<user>`, `C:\Users\<user>`, `Path.home()`. **Sacred privacy invariant: sequence content MUST never leak.**
39. **`splicecraft update` snapshots user data before any install subprocess.** All upgrade paths (pipx/uv-tool/uv-venv/pixi-global/pip-user/pip-venv) call `_create_pre_update_snapshot(__version__)` AFTER user confirm BEFORE `subprocess.run`. Covers `_USER_DATA_FILE_ATTRS` (10 files: library, collections, parts_bin, primers, features, feature_colors, grammars, entry_vectors, codon_tables, settings) + `_USER_DATA_DIR_ATTRS` (crash_recovery, dna_originals). **Atomic**: built in `<backup_dir>/.tmp-<rand>/`, fsynced, sealed by `os.replace`. Failure → staging removed + `OSError`/`shutil.Error` raised → exit 1. Location is **sibling** `<DATA_DIR>/../<DATA_DIR.name>-update-backups/` (override `$SPLICECRAFT_UPDATE_BACKUP_DIR`) so recursive-wipe can't kill recovery. Refuses when `_data_dir_inside_install_path()`. Restore: `splicecraft update --restore-pre-update [<id>|latest]` takes pre-restore snapshot first. Retention 5; rmtree restricted to `_PRE_UPDATE_NAME_RE`. **Sacred four restore checks**: `schema_version` ≤ `_PRE_UPDATE_SCHEMA_VERSION`, `attr` in whitelist, `name` rejects separators/`..`, SHA-256 re-verified before `os.replace`. Refusal paths + `--check` MUST NOT snapshot.
40. **Overhang pair is sacred source of truth for part classification.** `_classify_part_from_plasmid` resolves part type/level/position **purely** from the `(oh5, oh3)` pair released by each grammar's primary/secondary Type IIS enzyme. Feature labels, plasmid name, source filename NEVER consulted. Returns `None` if pair doesn't match any position (with "couldn't classify — use New Part" notify). Grammar position-table changes affect "this pair now/no longer classifies"; never re-route via heuristics. Adding GB 2.0 expanded grammar was a position-table change, not a classifier change.
41. **Robustness sweeps #2–#6 (0.7.5→0.8.10).** Key invariants:
    * **`_feat_bounds(feat, total) → (start, end, strand)`** is the canonical wrap-aware extractor. Use instead of raw `int(loc.start)/.end`.
    * **`_smallest_enclosing_feature(bp)`** (bisect on `_feats_starts_sorted` + wrap pass) replaces O(N) `_feat_at` for bp-lookup.
    * **Worker pattern:** heavy ops use `@work(exclusive=True, group=...)`. UI thread pre-captures inputs (`_collect_*_inputs`); worker emits `_on_*_failed` + `_apply_*_result`; never touches `widget.update`. Workers capture `_record_load_counter` at entry (extends invariant #28).
    * **Modal Ctrl+Z:** `_blocks_undo: bool = True` opts modal out of app-level undo. **Attr must come AFTER docstring** or first-statement detection breaks. Applied to Constructor/Domesticator/Mutagenize/PrimerDuplicates/PrimerDesignScreen/PartsBinModal/FeatureLibraryScreen.
    * **Primer dedup modal** defaults to KEEP (focus+Esc → Keep); `_skip_primer_dedupe_check` test flag.
    * **Atomic writes:** `_atomic_write_bytes(path, data)` is byte-mode counterpart to `_atomic_write_text`; all `.bak`/`.bak.<ts>`/daily-snapshot copies route through it. `.dna`/bundle/token writers call `_fsync_parent_dir` after `os.replace`. Same-second collision protector bumps `.bak.<ts>.{N}.json`.
    * **Symlink refusal:** `_safe_save_json` via `path.is_symlink()`; `_check_agent_read_dir` via `lstat`+`S_ISDIR`; `_check_agent_write_path` walks FULL ancestor chain via `resolve()` divergence + per-segment `is_symlink()`.
    * **`.dna` sidecar case-collision fix (HIGH).** `_dna_sidecar_path` case-folds basename + appends 8-char SHA-1 prefix of raw `entry_id`. `_dna_sidecar_legacy_path` migrates on first read. `_DNA_SIDECAR_BASENAME_MAX = 200`.
    * **Pre-update restore.** `_restore_pre_update_snapshot` REFUSES when manifest `sha256` missing/empty. Manifest reads capped at `_PRE_UPDATE_MANIFEST_MAX_BYTES = 4 MB`. `_restore_from_backup` staging uses `tempfile.mkstemp`.
    * **Agent-API save uniformity.** `_LIVE_APP_REF` soft pointer set in `on_mount` + `_agent_dispatch`, cleared in `finally`. `_agent_save_or_500(save_fn, label)` wraps every agent-endpoint `_save_*`; `_bg_notify_save_failure(label, exc)` daemon-thread counterpart.
    * **Concurrency.** Module-level `_cache_lock = threading.RLock()` wraps `_safe_save_json` + cache-reassignment in every JSON save helper. RLock because chains nest (`_save_library` ⇒ `_sync_active_collection_plasmids` ⇒ `_save_collections`). Reads don't take lock (`_typed_clone`-on-return + GIL). `_settings_flush_worker` try/finally so exception can't wedge `_settings_flush_running=True`. HMMscan/BLAST run/BLAST build split into distinct `@work(group=...)`.
    * **Defence-in-depth size caps.** `_gb_text_to_record` > `_GB_TEXT_MAX_BYTES = 64 MB`. `_h_hmmscan` via `_safe_file_size_check` (2 GB). `_backup_info` + `_restore_from_backup` + `.bak` fallback via 1 GB cap. `_h_diff_plasmid` pre-caps both seqs at `_PAIRWISE_MAX_LEN`. Export endpoints whitelist via `_check_export_extension`. `_sanitize_path` refuses `~user`. `splicecraft_cli.py` caps body (50 MB) + token (1 KB).
    * **Hygiene.** `_sweep_orphan_tmp_files` collects `.tmp`/`.migrating`/`.restoring` >1 h old. Lockfile uses `O_EXCL`. PID-alive recheck on stale lock.
    * **`MultiAlignPickerModal`** ships `(entry_id, gb_text)` tuples so multi-Mb dismiss doesn't block UI.
    * `tests/test_sweep5.py` (21 cases) regression coverage.
42. **Structured event logging (0.8.7+).** Design: **user pastes log → AI parses → patch shipped same loop**. `_log_event(event, *, _stacklevel=2, **fields)` emits `event <namespaced.name> {JSON}`. Strings >200 chars truncated with `…[+N]`; `SeqRecord`/`Seq`/`MutableSeq`/`bytes`/`bytearray` rendered as opaque tags (`<SeqRecord id=X len=Y>`) via class-name match (no BioPython import) — accidental `_log_event('e', rec=record)` cannot leak BioPython's `__repr__`-embedded bases. `seq.chunk_dump` only logs via structured event; `seq.hover_copy` logs `text_len` not `text`; UI snapshot routes settings through `_scrub_path`. `@_action_log(event_name)` decorates every `action_*` (exceptions swallowed — **logging must never break the underlying action**). `@_timed(path, threshold_ms=0)` wraps heavy ops; emits `op.timed`. Decorators pass `_stacklevel=3` so `funcName:lineno` lands on wrapped method. Names: `app.<area>.<verb>` (user actions), `op.<area>.<verb>` (ops), `<noun>.<verb>` (state — `save.ok`/`save.failed`, `undo.*`, `record.loaded`, `collection.switched`, `settings.changed`, `migration.step/failed`, `net.retry`, `lock.acquired/contended/stale/released`, `shutdown.drain.ok/timeout`). `SPLICECRAFT_DEBUG=1` bumps to DEBUG.
43. **Adversarial sweep #9 (2026-05-19, pre-v1.0.0).** 11 HIGH + 10 MEDIUM fixes; `tests/test_sweep9.py` (50 cases). Key invariants:
    * **Cache-bust on pre-update restore** (`_h_restore_pre_update_snapshot`) must enumerate **every** persisted-state cache including `_experiments_cache`, `_experiment_projects_cache`, `_gels_cache`. `RestoreFromBackupModal._TARGETS` mirrors.
    * **Project-switch atomicity.** `ExperimentProjectsPickerModal._open` AND `_do_delete` active-promotion MUST: (a) update active-pointer via `_set_active_project_name`, (b) FORCE sync flush via `_settings_flush_sync()`, (c) write `experiments.json` for promoted project. Pre-fix left power-loss window where settings said OLD and experiments held NEW.
    * **App-level Ctrl+Z routes through public `action_undo`** (NOT private `_action_undo`) so modal `_blocks_undo` actually fires.
    * **`_save_experiments` mirror inside `_cache_lock`.** `_sync_active_project_experiments(entries)` MUST be inside `with _cache_lock:` block — RLock allows nested `_save_experiment_projects` re-acquire.
    * **`_check_data_files` covers every persisted file** (sweep #9 added experiments/experiment_projects/gels/parts_bin_collections).
    * **`_sweep_orphan_tmp_files` walks `_EXPERIMENTS_DIR` per-entry subdirs** one level deep with symlink refusal at each entry.
    * **All new modals over editor/text input carry `_blocks_undo: bool = True`** (attr AFTER docstring). Sweep #9 added to `ExperimentProjectsPickerModal`, `ExperimentDeleteConfirmModal`, `ExperimentUnsavedChangesModal`, `ExperimentRenameModal`, `GelLibraryModal`, `ActionsPickerModal`, `ImageAttachModal`.
    * **Tag regex atomic-group hardening.** `_PLASMID_REF_RE`/`_ACTIONS_REF_RE`/`_GEL_REF_RE` use `(?=([A-Za-z][\w.\-]{0,63}))\1(?![;=])` lookahead+backref to prevent backtracking (Python 3.10 lacks possessive / atomic group). Without it, `&amp;`/URL params would partial-match.
    * **Windows-zip separator resilience.** `_zf_get_member_info(zf, name)` tries exact, falls back to normalised scan of `zf.infolist()` (Plasmidsaurus zips on Windows ship `category\\sample.gbk`).
    * **SequencingScreen same-path cache** invalidates on content drift via `_zip_signature: tuple[mtime_ns, size]`.
    * **Lockfile staleness via argv.** `_pid_is_splicecraft(pid)` reads `/proc/<pid>/cmdline` on POSIX to detect PID-recycle. Returns None where unimplementable. Stale-lock log carries `reason="no_live_process"`/`"pid_recycled_to_unrelated"`.
    * **Clipboard image megapixel cap.** `_EXPERIMENT_CLIP_MAX_PIXELS = 50_000_000` checked BEFORE `img.save`. Save in try/finally so failure unlinks tmpfile.
    * **`_summarize_perbase_tsv` float-aware** — `float()` not `int()`; extraction `int(float(cols[2]))`.
    * **Legacy tag migration runs on every save** (`_normalise_experiment_entry` triggers `_migrate_legacy_tag_format` when `@plasmid:`/`@actions:` substrings present).
    * **`_SETTINGS_SCHEMA` covers every persisted key** (sweep #9 added `active_parts_bin`, `active_project`, `experiments_custom_dict`).
    * **Highlight-map per-line caps.** `_HIGHLIGHT_LINE_LEN_CAP = 50_000` / `_HIGHLIGHT_PER_LINE_TAG_CAP = 500` on `_ExperimentMarkdownTextArea._build_highlight_map`.
    * **`_confirm_delete` (Experiments) resolves cursor row FIRST**, falls back to loaded entry only when no cursor. Arrow keys don't fire `_on_entry_selected`.
    * **Plasmid cross-ref click prompts on unsaved canvas edits.** `_open_plasmid_ref` wraps switch+load with `UnsavedNavigateModal` when `app._unsaved`.
44. **Every save is a full plasmid (2026-05-19).** User contract: every assembly path — L0 Domesticator, Constructor TU / MOD / L3+, Traditional cloning, Gibson, MoClo — lands as a library entry whose `gb_text` is a single complete circular plasmid carrying **payload + overhangs + backbone** AND every parent-fragment feature as its own annotation. Coverage closed in `TestEverySaveIsAFullPlasmid` (11 cases):
    * **L0 Domesticator → library mirror.** `PartsBinModal._new_part._on_result` runs the parts-bin save sync, then dispatches `_domesticator_library_mirror_worker` (`@work(thread=True, exclusive=True, group="dom_library_mirror")`) which calls `_part_to_cloned_seqrecord(part_dict)` and saves the full part-in-entry-vector plasmid as a library entry. **Off-thread** so a 100+ MB library doesn't freeze the UI between bin save and the success notify; mirror is best-effort (clone / save failure logs + notifies but does NOT roll back the bin row).
    * **Parts Bin Save-to-Collection for L1+.** `_save_to_collection` filter accepts rows with `sequence` OR `gb_text` (was: `sequence` only — rejected every TU / MOD silently). `_part_to_cloned_seqrecord` short-circuits at **tier 0** for L1+: `gb_text` IS the cloned plasmid, parse and return it directly. Tier 0 validates `_seq_len(rec) > 0` and falls through on a zero-length record (corrupt data) so the explicit "no sequence" ValueError surfaces instead of a degenerate 0-bp library row. `_diagnose_part_cloning` returns None for every L1+ row (with OR without gb_text) since the IIS-vector diagnostics don't apply.
    * **Colour preservation in clone simulation.** `_clone_part_marshal_vec_features` reads `ApEinfo_fwdcolor`/`ApEinfo_revcolor`/`color` from vector features (was hardcoded `"white"`). `_clone_part_build_seqrecord` writes `ApEinfo_fwdcolor`/`ApEinfo_revcolor` qualifiers when the carried colour is non-white. `_clone_part_build_part_feature` sources L0 part colour from `_GB_TYPE_COLORS[part_type]` (was hardcoded `"white"`) so Domesticator → library entries are visually typed.
    * **Wrap-feature `CompoundLocation` in `_clone_part_build_seqrecord`.** Features with `end < start` render as `CompoundLocation([(s, n_seq), (0, e)])` — origin-spanning backbone features (e.g. an Ori straddling the relegated join) survive instead of being silently dropped by the old `if e <= s: continue` guard.
    * **Structured save events for parity with Gibson.** `constructor.save.ok` / `constructor.save.failed` fired from `_save_to_library_worker` (fields: `gid`, `source_level`, `target_level`, `name`, `id`, `bp`, `n_feats`, `parts`, `backbone` / `stage`, `error`). `traditional.save.ok` / `traditional.save.failed` fired from `_save_product_to_library`. `domesticator.library_mirror.ok` / `domesticator.library_mirror.failed` fired from the worker. Naming follows the `<noun>.save.<verb>` convention `gibson.save.ok` established.
45. **Cross-platform contract (2026-05-19).** SpliceCraft runs on every modern OS / terminal pairing listed in `docs/PLATFORMS.md`. Audit sweep: `tests/test_cross_platform.py` (11 cases) covers every `sys.platform` branch via monkeypatch. Key invariants:
    * **UTF-8 stdout encoding is blocking.** `_check_terminal_capabilities()` refuses to launch on non-UTF-8 / non-Unicode terminals (the braille map U+2800-U+28FF would render as gibberish) with an actionable error pointing at `PYTHONIOENCODING=utf-8` / `LANG=C.UTF-8`. Aliases `utf8`, `cp65001` (Windows Terminal in `chcp 65001`), `utf-16`, `utf-32` all pass. Empty encoding (piped) is silently skipped.
    * **Optional Python deps degrade, never block.** `PIL` (clipboard image), `primer3` (Tm), `pyspellchecker` (F7) absence emits warnings via `_log.warning` + `_log_event("startup.terminal_capabilities", warning_count=N)` but launch proceeds. Per-feature gates handle the missing-import case at the call site.
    * **OSC 52 clipboard target is OS-specific.** `_copy_to_clipboard_osc52` writes to `/dev/tty` on POSIX, `CONOUT$` on Windows. Either failure (no controlling terminal, no console output device, encoding error) returns False so the multi-tier wrapper escalates to the file-based tier-3 fallback.
    * **Signal handlers gated by `hasattr` / `getattr`.** `SIGUSR1` (POSIX-only) faulthandler install: `if hasattr(_signal, "SIGUSR1")`. `SIGTERM` / `SIGHUP` translation: `getattr(_signal, name, None)`. Windows missing `SIGHUP` is fine.
    * **Subprocess calls always list-form, never `shell=True`.** Cross-platform quoting parity for the update install path + every other shell-out.
    * **`_RUNTIME_PLATFORM` cached at import** because `platform.platform()` shells out via subprocess on some OSes (conflicts with tests monkeypatching `subprocess.run`). Same cache pattern as invariant #36.
    * **Stale lock detection has POSIX-specific path.** `_pid_is_splicecraft(pid)` reads `/proc/<pid>/cmdline` on Linux to detect PID-recycle to unrelated processes. Returns None on macOS / Windows (no `/proc`); the conservative "assume live" stance preserves correctness.
    * **`/dev/tty` / `CONOUT$` writes (OSC 52) wrapped in `try / except (OSError, UnicodeEncodeError)`** so a broken SSH session / WSL-without-TTY / Windows legacy `conhost.exe` never crashes the clipboard helper.
47. **Entry-vector auto-detection (2026-05-20).** `_detect_entry_vector_role(record, grammar)` identifies which entry-vector role a plasmid plays via Type IIS digest fingerprinting — NOT name/label heuristics. Strategy: digest with both grammar enzymes, identify stuffer via `_fragment_has_backbone_marker` exclusion (NEVER size — `feedback_never_assume_smaller_frag_is_payload`), match `(inner enzyme, outer overhang pair)` → role via Golden Braid binary-assembly architecture. Returns `(role, "strict")` for α/Ω acceptors whose inner digest matches the canonical TU boundary AND outer digest matches a recognised pair; `("", "weak")` for UPD-style L0 donors with non-canonical stuffers; `None` for L0 parts, TUs, MODs, or anything else. `_resolve_acceptor_role(grammar, inner_enz, outer_pair)` carries the gb_l0 mapping (`Alpha1=Esp3I-outer-(GGAG,GTCA)`, `Alpha2=Esp3I-outer-(GTCA,CGCT)`, `Omega1=BsaI-outer-(GGAG,GTCA)`, `Omega2=BsaI-outer-(GTCA,CGCT)`). `_auto_bind_entry_vectors_from_entries(entries)` runs detection across every grammar for a batch of library entries, binds new roles via `_set_entry_vector` (NEVER clobbers existing user bindings; strict > weak on collisions). Surfaced two ways:
    * **Auto on collection import** — `_bulk_import_folder` callers append the auto-bind summary onto the import success notification. Zero-click happy path: user imports their EDEN/FFE folder, classification works immediately.
    * **Settings → Entry Vectors…** — `EntryVectorsModal` (`_blocks_undo=True`) shows every role for the active grammar in one table with Pick/Clear/Auto-detect controls. Replaces the single-slot widget in `GrammarEditorModal` (which only exposed the empty-role binding). Grammar selector at top lets the user reconfigure for any grammar. Default focus on the table so arrow keys + Enter drive the picks. Esc → close.
    * Tested by `tests/test_entry_vectors_auto.py` (17 cases) covering α1/α2/Ω1/Ω2 strict detection, UPD weak match, rejection of L0 parts / TUs / linear records / marker-less plasmids, auto-bind precedence (strict > weak), no-clobber guarantee, modal smoke test.
48. **Master Delete (2026-05-20).** File menu → "⚠ Master Delete (wipe all user data)…" wipes every persistent SpliceCraft file the user can produce — library, collections, parts bin, parts-bin collections, primers, features, feature colours, custom grammars, entry vectors, codon tables, settings, experiments, experiment projects, gels, agent token, data-version stamp, all `.bak` / `.bak.<ts>` siblings, daily snapshots, lost-entries spillover, UI snapshots, clipboard tmpfiles, crash-recovery autosaves, `.dna` sidecars, plugins dir, rotated log backups, `.migrated` legacy marker, AND the sibling pre-update-backups directory. True clean slate — no built-in restore path. Tested by `tests/test_master_delete.py` (33 cases). Sacred — two independent blockers against accidental fire:
    * **No agent endpoint.** `_AGENT_HANDLERS` has zero handlers whose name contains `master`/`wipe`/`nuke`/`delete_all`/`clean_slate`/`purge`/`destroy`. Enforced by `test_no_agent_endpoint_exposes_wipe`.
    * **Module-local sentinel.** `_perform_master_delete(app, *, sentinel)` raises RuntimeError BEFORE any disk op unless `sentinel is _MASTER_DELETE_SENTINEL` (a bare `object()` only the GUI flow holds a reference to). Enforced by `test_sentinel_mismatch_refused_before_any_disk_op`.
    * **Typed-YES gate (`MasterDeleteModal`).** Stage 1 Input must read exactly `"YES"` — case-sensitive, no `.strip()` / `.casefold()`. Cancel default-focused; Esc → cancel. Delete button starts disabled and only enables on the exact-match `Input.Changed`. `_blocks_undo = True`.
    * **3-second cool-down (`MasterDeleteConfirmModal`).** Stage 2 confirm button starts disabled with a countdown label (`set_interval(0.1, …)` ticks `_cooldown_remaining`) and only enables after `_MASTER_DELETE_CONFIRM_COOLDOWN_S` (3 s) elapses. Default focus on `No, keep my data`. `_blocks_undo = True`.
    * **No keyboard binding.** `action_master_delete` is reachable from the File menu only. Enforced by `test_no_keyboard_binding_for_master_delete` (greps the source for `Binding(... master_delete ...)`).
    * **Re-entrancy guard.** `PlasmidApp._master_delete_in_progress` flips True at action entry, False in every terminal callback (stage-1 cancel, stage-2 cancel, result-modal dismiss, wipe error). A menu double-click finds the flag set and notifies "already running"; no second flow stacks. Enforced by `test_action_master_delete_reentrancy_guard`.
    * **Lockfile + active log preserved.** `_master_delete_residual_paths` resolves both `_DATA_DIR / "splicecraft.lock"` and `_DATA_DIR / "logs"` and excludes them from the residual sweep. The process holding the lock survives the wipe; the `RotatingFileHandler` writes survive to a still-open file. `_master_delete_log_files` separately removes rotated log backups (`splicecraft.log.1`, `.2`, …) while skipping `_LOG_PATH`.
    * **Residual sweep (defense in depth).** After the named-target pass, `_master_delete_residual_paths` walks `_DATA_DIR.iterdir()` and unlink/rmtrees every file/dir that isn't the lockfile or logs/. Any future persistence file added without a corresponding `_USER_DATA_FILE_ATTRS` entry still gets wiped. `summary["residual_files"]` / `residual_dirs` counts surface in the result modal.
    * **Cache reset under `_cache_lock`.** Every name in `_MASTER_DELETE_CACHE_ATTRS` (14 entries — every `_*_cache` global) reset to None inside the lock, after the file-level deletion but before the lock releases. A concurrent `_save_*` queued behind us thus finds the cache emptied and writes the empty-shape payload onto a never-existing path. `_settings_flush_pending` cleared FIRST so the daemon flush worker doesn't beat the wipe to disk.
    * **Agent server stopped first.** `_stop_agent_api(app._agent_api_server)` before the wipe so an authenticated agent session can't re-populate data into the wiped tree. Token file unlinked by `_stop_agent_api` as a side effect.
    * **Pre-wipe scope count on main thread.** `action_master_delete` enumerates file/dir targets BEFORE pushing the first modal so both modals + the post-wipe summary quote the same numbers. Scope count failures degrade to zeros (the wipe itself still proceeds).
49. **Load-time collision resolution (2026-05-20).** Every load path that lands user-named items in a persistent list — parts bin (single + bulk Load Part, Domesticator), plasmid library (`LibraryPanel.add_entry`), primer library (`PrimerDesignScreen._save_primers_btn`) — routes through `_resolve_load_collisions(app, entity, new_items, existing_items, *, name_key, content_fn, on_resolved, on_cancelled)`. Two collision categories, two modals:
    * **Exact copy** (same name + same content) → `ExactCopyConfirmModal[bool]`. Default focus on Skip; "Keep as COPY" renames via `_ensure_unique_copy_name` (" COPY" / " COPY 2" / …) so the duplicate can coexist with its twin. Returns True to keep, False/None to skip.
    * **Name collision** (same name, different content) → `NameCollisionModal[str]`. Three-way: keep original (default), overwrite, cancel. Cancel aborts the ENTIRE load (caller does NOTHING) — surfaced via `on_cancelled` callback. Keep drops the colliding new items; Overwrite removes existing entries whose name matches and lets the new ones land.
    * **Counter-based parts-bin delete** — `PartsBinModal._delete_selected` switched from set-membership on `(name, sequence)` to `Counter`; selecting one of two duplicates removes one, not both. Pre-fix bug: legacy duplicates collapsed into one set element and got nuked together. Sacred regression guard.
    * **Modals both carry `_blocks_undo: bool = True`** (attr AFTER docstring per sweep #6); ID-replace path in `LibraryPanel.add_entry` (same `record.id`) is NOT a collision — treated as "editing the same record" — and bypasses the modal flow. Tested by `tests/test_parts_bin_dedupe.py` (24 cases) + `tests/test_collision_flows.py` (5 cases).
50. **Adversarial sweep #10 (2026-05-20, pre-v1.0.0 follow-up to sweep #9).** 11 HIGH fixes, `tests/test_sweep10.py` (25 cases). Targets bugs introduced by sweep #9 / invariant #49 + classes the earlier sweeps missed:
    * **Batch-collision flow (`LibraryPanel.add_entries_batch`).** `_save_to_collection` pre-fix called `lib.add_entry(rec)` in a loop, each colliding rec stacking a `NameCollisionModal` while the post-loop toast claimed success before any modal resolved (and the modal stack soft cap would silently default-resolve at N=12). New batch API pre-classifies the whole batch, routes ONE collision flow, and fires `on_done(saved_names, replaced_names, cancelled)` once after the user resolves both modals. Toast emits from `on_done`, not from the loop. Diagnostics (stub-backbone warnings) also surface from `on_done` so they only fire for parts that actually landed.
    * **Modal double-fire guard.** `ExactCopyConfirmModal` + `NameCollisionModal` carry a `_dismissed: bool` flag and a `_dismiss_once(payload)` helper. Pre-fix rapid double-clicks between the button-press handler and the async `pop_screen` completion could fire the same callback twice with potentially different payloads (Textual's `dismiss()` returns an AwaitComplete; the result-callback runs before the pop finishes). Every exit path (`_skip`/`_keep`/`_overwrite`/`_cancel_btn`/`action_cancel`) goes through `_dismiss_once`.
    * **`_blocks_undo` retrofit.** 6 modals gained the flag: `EditSeqDialog`, `AddFeatureModal`, `FeatureEditModal`, `NewPlasmidModal`, `GrammarEditorModal`, `RestoreFromBackupModal`. Every one mutates canvas / record / persistent grammars / persistent library — app-level Ctrl+Z under them could race the in-progress edit (the bug class invariant #41 catches).
    * **`RestoreFromBackupModal` cache-bust enumeration drift.** Pre-fix only reset 4 of 14 persisted-state caches. Sweep #9 added experiments / experiment projects / gels persisted files but the restore modal was never extended; restoring `experiments.json` from .bak left `_experiments_cache` stale and the next UI mutation silently overwrote the freshly-restored disk state. Now mirrors the full 13-cache enumeration from `_h_restore_pre_update_snapshot` (settings deliberately omitted — restoring settings would clobber the live app's pointer state mid-session).
    * **Save-chain lock-release gap.** `_save_library` + `_save_parts_bin` pre-fix released `_cache_lock` BEFORE calling their mirror helper (`_sync_active_collection_plasmids` / `_sync_active_parts_bin_parts`), so Thread A's write + Thread B's write + Thread B's mirror + Thread A's stale-snapshot mirror could leave the mirror file out of sync with the live file. Mirror calls moved INSIDE the `with _cache_lock:` block. RLock allows re-entry from the mirror's own `_save_collections` / `_save_parts_bin_collections` chain. `_commit_library_entries` (post-modal commit path) similarly wraps its cache reseat in the lock.
    * **Entry-vector RMW under lock.** `_set_entry_vector` + `_clear_entry_vectors_for_grammar` did load-mutate-save without `_cache_lock`. Two concurrent calls both read pre-state, both wrote, last writer lost the other's binding. The auto-bind flow's per-role loop amplified the race (TOCTOU on every iteration's `_get_entry_vector`). Now the full RMW runs under `_cache_lock`; RLock allows the nested `_save_entry_vectors` re-entry.
    * **`_history_node_to_dict` stack-overflow fix.** Pre-fix recursed through `node.parents`, blowing Python's default recursion limit (1000) on hostile `.dna` imports carrying deeply-nested `<HistoryTree>` (32 MB cap can yield 3M+ nodes). Sibling helpers `walk`, `_history_node_count`, `HistoryScreen.populate` were already iterative for this reason; `_history_node_to_dict` was overlooked. Now iterative DFS with explicit `_HISTORY_NODE_MAX_DEPTH = 500` + `_HISTORY_NODE_MAX_NODES = 100_000` caps. Truncation surfaces via `_truncated` markers in the output dict.
    * **`_bulk_import_folder` symlink refusal.** Pre-fix `path.is_file()` + `path.stat()` both FOLLOW symlinks — a hostile `.gb` symlinked to `/dev/zero` (devices report `st_size=0`) passed the size cap and `load_genbank` then read until OOM. Symlinks to `~/.ssh/id_rsa` or similar would also expose private data to any subsequent agent fetch of the imported entry. Now uses `path.lstat()` + `stat.S_ISREG(st.st_mode)` to refuse symlinks outright; the per-file check fires AGAIN inside the import loop (defence-in-depth against TOCTOU between the children-scan and the load).
    * **`_get_rotated_state` cache key.** Pre-fix keyed on `(id(self._seq), id(self._feats), o)`. Python's small-string interning + allocator reuse means two distinct strings can share the same id after one is GC'd, surfacing a stale rotated cache after an in-place edit that happened to reuse the previous string's address. `_build_seq_inputs` (line 6564) had been fixed for this same reason; rotated-state was overlooked. Now uses `(hash(self._seq), id(self._feats), o)`.
    * **`except (AttributeError, Exception)` collapse.** `_reset_app_state_after_master_delete` carried the misleading tuple form — `Exception` subsumes `AttributeError`, so the clause was just `except Exception`. The invariant #1 "no bare except" sweep would have flagged this. Narrowed to `except (TypeError, AttributeError)` for the `.clear()` site (read-only mapping defence) and `except AttributeError` for the simple attribute access.
51. **Adversarial sweep #11 (2026-05-20, deferred items from sweep #10).** 6 areas, `tests/test_sweep11.py` (18 cases). Closes the architecture-larger items sweep #10 deferred:
    * **Active-pointer flush-sync.** Every active-pointer flip that's followed by a sync file write now calls `_settings_flush_sync()` between them — mirrors the sweep #9 fix for project switch. Applied to 4 sites: `LibraryPanel._on_select` (collection switch), `LibraryPanel._request_collection_delete` (delete-promote), `PartsBinPickerModal._open` (parts-bin switch), `PartsBinPickerModal._delete` (parts-bin delete-promote). Pre-fix a power loss between the async daemon-flush and the sync file write left settings.json saying OLD while the dependent file held NEW; next mutation mirrored NEW into OLD, silently overwriting OLD's data.
    * **Cross-group worker RMW under `_cache_lock`.** Every worker that load-modify-saves the plasmid library — Constructor `_persist_assembly`, Traditional `_save_product_to_library`, Gibson `_gibson_save_worker`, Domesticator `_domesticator_library_mirror_worker` — now wraps the read-modify-save in `_cache_lock`. Pre-fix `exclusive=True` only serialised within ONE `@work` group; two workers from DIFFERENT groups could both read the same pre-state, both insert a new entry, both save — the second writer silently dropped the first's entry. RLock allows re-entry from the inner `_save_library` lock. Constructor + Gibson + Traditional ALSO re-disambiguate the unique `name` / `id` inside the lock to defend against a racing worker that took the originally-unique slot.
    * **Settings flush worker drain on Master Delete.** `_perform_master_delete` now BUSY-WAITS up to 2s under `_settings_flush_lock` for `_settings_flush_running == False` before proceeding with the file unlinks. Pre-fix the daemon worker only held the lock long enough to swap out the pending payload; it then released the lock and called `_safe_save_json` on its own — so a wipe that cleared `_settings_flush_pending` mid-write would find settings.json resurrected by the worker's atomic `os.replace` AFTER the wipe pass.
    * **UI freeze on bulk-import auto-bind closed via `@work` + batched writes.** `_auto_bind_entry_vectors_from_entries` previously ran N × M (entries × grammars) GenBank parses AND N synchronous `_set_entry_vector` writes on the UI thread immediately after `_bulk_import_folder` returned, freezing the UI for several seconds on a 100-entry × 3-grammar import. Now (a) gb_text is parsed ONCE per entry (not N × M), (b) all auto-detected bindings accumulate into a single `_set_entry_vectors_batch` call (one `_save_entry_vectors` write instead of N), (c) the whole detection runs in `LibraryPanel._auto_bind_worker` (`@work(thread=True, exclusive=True, group="auto_bind_evs")`); the import-success toast fires immediately + a follow-up notify lands when the worker finishes.
    * **`_find_library_entry_by_id` helper.** New helper that takes `_cache_lock`, walks `_library_cache` directly, and clones only the matched entry — O(N) walk with O(1) cloned bytes instead of `_load_library()`'s O(N) cloned bytes per single-entry lookup. Migrated 4 hot-path callsites: `LibraryPanel._refresh_active_row` (status badge refresh), `LibraryPanel._on_delete` (delete confirmation), `SequencingScreen._do_align` (target plasmid resolve), `PlasmidApp._action_discard_changes` (Esc-from-canvas reload). Pre-fix each click paid a full library deepcopy.
    * **Agent API hardening.** `_h_create_collection` now does an ATOMIC name-taken re-check inside `_cache_lock` so a GUI user (or second agent client) creating a same-named collection during the multi-second `_bulk_import_folder` window surfaces 409 instead of silently clobbering. `_h_hmmscan` collapses its "not found" / "rejected" / "too large" responses to a single generic 400 + log line — pre-fix an unauthenticated local process could probe arbitrary filesystem paths via the error-message differential. `_build_commercialsaas_features_packet_from_record` strips ASCII control characters from `<V text=>` qualifier attributes before serialising; pre-fix hostile values containing `\x01` etc. produced `.dna` files that BioPython accepted on write but `_safe_xml_parse` choked on during round-trip, silently dropping the colour + label overrides.
52. **UI audit sweep #12 (2026-05-20, four-phase render/modal/reactive pass).** `tests/test_sweep12.py` (23 cases). Audit ran each phase independently then consolidated:
    * **Phase 1 — `action_aspect_inc/dec` early-exit at limit.** Holding `,` (widen) at the 5.0 cap or `.` (heighten) at the 0.5 floor previously fired `self.notify(...)` AND assigned `self._aspect = round(...)` on every key tick — Toast widgets queued up at the screen edge and the reactive churn fired Textual watchers redundantly. Post-fix: `new = round(...)`, `if new == self._aspect: return`, then assign + notify. Same equality-check pattern as the `tooltip` cache at `PlasmidMap._tooltip_for` (line 11197) which already documented "Avoid spamming reactive churn with identical writes."
    * **Phase 2 — `_blocks_undo` retrofit onto 12 input-bearing modals.** Modals carrying user-facing `Input` / `TextArea` widgets (or committing persistent mutations) must opt in to the `_undo_blocked_by_modal` guard so app-level Ctrl+Z falls through to the Input's built-in undo instead of mauling the canvas underneath. Added to: `MultiRecordFastaModal`, `CustomEnzymeListModal`, `PrimerEditModal`, `ColorPickerModal`, `PartEditModal`, `SpeciesPickerModal`, `CollectionsModal`, `CollectionNameModal`, `NewCollectionModal`, `NamePlasmidModal`, `RenamePlasmidModal`, `MinPrimerBindingModal`. Same after-docstring placement as sweep #10 (invariant #41) — `TestBlocksUndoAttrPlacement` white-box checks that the attr lands AFTER both `"""` triples.
    * **Phase 3 — reactive & widget hygiene (no source changes; documented baseline).** Audit found ONE `watch_*` method (`PlasmidMap.watch_origin_bp`), ZERO `self.query(...)` callsites (everything routes through `query_one`), debounced search via `_SearchInput._on_input_changed` + `Input.Submitted`-only repopulate. `TestReactiveHygieneInvariants` regression-locks these as soft caps (≤3 watch methods total; `query_one` outnumbers `query` by 20×) so a future feature can't drift the baseline silently.
    * **What this sweep did NOT do.** The render-perf audit produced exactly ONE fixable finding (aspect notify spam); the Explore agent's HIGH-tagged "id() on rotated state" finding was a false positive — `_get_rotated_state` returns CACHED `_rotated_seq` / `_rotated_feats` whose identity IS stable across calls with the same `_rotated_cache_key`, so the outer `_view_cache_key` keying on `id(disp_seq)` works correctly. Documented here so a future reader doesn't re-investigate.

## Persistent user preferences

`settings.json` via `_get_setting`/`_set_setting`. To add:

1. Class-level annotation on `PlasmidApp` with default (e.g. `_my_setting: bool = True`).
2. Hydrate in `PlasmidApp.compose()` — `self._my_setting = bool(_get_setting("my_setting", True))`. **`compose()` not `on_mount`** (mount fires leaves→root, so by `on_mount` children read stale defaults).
3. `action_toggle_my_setting` calls `_set_setting("my_setting", self._my_setting)` after flipping.
4. Surface in Settings menu (`MenuBar.MENUS` between File and Edit; `Settings` entry in `PlasmidApp.open_menu`'s `menus` dict).

Persisted toggles: `show_feature_tooltips`, `click_debug`, `check_updates`, `show_restr`, `restr_unique_only`, `restr_min_len`, `min_primer_binding`, `show_connectors`, `linear_layout`, `active_collection`, `active_grammar`. `map_mode` is **per-plasmid** on each library entry's `map_mode` field. `_library_load` stashes as `_tui_map_mode`; `pm.load_record` honours over topology default; `action_toggle_map_view` + `_register_alignment` write through `_persist_map_mode_for_active`. Sequencing-aligned plasmids auto-tag `linear`. `show_connectors`/`linear_layout` need deferred apply via `_pending_show_connectors`/`_pending_linear_layout` (targets not composed yet in `compose()`).

Persisted infrastructure: `last_seen_version` (What's New auto-push), `last_known_latest` + `last_update_check_ts` (24 h PyPI cache), `hmm_db_path`, `active_parts_bin`, `active_project`, `experiments_custom_dict`.

## Plasmidsaurus ingestion + pairwise alignment (0.5.3+, sub-tabs 0.9.5+)

Two-stage pipeline:

1. **Zip ingestion** — `_list_gbk_members_in_zip(path)` lists `.gbk`/`.gb`/`.genbank`; `_extract_gbk_member(path, name)` reads one out. Size-capped (`_PLASMIDSAURUS_ZIP_MAX_BYTES = 500 MB`, `_PLASMIDSAURUS_MEMBER_MAX_BYTES = 50 MB`, `_PLASMIDSAURUS_MAX_MEMBERS = 2000`).

2. **Structured parse** — `_parse_plasmidsaurus_zip(path)` groups per sample (`{gbk, fasta, summary, perbase, histogram, coverage_plot, interactive_map, ab1_files, summary_text, perbase_coverage}`). Run-level extras (`<run>_gel.png`, README) → `run_files`. Category folders matched on `_<suffix>` anchor (`_genbank-files`, `_fasta-files`, `_summary-files`, `_per-base-data`, `_histograms`, `_coverage-plots`, `_interactive-map`, `_ab1-files`); run-ID by majority vote. Standalone `.gbk` outside category folders still surfaced. Summary bodies (≤`_PLASMIDSAURUS_SUMMARY_MAX_BYTES = 4 KB`) streamed inline; per-base TSVs stream-summarised into `{mean, min, max, n_pos, above_20x}`.

3. **Alignment** — `_pairwise_align(query, target, mode='global'|'local')` wraps `Bio.Align.PairwiseAligner`. Returns `{mode, score, identity_pct, aligned_q, aligned_t, n_matches, n_mismatches, n_gaps, q_len, t_len}`. Capped at `_PAIRWISE_MAX_LEN = 200_000`. **Aligned strings come from `Alignment[0]`/`Alignment[1]`**, NOT `format()`-parsing.

Entry: `Sequencing → Plasmidsaurus` on `SequencingScreen`. Nested `TabbedContent` with 4 sub-sub-tabs: **General** (always enabled, `_ZipAwareDirectoryTree` + overview), **Samples** (DataTable `#align-members`, row keys carry gbk member name), **Quality** (`#plasmidsaurus-quality-table` + `#plasmidsaurus-runfiles-table`), **Align** (query + target Select `#align-target` + `#btn-align-go`). Gating via `_apply_subtab_gating(enabled)`. Same-path re-pick short-circuits at top of `_on_zip_picked`. `PlasmidsaurusAlignModal` module-level alias of `SequencingScreen` for back-compat.

Hardening (0.9.4):
* **Per-base TSV zip-bomb defence** — `_PLASMIDSAURUS_PERBASE_MAX_BYTES = 100 MB` two-layer cap (central-directory check + chunked `codecs.getincrementaldecoder` 64 KB).
* **Single-pass zip-open** — `_batch_extract_gbk_meta` reads every sample in one `ZipFile` open.
* **NUL-anchored sentinels** — `_NO_GBK_KEY_PREFIX = "\x00no-gbk\x00"`, `_EMPTY_LIBRARY_SENTINEL = "\x00no-library\x00"` (collision-proof).

## Experiments lab-notebook (0.9.6+, projects refactor 0.9.7+)

Top-level (Menu → Experiments) → `ExperimentProjectsPickerModal` first (mirrors `PartsBinPickerModal` → `PartsBinModal`). Picking pushes full-screen `ExperimentsScreen`. Split-pane:

* **Top row** — active project label + `Projects… [^P]` button (Open/New/Rename/Duplicate/Delete/Close).
* **Left pane** — entries DataTable `Updated` + `Title`, natural-sort `updated_at` desc. Width 1fr (~20%) `min-width: 24`. New/Open/Rename/Delete.
* **Right pane** — `TabbedContent[Compose | Attachments]`. Disabled until entry loaded.
  * **Compose** — `TextArea` markdown source. Live preview dropped 2026-05-18; `_render_plasmid_refs` preserved for export.
  * **Attachments** — per-entry image grid via `ImageAttachModal`; Win/Mac clipboard paste via `Pillow.ImageGrab.grabclipboard()` (Linux/WSL disabled — no pure-Python API).

**Projects layer (projects:experiments :: collections:plasmids):** `experiment_projects.json` holds named projects, each with `experiments: list[dict]`.
* `_load_experiment_projects`/`_save_experiment_projects` — cache + `_cache_lock` + deepcopy.
* `_get_active_project_name`/`_set_active_project_name` — `settings["active_project"]`.
* `_ensure_default_project` — first-run wraps existing `experiments.json` into `_DEFAULT_PROJECT_NAME = "Main Project"`. Called from `compose()` per invariant #9.
* `_sync_active_project_experiments` — called by `_save_experiments` after every save.

**Sacred — Experiments mirror:** every save MUST go through `_save_experiments` → `_sync_active_project_experiments(entries)`. Routing around bypasses mirror (same threat as invariant #10).

`experiments.json` envelope-v1 schema:

```
{
  "id":                   "exp-<8 hex>",
  "title":                str,               # <= 200 chars
  "body_md":              str,               # <= 1 MB
  "created_at":           ISO-8601 w/ tz,
  "updated_at":           ISO-8601 w/ tz,
  "tags":                 list[str],         # max 20, <= 60 chars
  "attached_plasmid_ids": list[str],
  "image_paths":          list[str],         # relative to attach dir
}
```

**Plasmid cross-refs.** `@<id>` inline. Lookbehind `(?<![\w@])` rejects emails / double-`@`; id must start `[A-Za-z]`. `_render_plasmid_refs` → markdown links with `splicecraft://plasmid/<id>` href.

**Action cross-refs.** `!<id>` (distinct sigil). `(?<![\w!])` blocks word-adjacent / double-`!`; next char must be letter so `![alt](url)` doesn't false-match. `_EXPERIMENT_ACTIONS` curated catalog (19 entries) via `ActionsPickerModal`; free-form ids accepted.

**Gel cross-refs (2026-05-19).** `&<id>` references saved gels in `gels.json`. Orange chip `_GEL_CHIP_COLOR = "#FFB347"`. `_extract_gel_refs` denormalises into `attached_gel_ids`. Pick via `Gel ref` button → `GelLibraryModal`.

**Click-to-open / Ctrl+G.** Ctrl+G / double-click scans line for `@`/`!`/`&` tag spanning cursor column. Plasmid hit → auto-save dirty compose → search every collection → switch + load → dismiss. Gel hit → `GelLibraryModal(initial_gel_id=<id>)`. Action hit → `ActionsPickerModal(initial_action=<id>)`.

**Legacy tag migration.** Pre-2026-05-18 `@plasmid:<id>`/`@actions:<id>` rewritten on every `_load_experiments` via `_migrate_legacy_tag_format`. One-way; once saved through `_save_experiments`, old format gone.

**In-editor token coloring.** `_ExperimentMarkdownTextArea` overrides `_build_highlight_map` for `@<id>` (lime `_PLASMID_CHIP_COLOR = "#9AFF80"`), `!<id>` (purple `_ACTIONS_CHIP_COLOR = "#C77FFF"`), `&<id>` (orange `_GEL_CHIP_COLOR = "#FFB347"`). Highlight names `splicecraft.plasmid_ref`/`.action_ref`/`.gel_ref` injected into theme's `syntax_styles` via `setdefault`. ASCII fast-path vs non-ASCII path (codepoint→byte table). Backspace at end of tag deletes whole tag. `on_click` intercepts only double-clicks (event.chain ≥ 2) for `TagOpenRequested`.

**Sacred sizing caps:**
* `_EXPERIMENT_BODY_MAX_BYTES = 1_000_000` per entry (deterministic truncate).
* `_EXPERIMENT_IMAGE_MAX_BYTES = 10_000_000` per image.
* `_EXPERIMENT_DIR_MAX_BYTES = 100_000_000` per entry.

**Filesystem invariants** (mirror .dna sidecar):
* `_sanitize_experiment_id` rejects empty/NUL/`..`/`/`/`\`/shell metas/>64 chars.
* `_experiment_attach_dir` walks FULL ancestor chain via `is_symlink()`. No `resolve()` divergence (would trip on macOS `/tmp` → `/private/tmp`).
* `_save_experiment_image` via `_atomic_write_bytes`. Filename `img-<ts>-<rand>.<ext>`. Clipboard tmpfiles (prefix `_EXPERIMENT_CLIP_TMP_PREFIX = "exp-clip-"`) unlinked after bytes copied.
* `_save_experiments` takes `_cache_lock` for save+cache-reassign, then `_sync_active_project_experiments`.
* `_persist_current` detects body-over-cap BEFORE save (notifies). Save path dedup-by-id replaces ALL matches.

**Spellcheck.** pyspellchecker-backed (pure-Python English wordlist). F7 / "Spellcheck" → `_spellcheck_body(body_md)` masks non-prose markdown regions and tokenises via `_SPELLCHECK_WORD_RE` (alphabetic + apostrophe + hyphen, ≥2 chars). `SpellcheckModal`: Replace/Add-to-dict/Skip per row. Custom dict via `experiments_custom_dict` settings key; `_clear_spellcheck_engine` invalidates cached engine after add.

**Hard deps:** `Pillow>=10.0`, `pyspellchecker>=0.8.0`, `rich-pixels>=3.0.0` (pure-Python wheels).

**Modal `_blocks_undo=True`** on `ExperimentsScreen` + `SpellcheckModal`.

**Unsaved-changes guard.** `ExperimentsScreen.action_cancel` pushes `ExperimentUnsavedChangesModal` (Save/Abandon/Close; default Close, Esc → cancel) when buffer dirty. Screen callback stays on top if Save fails so user can retry. Delete paths use `ExperimentDeleteConfirmModal` default No (sacred — stray Enter cannot delete data). `ExperimentProjectsPickerModal._do_delete` re-checks last-project guard inside confirm callback.

**Logging events:** `experiments.*` (new/save/delete/attach.image/remove.image/insert.{plasmid,action,gel}_ref/spellcheck.applied/tag.migrated), `project.*` (switched/created/renamed/duplicated/deleted), `gel.*` (created/renamed/deleted/loaded/ref.opened), `plasmid.ref.opened`, `action.ref.opened`. `_log_event` sanitises body (200-char truncation).

## Gels (saved agarose-gel snapshots, 2026-05-19+)

`gels.json` (`_GELS_FILE`) holds saved Simulator gel configurations. Schema envelope-v1:

```
{
  "id":          "gel-<8 hex>",
  "name":        str,               # <= 200 chars
  "lanes":       list[dict],        # [{name, source, detail}, ...] cap 20
  "agarose_pct": float,             # clamped 0.3–5.0; NaN/inf rejected
  "notes":       str,               # <= 2000 chars
  "created_at":  ISO-8601 w/ tz,
  "updated_at":  ISO-8601 w/ tz,
}
```

Helpers mirror experiments/projects: `_load_gels`/`_save_gels` with `_cache_lock` + deepcopy-on-read+save, `_safe_save_json` for atomic write. `_sanitize_gel_id` rejects empty/NUL/`..`/`/`/`\`/>64. `_normalise_gel_entry` caps all string fields, drops non-dict lanes, clamps agarose, replaces invalid ids with fresh `gel-<hex>`. `_find_gel(id)` returns None for unsanitisable ids (defensive against `&../etc`). `_gel_name_taken(name)` strip-compare case-sensitive.

`GelLibraryModal` dual-context picker:
* From `SimulatorScreen.Gel` (Library button) — opens with `current_lanes` + `current_agarose_pct`; Save current enabled; dismiss-with-id restores lanes/agarose and re-renders.
* From `ExperimentsScreen` (Gel ref button) — no current snapshot; Save current disabled; dismiss-with-id inserts `&<id>`.
* From click-to-open — opens with `initial_gel_id` scrolled to row.

`SimulatorScreen` has no persistent state; live `self._lanes`/`self._agarose_pct` are in-memory, only written via `_save_gels` on explicit save. Delete-last-gel allowed (no "active gel" concept).

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

## For future agents

1. Read this file first, then `git log --oneline` for recent context.
2. `python3 -m pytest -n auto -q` before and after any change. `tests/test_dna_sanity.py` (<2 s) is the fast inner loop.
3. Don't break sacred invariants. Don't bypass `_safe_save_json`. Don't add bare `except`.
4. Eyeball real-world behaviour on pUC19 (`L09137`) and pACYC184 (`MW463917.1`).
5. Past fix history is in git — `git show <hash>` beats stale prose.
