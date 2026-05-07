# SpliceCraft Changelog

---

## [0.7.4.0] — 2026-05-07

### Added — origin rotation cascades across all three views

- **Map rotation now reorders the feature sidebar.** When the user
  rotates the plasmid map (← / → / [ / ] / Shift+ / mouse wheel),
  the FeatureSidebar re-sorts so the feature nearest the new
  origin (clockwise) lands at row 0. Sort key is the modular
  distance ``(start - origin_bp) % total`` with a tiebreak on
  ``end``; wrap features get the same modular shift so they land
  at the right position. With ``origin_bp == 0`` the sort reduces
  to the historical ``(start, end)`` ordering — no change for
  users who don't rotate.
- **Map rotation now shifts the sequence viewer.** The seq panel
  rotates its display so the new origin's base is the first cell
  of the viewer (display row 0, column 0). Internal state stays
  in absolute record coords — clicks, hover tooltips, edits,
  saves, undo / redo, and selection ranges are all converted at
  the boundary so the rotation is purely cosmetic. Line numbers
  show display coords (1, 61, 121, ... starting from the new
  origin); ``Home`` resets to the absolute origin.
- **Alt+O sets the highlighted feature as the new origin.** Bound
  on the plasmid map, feature sidebar, and sequence panel — all
  three route through ``PlasmidMap.action_set_origin_to_selected``
  so a single keystroke from any panel re-anchors the display
  origin at the highlighted feature's start. No-op on linear maps
  (rotation only makes sense on a circular topology); notifies
  the user when nothing is selected.
- **OriginChanged Message** — the cascade backbone. Whenever
  ``origin_bp`` reassigns on PlasmidMap, the watcher posts
  ``OriginChanged(origin_bp, total_bp)``; the App handler calls
  ``FeatureSidebar.set_view_origin`` and ``SequencePanel.set_view_
  origin``. Decouples the panels — they don't need to know about
  PlasmidMap directly.

### Internal

- ``SequencePanel`` gains ``_view_origin_bp``, ``_get_rotated_state()``
  (cached on ``(seq id, feats id, origin)``), and the
  ``_abs_to_disp`` / ``_disp_to_abs`` coordinate-conversion helpers.
- ``_click_to_bp``, ``_bp_to_content_row``, ``_hover_at`` now use
  the rotated views for chunk lookup + convert back to absolute
  on return so click resolution + hover stay correct under
  rotation. Hover-tooltip ``bp`` field reports absolute coords so
  the user always sees their record position.
- 6 new tests in ``test_smoke.py``: 2 sort-key tests for the
  origin-rotation math, 4 cascade integration tests covering the
  full ``map rotation → sidebar reorder → seq panel rotate``
  pipeline + the Alt+O binding + the absolute↔display round-trip.

### Documentation

- Help modal (``?``) lists Alt+O and the ``[`` / ``]`` rotation
  keys. Existing rotation entry now mentions all the supported
  keystrokes.

---

## [0.7.3.0] — 2026-05-07

### Fixed (correctness — Golden Braid L0 cloning simulation)

- **IIS-cloning simulation now respects the entry vector's dropout
  overhangs, not the part's BsaI junction overhangs.** The user's
  `oh5`/`oh3` on a parts-bin entry are the L1-junction overhangs
  exposed AFTER L0 cloning (e.g. `AATG`/`GCTT` for a Golden Braid
  CDS, exposed by BsaI in the next assembly step). The L0 cloning
  step itself uses Esp3I and the entry vector's dropout overhangs
  (e.g. `CTCG`/`TGAG` for pUPD2 / FFE 1 ENTRY UPD). Previously
  `_clone_part_into_entry_vector` matched the part's `oh5`/`oh3`
  against the vector's Esp3I cut overhangs and bailed when they
  didn't match — so a CDS designed in SpliceCraft's Domesticator
  (BsaI overhangs only) saved into a real pUPD2-style entry vector
  silently fell back to the pUPD2 stub backbone instead of the
  user's actual vector. Now the simulator identifies the dropout
  fragment first (smallest fragment after IIS digest), then
  synthesises an insert with the dropout's overhangs as sticky
  ends and `oh5 + insert + oh3` as the cloned content. Result:
  byte-exact correct cloned plasmids that match a real bench
  reaction. Verified end-to-end with aeBlue (`/blueWT.fasta`)
  cloned into `FFE 1 ENTRY UPD.dna` via Esp3I → 2433 bp plasmid
  with FuGFP cassette excised + aeBlue in its place.
- **Save-to-Collection diagnostic notify.** When a part lands in
  the collection with a stub backbone instead of the user's
  configured entry vector (no vector configured, vector lacks
  ≥2 enzyme cuts), a per-part warning toast surfaces the reason
  via the new `_diagnose_part_cloning(part)` helper. Without this
  the user just saw "Saved N parts" and had no idea their
  cloning fell back. Notification timeout extended to 12 s so
  the explanation has time to land.

### Added (UI + UX)

- **Multi-record FASTA → bulk import.** Loading a FASTA with
  multiple records now prompts to import all sequences into a new
  collection. Per-record linear/circular detection.
- **Parts-bin Save-to-Collection.** New button + multi-select
  (Ctrl/Shift/Alt+click + drag). Selected parts are simulate-cloned
  into the active grammar's entry vector (or stub fallback) and
  added as fresh library entries. Acts on toggled parts uniformly
  whether the toggle came from modifier-click or drag.
- **Parts-bin Delete + multi-delete.** New Delete button + Delete
  keyboard binding. Multi-select deletes show a count in the
  warning modal. Removes the built-in catalog rows (no sequence
  → not actionable from this UI).
- **File browsers everywhere paths were typed.** Ctrl+O modal,
  Library `+` button, Domesticator source pickers, etc. Highlights
  FASTA in pink, `.dna` in orange.
- **Settings: typeable minimum primer length.** Replaced the
  cycling presets with a free-form integer input.
- **Feature sidebar sort by genome order from origin.** Sorts
  ascending by `start`, ties broken by `end`. Wrap features
  appear at their origin-relative position.

### Fixed (UI consistency)

- **Stale data clearing on deletion.** Deleting the currently-
  loaded plasmid from the library now clears the canvas + sequence
  panel + sidebar. Same fix applied to parts-bin deletes (the
  loaded part's data no longer lingers after the part is removed).
- **Crash-recovery notice fires once per leftover set.** Previously
  it re-displayed on every load while leftovers existed.
- **Domesticator: source group moved to the top.** First thing a
  user picks is where the part comes from.
- **Backbone + selection marker reflect the user's configured
  entry vector** for L0 parts (not the hardcoded pUPD2 /
  Spectinomycin defaults).

### Internal

- New helpers `_clone_part_into_entry_vector` (rewritten with
  synthesis fallback), `_splice_part_into_vector_by_overhang`
  (refuses sequence-splice when vector has IIS cuts whose
  overhangs conflict with part's overhangs — prevents silent
  wrong-position splice), `_diagnose_part_cloning` (Save-to-
  Collection notify backend).
- Test coverage: 4 new edge-case tests for the synthesis path
  (3-cut vector, zero-overhang part, no-cuts vector diagnostic,
  no-vector diagnostic) plus a regression test capturing the
  exact aeBlue + FFE-1-style scenario that motivated the fix.

---

## [0.7.2.0] — 2026-05-06

### Fixed (review pass — hardening + correctness)

- **Render-cache eviction.** `_BUILD_SEQ_CACHE`, `_CHUNK_LAYOUT_CACHE`,
  `_CHUNK_STATIC_CACHE`, and `_CHUNK_OVERLAY_CACHE` were converted from
  blanket-`.clear()` / FIFO-pop eviction to `OrderedDict` LRU with
  `move_to_end` on hit and `popitem(last=False)` on miss. Matches the
  proven `_RESTR_SCAN_CACHE` idiom. Cycling through 5+ open plasmids
  no longer pays the full chunk-layout rebuild cost on every cycle.
- **`fetch_genbank` size cap.** New `_NCBI_GB_MAX_RESPONSE_BYTES` (64 MB)
  + `handle.read(MAX + 1)` + bail pattern. Closes the last NCBI ingest
  path that wasn't size-capped — every legitimate plasmid / cosmid /
  BAC / small chromosome still fits while a multi-GB pathological
  response (compromised server, MITM) is refused.
- **`notify()` markup-injection escapes.** ~12 `notify()` callsites
  that interpolate user-controlled names (collection / feature /
  grammar / plasmid / primer / record names + feature labels) now
  pass `markup=False`. A library entry named `[red]boom[/]` can no
  longer break the toast layout or render misleading markup.
- **`_CommercialSaaSHistoryNode.walk()` iterative.** Replaced the
  yield-from recursion with a stack-based pre-order traversal. A
  hostile `.dna` history XML with 1000+ deep nested `<Node>` chains
  can no longer trip the CPython recursion limit.
- **`_feature_library_match` memoization.** The `(name, type) →
  sequence` index is now cached by `_features_generation` so the
  one-off lookup helper doesn't iterate the whole feature library on
  every call.
- **`_restr_scan_worker` `NoMatches` debug log.** When widgets are
  unmounted between scan start and apply, the swallow now logs at
  `DEBUG` instead of vanishing silently — surfaces in transcripts
  for future diagnosis.
- **`_tick_progress` is_mounted guard.** Bulk-import progress callbacks
  fired after the modal closes now early-return cleanly instead of
  doing two `query_one` calls + double `except NoMatches` blocks per
  tick.
- **`_spill_lost_entries` atomicity.** The lost-entries safety dump
  now routes through `_atomic_write_text` so a mid-write crash
  (disk full, RO mount, power loss) leaves either nothing or a
  complete recovery dump — never a half-written file masquerading as
  evidence. Safety-net for the safety-net.

### Added (responsiveness)

- **Async settings writes.** `_set_setting` updates the in-memory cache
  synchronously and dispatches the disk flush to a daemon thread with
  coalescing — a burst of 5 toggles in 50 ms now collapses to 1–2 disk
  writes instead of 5. UI no longer blocks on fsync when the user
  toggles `r` / `c` / aspect / etc. New `_settings_flush_sync()` is
  called from `main()`'s `finally` so the user's last toggle reaches
  disk before the daemon thread is killed by interpreter shutdown
  (bounded 2 s wait).
- **`LibrarySearchModal` debounce.** `Input.Changed` schedules
  `_refresh` via `set_timer(0.15 s)` instead of running the cross-
  collection scan per-keystroke. A 5-character query in a 200 ms burst
  now triggers 1 search instead of 5 — matters on libraries with
  thousands of plasmids.
- **`OpenFileModal` background load.** Large `.gb` / `.dna` parses
  (BAC/cosmid records, `.dna` files with rich history XML) now run on
  a `@work(thread=True)` worker that mirrors the `FetchModal._do_fetch`
  pattern. The modal shows "Parsing…" immediately + disables the
  buttons + remains interactive (Esc cancels). Stale-modal guard via
  `is_mounted` so a user who hits Esc mid-parse doesn't crash on
  dismiss-after-dismiss.

### Changed (UX)

- **`LibraryPanel` width fixed at 25 cells** (sum of the plasmid-view
  button row: 4 buttons × 5 `min-width` + 4 × 1-cell margin + 1
  border-right). Pre-2026-05-06 the panel grew to fit the longest
  plasmid name (capped at ~59 cells), eating map real estate for
  libraries with descriptive names. Names + status + bp wider than
  the panel now scroll horizontally inside the table via
  `overflow-x: auto`. The map gains the freed cells; the column
  width logic is preserved for the in-table scroll bounds.

### Fixed (packaging)

- **CHANGELOG.md bundled in the wheel.** Added to
  `[tool.hatch.build.targets.wheel].only-include` so `pipx`/`pip` users
  get the in-app "What's New" modal text without falling back to the
  GitHub-link placeholder. Added a third lookup candidate
  (`Path(__file__).parent.parent / "CHANGELOG.md"`) for editable
  installs.

### Tests

- `test_round_trip_through_disk` updated to call
  `_settings_flush_sync()` between `_set_setting` and the cache-clear
  re-read, since the disk write is now async.
- F-key panel-restore tests updated for the new 25-cell library
  width.
- All 1606 tests pass; the suite picked up no regressions.

---

## [0.7.1.0] — 2026-05-06

### Fixed (data safety — defense in depth)

A user-reported library wipe motivated a four-layer hardening of the
JSON persistence path. None of these change the on-disk schema; every
existing library + collections file keeps loading without migration.

- **Layer 1 — multi-generation rotating backups.** `_safe_save_json`
  now writes the prior content to BOTH `<file>.bak` (the legacy single-
  generation kept for back-compat with existing tooling) AND
  `<file>.bak.YYYYMMDD-HHMMSS` (timestamped, lex-sortable). The last
  `_BACKUP_RETENTION_COUNT` (10) rotating backups are retained on disk;
  older ones pruned after each save. Two consecutive bad saves can no
  longer wipe history.
- **Layer 2 — daily launch-time snapshot.** On every new calendar day
  the user starts SpliceCraft, `_snapshot_data_files` copies each
  persistent JSON file (`plasmid_library.json`, `collections.json`,
  `parts_bin.json`, `primers.json`) to
  `<DATA_DIR>/snapshots/<stem>-YYYY-MM-DD.json`. Last
  `_SNAPSHOT_RETENTION_DAYS` (30) days are retained. Best-effort —
  silent on permission / disk-full failures so a sandboxed install
  never aborts the launch.
- **Layer 3 — suspicious-shrink spillover.** When a save would discard
  >50% of a populated library (with at least 5 prior entries), the
  dropped entries are dumped to
  `<DATA_DIR>/lost_entries/<stem>-<timestamp>.json` BEFORE the save
  proceeds. The save itself still runs (the user may have legitimately
  pruned the library), but the data is never silently destroyed.

### Added (data safety — recovery surface)

- **Layer 4 — Settings → Restore library / collections from backup…**
  opens `RestoreFromBackupModal` listing every recoverable copy across
  the four storage tiers (legacy bak, rotating bak, daily snapshot,
  lost-entries spillover) for a chosen target file. Pick a row and the
  live file is overwritten with the chosen source — itself routed
  through `_safe_save_json` so the *current* state lands in a fresh
  rotating backup. Every restore is reversible.

### Tests

- 18 new tests in `tests/test_data_safety.py`:
  - `TestSafeSaveJsonMultiGenBackup` — rotation, retention cap, no
    spurious backup on first write.
  - `TestSafeSaveJsonShrinkSpillover` — suspicious-shrink dumps, routine-
    delete passthrough, threshold suppression on small libraries.
  - `TestSnapshotDataFiles` — write-once-per-day, missing/empty file
    skip, retention prune, OSError tolerance.
  - `TestListAndRestoreBackups` — discovery across all four tiers,
    restore-creates-fresh-backup, unparseable-source rejection.
- New modal-baseline coverage for `RestoreFromBackupModal`.

### Roadmap

- v1.0.0.0 scope status: 6/6 v1.0 features done; the data-safety
  hardening backfills a "STABLE" requirement from before features.
  SnapGene .dna round-trip remains the long-pole.

---

## [Unreleased] — Phase 4 stability gate

### Fixed

- **`_diff_align_worker` now captures `_record_load_counter` at entry** and refuses to push `AlignmentScreen` if the user paged to a different plasmid mid-alignment. Brings the new diff worker in line with the same stale-load contract `_restr_scan_worker` and `_seed_default_library` follow.
- **`_find_annotation_transfers` whole-plasmid match.** When `feat_len == n_tgt` the wrap-fold collapsed `t_e` to `t_s` and the dedupe key aliased every full match — a circular permutation of the same plasmid returned 0 transfers. Now special-cased to a single `[0, n_tgt)` transfer.
- **`_apply_annotation_transfers` degenerate wrap.** `t_e == 0` (origin-spanning end at the origin itself) used to construct `FeatureLocation(0, 0)` which Biopython rejects on serialise; now collapses to a single tail `FeatureLocation(t_s, n)`.
- **`_h_find_orfs` empty-record guard.** A record with `seq=""` or `annotations=None` (partial-parse edge case) used to traverse `(rec.annotations or {})` then call `_find_orfs` on an empty string. Now short-circuits to `{orfs: [], count: 0}`.
- **`_search_collections_library` skips id-less entries.** Library entries with no `id` would round-trip as `(collection, "")` and the loader's `entry.get("id") == ""` match aliased every untagged entry to the first one found — picking the wrong plasmid.

### Added (CLAUDE.md)

- Sacred-invariants entries #26–#30 covering GFF3 off-by-one + wrap-split convention, annotation-transfer exact-match contract + whole-plasmid case, pairwise-alignment cancellation semantics, cross-collection search id requirement, and agent-endpoint active-collection scope.

### Tests

- +3 regression guards: whole-plasmid annotation-transfer match, `_h_find_orfs` on empty / no-annotations record, `_search_collections_library` skipping id-less entries.

---

## [0.7.0.0] — 2026-05-06

### Added

- **Diff with another plasmid** (Phase 2.1) — File → Diff with another plasmid… opens `PlasmidPickerModal` to choose a comparison target, runs `_pairwise_align` in a `@work(thread=True, exclusive=True, group="diff_align")` worker so the UI stays responsive, then pushes the existing `AlignmentScreen`. New `_h_diff_plasmid` agent endpoint returns the same alignment result dict (`{score, identity_pct, aligned_q, aligned_t, n_matches, n_mismatches, n_gaps, q_len, t_len}`) shape so an agent can answer "how similar are these two" in one round-trip.
- **Annotation transfer** (Phase 2.2) — Edit → Transfer annotations from… picks a source library entry, runs the new `_find_annotation_transfers` exact-sequence matcher across both strands (and across the origin on circular targets), and previews the matched coords in `AnnotationTransferModal`. "Apply all" appends matched features as `SeqFeature`s on the loaded record (wrap matches become `CompoundLocation` of `[start, n) + [0, end)`) — undo-able in one Ctrl+Z. New `_h_transfer_annotations` agent endpoint with `dry_run` (default true) so an agent can preview before committing. Skips features below 30 bp by default to silence primer-binding-site noise.
- **GFF3 export** (Phase 3) — File → Export as GFF3 (.gff3)… writes the loaded record as GFF3 1.26: `##gff-version 3` header, `##sequence-region` pragma, synthesised top-level `region` row carrying `Is_circular=true` for circular plasmids, one tab-separated row per `FeatureLocation` part. Wrap features become two rows joined by a shared `ID=...` (the standard GFF3 split-feature convention); attribute values are percent-encoded so labels containing `;` / `=` round-trip cleanly. New `_h_export_gff` agent endpoint mirrors the existing `_h_export_genbank` / `_h_export_fasta` shape.

### Tests

- 29 new tests across `_find_annotation_transfers` (forward / RC / wrap / min-length / no-match / source-feature-skip), `_record_to_gff3` (header / coords / strand / wrap split / `Is_circular` / `source` skip / attribute escaping), `_export_gff_to_path` round-trip, the three new agent endpoints (`diff-plasmid`, `transfer-annotations`, `export-gff`), and modal-fits-in-baseline-terminal coverage for `AnnotationTransferModal` and `GffExportModal`.

### Roadmap

- v1.0.0.0 scope status: FASTA export ✓, GFF export ✓, diff view ✓, ORF finder ✓, annotation transfer ✓, cross-collection search ✓, SnapGene .dna round-trip (in flight), stability gate (next).

---

## [0.6.0.0] — 2026-05-06

### Added

- **Whole-plasmid FASTA export** — File → Export as FASTA (.fa)…  Pushes `FastaExportModal` (already used by the feature-library + parts-bin export flows) pre-populated with the loaded record's name + sequence. The `_h_export_fasta` agent endpoint already existed; this wires the GUI front-door.
- **ORF finder** — Edit → Find ORFs… opens `ORFFinderModal` showing every six-frame ORF over the loaded record. Configurable min length (default 30 aa) + opt-in alternative bacterial starts (GTG / TTG). Wrap-aware on circular plasmids: ORFs crossing the origin are reported with `end < start` matching the existing wrap-feature convention. Row pick highlights the ORF in the seq panel + map. New `_find_orfs` helper + `_h_find_orfs` agent endpoint.
- **Cross-collection plasmid search** — File → Find plasmid (all collections)… opens `LibrarySearchModal` with a fuzzy-matched live-filtered table of every plasmid across every collection on disk. Selecting a row switches the active collection (if needed) and loads the plasmid through the existing `_apply_record` flow. New `_search_collections_library` helper + `_h_search_library` agent endpoint.

### Tests

- 26 new tests covering `_find_orfs` (forward / reverse / wrap / alt-starts / dedupe), `action_export_fasta`, `_search_collections_library`, `action_find_plasmid`, the new agent endpoints (`find-orfs`, `search-library`), and modal-fits-in-baseline-terminal coverage for `ORFFinderModal` and `LibrarySearchModal`.

### Roadmap

- v1.0.0.0 scope locked: FASTA export ✓, GFF export, diff view, ORF finder ✓, annotation transfer, cross-collection search ✓, SnapGene .dna round-trip (in flight), stability gate. No CLI — every new feature ships an agent-API endpoint instead.

---

## [0.5.13.0] — 2026-05-06

### Security

- **`.dna` history XML routes through `_safe_xml_parse`.** `_parse_commercialsaas_history` previously called `ET.fromstring` directly, leaving the import path open to billion-laughs / DOCTYPE entity expansion on a hostile `.dna` file. Now defangs DOCTYPE/ENTITY before parsing.
- **Streaming LZMA decompression for the `.dna` history packet.** `_extract_commercialsaas_history_xml` now uses `LZMADecompressor(...).decompress(payload, max_length=cap+1)` so a compressed bomb that would expand to gigabytes is rejected at the cap rather than after materialising the full plaintext.
- **NCBI esearch / esummary and Kazusa response reads are size-capped.** New constants `_NCBI_MAX_RESPONSE_BYTES` (4 MB) and `_KAZUSA_MAX_RESPONSE_BYTES` (1 MB); a hostile / mis-configured upstream can no longer stream gigabytes at the worker. Mirrors the existing PyPI cap pattern.
- **`_h_load_file` agent-API endpoint is size-capped at `_BULK_IMPORT_MAX_BYTES` (50 MB)** with `force=true` override for legitimate chromosome-scale assemblies. A runaway agent script can no longer OOM the worker by pointing at a 10 GB file.
- **`_safe_load_json` is size-capped at `_SAFE_LOAD_JSON_MAX_BYTES` (50 MB).** Defends against a corrupt / mis-restored / hostile-shared library file that would otherwise be slurped whole before validation.
- **`_dna_sidecar_path` tightened.** Path traversal IDs (`..`, `/`, `\`, NUL bytes) and dot-only segments are normalised via `Path(...).name` + sentinel fallback so the resulting sidecar always lands inside `_DNA_ORIGINALS_DIR`.

### Fixed

- **`_safe_save_json` re-raises on save failure.** Previously the outer `except Exception` logged-and-swallowed; UI state silently desynced from disk on disk-full / RO-mount / permission-denied. Callers can now catch and `notify` the user. Sacred invariant #7 documents the new contract.
- **`AlignmentScreen` per-part dissects target wrap features.** `int(loc.start)` on a `CompoundLocation` returns `min(parts.start)` and silently flattens wrap CDS (sacred invariant #9). The alignment annotation lane now iterates `loc.parts` so each arc-half labels its own columns. Wrap CDS no longer renders across the wrong arc.
- **`_excise_fragment_pair` rejects ≥3-cut digests on circular plasmids.** Helper now surfaces a clear "got N cut sites; need exactly 2" error so callers can't silently ship `fragments[0:2]` from an ambiguous N-fragment pool. Restriction-cloning correctness depends on this.
- **`_load_parts_bin` and `_load_primers` deepcopy on read.** Previous shallow `list(...)` copy let caller mutations of nested `qualifiers` / primer-pair dicts poison the cache for every subsequent reader. Sacred invariant #17 now extends to both.
- **BLASTP DB-build silent skips now `_log.debug`.** Malformed entries / failed translations were dropped without a trace; the failure is now diagnosable from the log.

### Changed

- **Persisted `.dna` and helper-script identifiers are renamed.** Public API for the trademarked binary plasmid format (the popular commercial plasmid editor's `.dna`) now uses generic identifiers (`_iter_commercialsaas_packets`, `_extract_commercialsaas_history_xml`, `_inject_commercialsaas_history`, `ExportCommercialSaaSModal`, `_CommercialSaaSHistoryNode`, etc.). The `.dna` file format magic bytes and the BioPython API contract string are stored hex-encoded as `_COMMERCIALSAAS_COOKIE_MAGIC` and `_BIOPYTHON_DNA_FMT`. User-facing prose says "popular commercial plasmid editor file format". `.dna` import / export / round-trip behaviour is unchanged.
- **Three confirmed-dead functions removed** (~31 lines): `_blast_index_kmers`, `SequencePanel._scroll_to_row`, `SequencePanel._annot_feats_sorted`. No callers in source or tests; verified before removal.
- **`tests/test_commercialsaas_io.py` integration tests** are now gated by the `SPLICECRAFT_DNA_FIXTURES_DIR` environment variable instead of a hard-coded local path, so the suite skips cleanly on machines without the fixtures.
- **CLAUDE.md** documents 8 new pitfalls / invariants (#18–#25) covering the scrub policy, the streaming-decompress contract, response-size caps, sidecar sanitisation, the agent-endpoint cap, the load-json cap, and the excise-2-cut rule.

### Added

- **Traditional restriction-digest + ligation cloning engine** — `_enzyme_cuts`, `_digest_with_enzymes`, `_make_synthetic_fragment`, `_excise_fragment_pair`, `_simulate_traditional_cloning`, and the supporting `_close_circular` machinery. Powers the `ConstructorModal` "Traditional" tab. Wrap-feature aware on circular plasmids; sticky and blunt overhangs handled separately; orientation enumerated for both forward and reverse-complement insert pairings; per-fragment `_split_features_at_cuts` preserves annotation lineage across the cut. Tests in `tests/test_traditional_cloning.py`.
- **Round-trip writer for the popular commercial plasmid editor's `.dna` binary format** — `_write_commercialsaas_dna_bytes` builds a from-scratch `.dna` from a SeqRecord (cookie + DNA + features XML + notes + optional history), `_inject_commercialsaas_history` splices a new `<HistoryTree>` XML into existing sidecar bytes preserving every unhandled packet verbatim, and the construction-history `<HistoryTree>` is modelled as a typed `_CommercialSaaSHistoryNode` tree (XML ↔ Python). The popular commercial editor's free viewer can open the resulting `.dna` files.
- **`_BIOPYTHON_DNA_FMT` constant** — single hex-encoded source of truth for the BioPython SeqIO format identifier used by the `.dna` parser. Replaces scattered string literals.
- **Agent-API endpoints for entry vectors and plasmid status** (`set-entry-vector`, `get-entry-vector`, `set-plasmid-status`, `get-plasmid-status`, plus 8 more parity endpoints), wired to the same code paths the GUI uses so external CLI agents can drive every flow the GUI offers without UI duplication.

### Tests

- +17 regression guards documenting the 2026-05-06 fix date in their docstrings: streaming-bomb rejection, alignment-screen wrap-feature dissect, agent-endpoint size cap (4 cases), NCBI + Kazusa response caps (2), `_excise_fragment_pair` ≥3-cut, `_dna_sidecar_path` traversal / dot-only / NUL / absolute-path (4), `_load_parts_bin` + `_load_primers` deepcopy (2), `_safe_save_json` re-raise + tempfile cleanup (2), `_safe_load_json` size cap.
- Total: 1528 (was 1511, with 4 sample-file integration tests now gated by env var).

---

## [0.5.12.0] — 2026-05-04

### Added

- **Plasmid workflow status field.** Each library entry can now carry a workflow status — `DESIGNING` (purple), `CLONING` (orange), `SEQUENCING` (blue), or `VERIFIED` (green) — set via `PlasmidStatusPickerModal` (5-radio picker, Esc to cancel). Triggered by pressing `s` on a library row in the plasmids view. The status persists across re-saves, mirrors into the active collection through `_save_library`, and survives renames.
- **Status column in the library panel** — dedicated column rendering the status text in its colour, plus a colour-circle prefix on the plasmid name itself for at-a-glance scanning. Rows with no status reserve the same 2-cell prefix slot so name columns stay aligned.
- **Dynamic plasmid-name column width.** Library panel measures the longest plasmid + collection name on every repopulate and resizes both panel and column to fit, clamped to `[12, 30]` cells. Long names no longer clip to 14; absurdly long names still cap so they can't push the map / sidebar off-screen.
- **PyPI update check on launch.** Background `@work` worker hits `https://pypi.org/pypi/splicecraft/json`, compares the published version against the running `__version__`, and toasts a friendly upgrade hint (`pipx upgrade splicecraft`) when a newer release is available. Pure-stdlib (urllib + json), 3 s timeout, 24 h cache in `settings.json` (`last_known_latest` + `last_update_check_ts`), 256 KB response cap, polite UA. Toggle via `Settings → [✓] Check for updates on launch` (persisted as `check_updates`); skipped under tests via the class-level `_skip_update_check` flag.
- New helpers: `_sanitize_plasmid_status`, `_parse_pypi_version`, `_is_newer_pypi_version`, `_fetch_latest_pypi_version`, `_primer_tm_safe` (memoized).

### Changed

- **What's New modal — content trimming + colour overhaul.** Body is now capped at the 3 most recent releases by default (`_WHATS_NEW_MAX_VERSIONS = 3`); previous all-24-versions render was visibly slow on cold open. Footer now points at the GitHub changelog (`https://github.com/Binomica-Labs/SpliceCraft/blob/master/CHANGELOG.md`) for older releases. Dialog colour scheme moved off the loud `$accent` (orange in `dark-ansi`) and onto `$success` (green) for the title, border, every Markdown heading level (H1–H4), inline code spans (`MarkdownBlock > .code_inline`), and links. Cache key upgraded to `(path, mtime)` so an in-session edit to `CHANGELOG.md` isn't masked by a stale render.
- **`[Unreleased]` block in `CHANGELOG.md` folded into `[0.4.4]`** with a leading "catch-up entry" note — covers the features that landed across the 0.2.x → 0.4.x development arc before the per-release changelog convention. The What's New modal also now filters out any non-numeric heading defensively, so future `[Unreleased]` staging during dev won't sneak into the modal.
- **PrimerEditModal performance + hardening.** Per-keystroke Tm calc is memoized via `@lru_cache(512)` on `_primer_tm_safe` so retyping doesn't re-run primer3 thermodynamics; `_seq_changed` now does a single TextArea query and threads the value through both stats line and preview repaint. Bare `except Exception` in `_stats_line` narrowed to `(ImportError, OSError, ValueError, RuntimeError, TypeError)`. Custom 5'-prefix capped at 100 bp (defence against pasted blobs); primer sequence capped at 500 bp on save; preview window capped at 2000 cells (malformed feat coords can't allocate giant cell lists).

### Fixed

- **Pre-compiled regexes** for the changelog heading parser and the IUPAC primer-prefix validator — both were being recompiled on each call (Python's regex cache amortised it, but explicit module-level compilation is clearer and faster on cold paths).

### Tests

- +13 tests covering: plasmid status sanitizer (strict canonical-only acceptance), status persistence through re-save, name-column width cap + floor, `PlasmidStatusPickerModal` boundary, `_parse_pypi_version` strict parser, `_is_newer_pypi_version` comparator, `_primer_tm_safe` bounds + cache hit, `_build_whats_new_body` truncation + GitHub footer + no-truncation footer + `[Unreleased]` filter, oversized custom prefix rejection, oversized primer save rejection. Total: 1350 (was 1337).

---

## [0.5.11.0] — 2026-05-04

### Added

- **5'-add-on workbench in `PrimerEditModal`.** New "Add 5':" row sits between the primer-sequence textbox and the live preview: a curated dropdown of 17 common cloning enzymes (EcoRI, BamHI, HindIII, XhoI, SacI, KpnI, SalI, PstI, NotI, SpeI, XbaI, NcoI, NdeI + Type IIS BsaI/BsmBI/BbsI/SapI), a free-form custom-bases Input (DNA/IUPAC validation), and an `+ Apply` button that prepends the chosen prefix to the primer sequence. Custom prefix takes precedence when non-empty so the user can combine the dropdown's `(none)` with a typed value.
- **Live primer preview** inside the modal — a 4-row mini-rendering of the primer's flap + bound bar aligned to the template binding site, mirroring the seq-panel visualisation. Repaints on every keystroke in the sequence textbox so users see the bound region grow/shrink as they type or apply prefixes. Out-of-template flap bases (5' overhang dangling past a linear plasmid's end) are padded with spaces so the layout stays aligned.
- **What's New modal (`WhatsNewModal`).** Auto-pushed once per version after the splash dismisses; stays quiet on subsequent launches at the same version. Available any time from `File → What's New…` for users who want to re-read the release notes. Body is built from `CHANGELOG.md` at open time, sorted newest-version-first by parsed SemVer components. Scrollable; closes on Escape, `q`, or the Dismiss button. The header reserves space for per-release contributor credits — feature requests and code contributions get visible recognition without users having to dig through git log.
- New `last_seen_version` setting, persisted in `settings.json`, drives the auto-trigger.

### Tests

- +5 tests covering the 5'-add-on workflow: `_build_primer_preview` for fwd / rev / wrap-unsupported scenarios, the EcoRI dropdown round-trip, custom prefix DNA/IUPAC validation.
- +6 tests for the What's New modal: CHANGELOG section parser round-trip, version sort key, body composition orders newest-first, auto-push fires after splash on version change, auto-push skipped when version already seen, modal-boundary check at the 160×48 baseline.

---

## [0.5.10.0] — 2026-05-04

### Added

- **Primer-specific editor (`PrimerEditModal`).** Opens when a `primer_bind` feature is activated via Enter on the sidebar, double-click on a sidebar row, or Enter on the seq panel with a primer selected. Read-only by default like `FeatureEditModal`; `Edit` button unlocks the form. Edit fields: name, full primer sequence (5'→3'), strand, notes. Live stats line updates as the user types: length / GC% / Tm (via primer3, lazy-imported). Save round-trips through the `/primer_seq` qualifier so the seq-panel re-renders the bound + flap visualisation with the new bases. Position is intentionally NOT editable from this modal — relocation goes through delete + re-add (same trade-off as `FeatureEditModal`).
- **Type-aware dispatch in `_open_feature_editor`.** Primer features land in `PrimerEditModal`; other features land in `FeatureEditModal`. Each path opens for the EXACT `idx` passed in — so when a user clicks one feature out of an overlapping stack, the editor opens for THAT feature, not for any feature it shares column-space with. Verified via three tests covering primer dispatch, non-primer dispatch fallback, and the no-leak invariant on identically-positioned overlapping CDSs.

### Tests

- +5 tests in `test_smoke.py`: primer-modal dispatch on a `primer_bind` feature, fallback dispatch on a non-primer feature, idx-specific opening on overlapping CDSs (no leak), end-to-end primer save round-trip through the `/primer_seq` qualifier, plus `PrimerEditModal` boundary check at the 160×48 baseline terminal.

---

## [0.5.9.1] — 2026-05-04

### Fixed

- **Wrap-primer bound bases overflowed past the half's column range.** A primer whose bound region crossed the origin (e.g. `start=95, end=5` on a 100-bp plasmid) got split by `_feats_in_chunk` into a tail half + head half, but `_paint_primer_bound_bar` wrote ALL of the bound's bases starting at each half's left edge — so a 10-bp wrap primer painted `AAAAA▶AAAA` (10 chars) into the head half's 5-cell window. Now the painter inspects `_orig_start` / `_orig_end` (stamped by `_feats_in_chunk` on each half) and slices `_primer_seq[flap_len:]` so the tail half holds the FIRST bases and the head half holds the LAST. Arrow suppression is symmetric: only the half owning the primer's 3' end paints the arrow (head for fwd, tail for rev).
- **Full-binding primers (primer_seq present, no flap) now show their bases inline** with the strand instead of falling back to the legacy `▒▒▒▒` block fill. `_primer_seq` and `_bound_len` are stamped on every `primer_bind` feature whose qualifier is set, regardless of flap presence; the renderer dispatches to the bases-inline painter on the bound row when `_primer_seq` is available, and uses the floating-flap row only when `_flap_bases` is also set. Full-binding primers keep their name label on the row above the bar.

### Tests

- +2 regression tests in `test_smoke.py`: `test_wrap_primer_bound_bases_dont_overflow` exercises the head-half slicing directly with a synthesised wrap primer; `test_full_binding_primer_renders_bases_inline` verifies the bases-in-bar painter fires for a primer with `_primer_seq` but no flap.

---

## [0.5.9.0] — 2026-05-04

### Added

- **Partial-binding primer visualisation in the seq panel.** Primers added to the map from the library now carry the original 5'→3' sequence as a `/primer_seq` qualifier. When the primer is longer than its bound region (typical for cloning primers with restriction-site / Gibson-overhang 5' tails), the seq-panel renders the **bound bases** inline with the strand and the **flap bases** on a floating segment one row farther from the strand. The bar's primer-color background spans both rows so the eye reads them as one continuous primer; the flap is offset horizontally so it never vertically overlaps the bound region. Forward-primer flap floats UP-and-LEFT; reverse-primer flap floats DOWN-and-RIGHT (mirror geometry).
- **Hybridization parameter `min_primer_binding`** (default 15 bp) added to `settings.json` — minimum contiguous binding length below which a primer is flagged as weak. Range-checked at hydrate (1-60 bp inclusive); a hand-edited settings entry that smuggles a non-int / out-of-range value falls back to 15. Surfacing in the Settings menu + a per-primer warning glyph follow in the next iteration; the field is wired through now so the data model is in place.
- Rendering: bound bar uses 9 cells per 8 bp (was 8 — the arrow now takes its own extra cell beyond the bound region's `[start, end)` range so every bound base stays visible). The arrow extends one cell past the bound bar (col `end` for fwd, col `start - 1` for rev). Other features sharing the arrow's column won't collide visually because the arrow paints last in the row.

### Tests

- +3 tests in `test_smoke.py`: forward/reverse primer flap data computed correctly on the parsed feat dict (`_flap_bases`, `_flap_start`, `_flap_end`, `_flap_len`, `_bound_len`); full-binding primers (no flap) skip the extra fields entirely; end-to-end seq-panel render contains the flap bases at the expected columns.

---

## [0.5.8.1] — 2026-05-04

### Added

- **Entry-vector banner on `DomesticatorModal`.** The vector chosen for the active grammar (set in Grammar editor → Entry vector) is now also surfaced at the top of the Domesticator with a `Change…` button — mirrors the Constructor banner so users designing L0 parts can confirm at-a-glance which destination plasmid the part will land in. Auto-refreshes when the cloning grammar changes via the existing dropdown so a switch from `gb_l0` (FFE 1) to `moclo_plant` (pAGM4673) updates the banner without leaving the modal.

---

## [0.5.8.0] — 2026-05-04

### Added

- **Per-grammar entry-vector assignment.** Each cloning grammar (Golden Braid L0, MoClo, custom) can now have a canonical destination plasmid (e.g. pUPD2 for GB L0, pAGM4673 for MoClo L1). Set via the **Grammar editor** (a new "Entry vector" row near the top with `Pick from library…` / `Open file…` / `Clear` buttons) and surfaced as a banner at the top of the **Constructor modal** (`Entry vector: <name> (<size> bp)  [Change…]`). The Change button on the constructor jumps directly into the grammar editor for the active grammar.
- **Storage**: new `entry_vectors.json` file (envelope schema v1, atomic save via `_safe_save_json` like every other persisted library). Each entry embeds the full GenBank text rather than a library-id reference so the vector survives library renames / deletes. Schema: `{grammar_id, name, size, source ("library:<id>" | "file:<path>"), gb_text}`. Editable for built-in grammars too — entry vector is grammar-scoped meta, not part of the canonical (immutable) grammar definition.
- New helpers: `_load_entry_vectors`, `_save_entry_vectors`, `_get_entry_vector(grammar_id)`, `_set_entry_vector(grammar_id, vector | None)`. Type-strict (non-string grammar_id silently rejected, mirroring the `_sanitize_*` family). Hooked into `_check_data_files` for startup corruption detection + .bak recovery.

### Changed

- **`FeatureEditModal` layout fix.** Replaced the `height: 90%` rule that always inflated the dialog to ~43 rows (leaving a big vertical gap below the form) with `height: auto; max-height: 38`. The dialog now hugs its content, while the sequence + notes textboxes carry their own scrollbars (sized at 4 rows each — the modal-boundary check enforces fit on a 48-row terminal). Inline position row (`Position: 100..400 (300 bp)`), `border_title` on the sequence + notes widgets to drop the redundant external labels, body wrapped in a `ScrollableContainer` only as a fallback so tiny terminals stay reachable.

### Tests

- +4 entry-vector tests in `test_smoke.py`: `_set_entry_vector` round-trip with multi-grammar separation, type-strict rejection of bad `grammar_id`, the Grammar editor's entry-vector row renders + is enabled even on built-ins, and `_commit_entry_vector` persists through `_get_entry_vector`.

---

## [0.5.7.0] — 2026-05-04

### Performance

Three render-path optimizations targeting the user-perceived sluggishness in fullscreen panel modes (F1-F5) and modal dialogs on older WSL2 hardware:

- **Skip redundant `Static.update()` in `SequencePanel._refresh_view`** — when the cache key is unchanged, both the rebuild AND the push are now skipped. Previously the push fired on every call, costing ~18 ms per redundant repaint on a 45-row fullscreen seq panel because Textual's `Static.update` triggers a full layout pass even with an unchanged renderable. Tracked separately as `_pushed_view_key` so cache invalidations (e.g. connector toggle, restriction overlay change) still force the next call to repush.
- **Memoize `textual.style.Style.from_rich_style`** — profiling on a 5 kb plasmid in fullscreen seq mode showed 87 % of every cursor-move's wall-clock time inside Textual's Rich-Text-to-Content conversion, dominated by 912 `from_rich_style` calls per repaint. The conversion is pure within a single app run (Rich `Style` is frozen-hashable; `console` is the singleton App console), so memoizing on the Rich Style hash with a 2048-entry LRU saves ~2 ms per cursor move and ~30 ms per cold PlasmidMap render. Patched at module-import time near the `textual.style` import. As a happy side-effect the test suite itself runs ~30 % faster (rendering shows up across many widget tests).
- **`ColorPickerModal` xterm grid: 256 Buttons → 1 Static (`_XtermColorGrid`).** The picker used to mount 256 individual cell Buttons + iterate them in `on_mount` to set per-cell `styles.background`; on a T480s baseline that pushed the modal-open latency to ~2 s. Replaced with a single `Static` subclass that renders the entire 256-color grid as one Rich Text canvas (3 spaces per cell with a coloured background) and hit-tests clicks via integer math against the widget's region — three orders of magnitude fewer widgets, modal-open latency drops to ~490 ms (4.1× faster). Drag-preview behaviour is unchanged: `on_mouse_down` / `on_mouse_move` still drive the live preview, just routing through the new `cell_at(x, y)` helper instead of a `get_widget_at` widget-tree lookup.

Measured on a T480s baseline plasmid (5 kb, 80 features), 50 sustained cursor moves:

| Mode | Before | After | Speedup |
|---|---|---|---|
| Multi-panel (F5) cursor move | 2.91 ms | 1.39 ms | **2.1×** |
| Seq-panel only (F4) cursor move | 11.44 ms | 3.53 ms | **3.2×** |
| PlasmidMap cold render (F2 fullscreen) | 39 ms | 8 ms | **4.8×** |
| Seq panel warm `_refresh_view` (no input change) | 18 ms | 0.1 ms | **180×** |
| ColorPickerModal push + settle | 2025 ms | 491 ms | **4.1×** |
| Test suite wall time (no functional change) | 256 s | 183 s | 1.4× |

The 180× win on warm `_refresh_view` is what makes holding an arrow key feel snappy in fullscreen seq mode — every keystroke triggered the redundant `Static.update` cycle even when the cache was warm. The cumulative effect across selections, scroll, hover, and resize is a noticeably less choppy app on slower hardware.

### Hardening

- **`/note` qualifier sanitization (`_sanitize_note`).** New helper strips `\x00..\x08`, `\x0b..\x1f`, and DEL (preserving `\t` and `\n` so multi-paragraph Markdown round-trips), caps at 8 KB, and is type-strict (a JSON dict / int payload becomes empty rather than `str()`-coerced — same convention as `_sanitize_label`). Wired into `FeatureEditModal._on_save` so user-typed notes can't smuggle ANSI escape sequences into the `.gb` export, and into `_apply_feature_edit` as defence-in-depth for non-modal call paths (tests, future agent-API endpoint).
- **Defence-in-depth on the read path.** `_open_feature_editor` now also runs sanitization when extracting notes + sequence from a freshly-opened SeqRecord. A malicious `.gb` whose `/note` qualifier or sequence body carries terminal-escape bytes can no longer reach the modal's Markdown widget or read-only sequence TextArea unfiltered. `Bio.Seq` doesn't enforce a DNA alphabet, so the same `_CONTROL_CHARS_RE` filter applied to labels now scrubs the sequence display too. +3 regression tests cover the sanitizer behaviour, the read-path notes path, and the read-path sequence path.

---

## [0.5.6.0] — 2026-05-04

### Added

- **Feature editor modal (`FeatureEditModal`).** Opens read-only by default — every input (label, type, strand, color, notes) is `disabled` so a stray click can't mutate the record. The user presses **Edit** to unlock the form, then **Save** to commit (or **Cancel** to discard). Position is shown but never editable from this modal — wrap-feature invariants (CLAUDE.md sacred #5/#8/#9) make that path significantly more involved than the safe label/type/strand/color/notes edits, so position changes still flow through delete + re-add.
- **Sequence box + notes box.** The body now includes:
  - A **read-only sequence TextArea** (8-row scrollable) showing the feature's 5'→3' bases, wrap-aware so a feature spanning the origin renders its tail + head as a contiguous string (extracted from the SeqRecord at open time, not stored on the modal).
  - A **notes / references field** that round-trips through the standard GenBank `/note` qualifier. Renders as a Markdown widget in view mode (clickable URLs and `[text](url)` links) and swaps to a TextArea in edit mode for raw Markdown editing. Multi-paragraph notes (separated by blank lines) are split into one `/note` qualifier per paragraph so they round-trip cleanly through `.gb` files via SeqIO.
- **Three triggers** all funnel through the same `PlasmidApp._open_feature_editor(idx)`:
  1. **Enter** on a row in the feature sidebar (priority binding pre-empts `DataTable.action_select_cursor` so it actually fires on a resting cursor).
  2. **Double-click** on a sidebar row (`Click.chain >= 2` captured in `_on_table_click` and consumed in `_row_selected`, which then posts `RowOpened` instead of the highlight-only `RowActivated`).
  3. **Enter** while the SequencePanel has focus and the plasmid map has a selected feature.
- **Layout fix.** Title docks top, action buttons (`Edit` / `Save` / `Cancel`) and status row dock bottom — both **always visible** regardless of body height. The form fields, sequence box, and notes box live in a `1fr` ScrollableContainer in between, so dense content scrolls inside the dialog instead of pushing the buttons off-screen. Resolves the "buttons out of viewport" bug from the prior layout, where `height: auto` on the dialog combined with `max-height: 28` on the body let the modal grow past the visible area on certain terminal sizes.
- Save flow mirrors the agent-API `_h_update_feature` endpoint so the UI and the API can't drift — both rebuild the SeqRecord via `deepcopy` + per-feature mutation, push undo, and refresh all panels. Color persists via the de-facto-standard `ApEinfo_fwdcolor` / `ApEinfo_revcolor` qualifiers (used by Benchling and the popular commercial plasmid editor); notes via the standard GenBank `/note` qualifier.

### Tests

- +1 modal-boundary case for `FeatureEditModal` to verify it fits in the 160×48 baseline terminal (now exercising the new sequence + notes args).
- +10 tests in `test_smoke.py`: opens-read-only, Edit-unlocks-the-form, Save-applies-edits (label change round-trips through the SeqFeature qualifiers), Cancel-discards-edits, sequence-box-shows-feature-bases, wrap-feature sequence assembles tail + head, notes round-trip through `/note` qualifier (multi-paragraph splits), seq-panel Enter on a selected feature opens the modal, seq-panel Enter without a selection no-ops, sidebar `action_open_feature_at_cursor` opens the modal.

---

## [0.5.5.3] — 2026-05-04

### Added

- **Natural-order sort in the plasmid library and collections list.** Plasmids named `pBin1`, `pBin2`, …, `pBin10`, `pBin20` now display in human-readable numeric order instead of the lexicographic `pBin1, pBin10, pBin2, pBin20`. New `_natural_sort_key` helper splits each name into alternating text + integer runs and uses `(0, str)` / `(1, int)` discriminator tuples so mixed-prefix names (`5kb_backbone` vs `pBin1`) compare without raising on cross-type tuple comparison. Applied to both `_repopulate_plasmids` and `_repopulate_collections`.

### Performance

- **Cold-launch import time cut by ~33 %** (≈ 180 ms saved on a T480s baseline). `Markdown` is no longer imported eagerly at the top of `splicecraft.py` — it's lazy-imported inside `HelpModal.compose` and `AlignmentScreen.compose`, the only two places that use it. The eager import was pulling `markdown_it`, `pygments`, and `rich.markdown` (~125 ms cumulative dependency cost), penalising every `splicecraft` invocation even though the help modal opens only on `?` and the alignment viewer is rare. Profiled top-level cumulative import time dropped from ~558 ms to ~327 ms; first user-visible splash frame correspondingly comes up sooner.

### Audit findings (no-action)

- `Bio.SeqIO` (~241 ms): already lazy-imported inside fetch / open / save helpers — paid only when the user actually opens a file. No further win available.
- `pyhmmer` (~53 ms), `primer3` (~9 ms): already lazy.
- `http.server` + `socketserver` (~24 ms cumulative): used only when the agent-API is opted in via `--agent-api`. Marginal win, would require restructuring the `_AgentRequestHandler` / `_AgentAPIServer` classes — deferred until the saving justifies the surgery.
- Splash screen `_compose_splash` is ~10 ms per frame; animation runs at 25 FPS. Not on the critical path to first-paint.

### Tests

- +5 tests in `test_smoke.py` covering `_natural_sort_key` directly (numeric ordering, mixed prefix, no-digits fallback, digit-prefix mixed types) and an end-to-end check that the LibraryPanel DataTable lists plasmids in natural order after they're added in random order.
- Updated `test_delete_collection_via_panel` to look up the row by collection name instead of relying on insertion order — collections now sort alphabetically.

---

## [0.5.5.2] — 2026-05-04

### Fixed

- **Panel focus shortcuts moved from Alt+N to F1-F5.** Alt+digit was eaten by Windows Terminal / iTerm2 / GNOME Terminal for tab-switching before reaching the app — same root cause as the Ctrl+digit failure in 0.5.5.0 (terminals intercept the keystroke before Textual sees it). Settled on `F1`-`F5`: function keys send dedicated CSI/SS3 sequences that no terminal hijacks. HelpModal + toast hints updated; the `pilot.press` regression test now drives F-keys.

---

## [0.5.5.1] — 2026-05-04

### Fixed

- **Panel focus shortcuts moved from Ctrl+N to Alt+N.** Most terminals don't emit a distinct byte sequence for `Ctrl+1` / `Ctrl+3` / `Ctrl+4` / `Ctrl+5` — only `Ctrl+@` / `A-Z` / `[ \ ] ^ _ ?` get unique control bytes, so Ctrl+digit reaches the app as a bare digit and the binding silently never fires. Swapped to `Alt+1` … `Alt+5` (sends `ESC <digit>` cross-terminal reliably). HelpModal updated; toast hint updated. Added an end-to-end `pilot.press("alt+N")` regression test so the keystroke→action wire is enforced, not just the action methods.

---

## [0.5.5.0] — 2026-05-04

### Added

- **Linear viewport "flag" layout (Settings → Linear layout).** Alternative to the default centered layout: features stack into greedy first-fit lanes ABOVE (forward) and BELOW (reverse) a thin rail, each with a single-column stem (`│`) connecting feature-midpoint to rail. Forward heads use `▶`, reverse `◀`. Designed for densely-annotated regions where the centered layout's shared 2-row strip causes overlap. Toggleable via Settings menu and persisted to `settings.json` as `linear_layout` (`"centered"` default | `"flag"`). The two layouts are interchangeable — same zoom + pan + click-target conventions; click hit-testing audited on both layouts.
- **Panel focus mode (Ctrl+1 … Ctrl+5).** Collapse the 4-panel layout down to a single panel for focused work: `Ctrl+1` library only, `Ctrl+2` plasmid map only, `Ctrl+3` feature list only, `Ctrl+4` sequence panel only, `Ctrl+5` restores the multi-panel layout. The remaining panel fills the freed space — Library / Sidebar widths and the SequencePanel height are overridden to `1fr` (and snapshotted in `_panel_dims` so Ctrl+5 puts them back). `self.refresh(layout=True)` after each transition so live terminals see the swap immediately. All five bindings are `priority=True` so they fire even when an inner Input or DataTable holds focus; `check_action` blocks them on modal screens. Documented in the `?` Help modal under "Layout".

### Tests

- +4 tests for the flag-layout in `test_smoke.py`: glyphs, default-is-centered, `action_toggle_linear_layout` round-trip + settings persistence, and overlapping-feature multi-lane packing.
- +7 tests for panel focus mode covering each `action_focus_panel_*` action, the seq-panel "hide top-row" path with explicit height-fills-screen check, the restore-everything path including the seq-panel height roundtrip, and a chained `Ctrl+1 → Ctrl+2 → Ctrl+3 → Ctrl+5` to verify the snapshot logic. Cumulative: 1,291 tests.

---

## [0.5.4.0] — 2026-05-03

### Added

- **Linear plasmid-map redesign — single-lane, backbone-centered.** Features now render in one strip that runs through the middle of the backbone line: 2-row arrows with corner-triangle heads (`◥/◢` forward, `◤/◣` reverse) sitting astride the backbone. Forward and reverse share the same row pair; direction is encoded purely by which end the arrowhead lands on. Cleaner at a glance, and lets the eye scan a slice without hopping lanes.
- **Linear-view zoom + pan.** New `_linear_zoom` and `_linear_offset_bp` reactives on `PlasmidMap`. `+`/`=` zoom in 1.5×, `-` zoom out, `0` reset, `[`/`]`/`←`/`→` pan in linear mode (preserving rotate semantics in circular). The renderer always paints only the **visible bp range** — naturally implements a fog-of-war for large records.
- **Auto-fog for large records.** Plasmids longer than `_LINEAR_LARGE_BP = 100,000` open with the linear viewport zoomed in to a `~50,000 bp` window, so the user gets a readable slice instead of an unreadable strip. User can `0` to reset or `-` to zoom back out.
- **Lazy chunk rendering in the SequencePanel.** `_build_seq_text` now accepts a `viewport_y_range` and emits blank-line placeholders for chunks outside the visible scroll window. The outer `_view_cache_key` includes a quantized viewport tuple, and `SequencePanel.on_mount` watches the inner ScrollableContainer's `scroll_y` to fire a refresh when the user crosses a chunk boundary. **Result: 5 Mb chromosome first-render drops from ~30 s to ~50 ms; cursor refreshes on a 100 kb plasmid drop ~100×.**
- **Restriction-site scan cache.** `_scan_restriction_sites` is now a thin wrapper over `_scan_restriction_sites_impl`, memoising results in a 4-entry LRU `_RESTR_SCAN_CACHE` keyed on `(id(seq), min_recognition_len, unique_only, circular)`. **Result: `r`-toggle on a 5 Mb record drops from ~3 s to ~5 µs after the first scan.** Auto-invalidates on edits since `_rebuild_record_with_edit` allocates a fresh SeqRecord.
- **Sorted-by-start feature index.** `PlasmidMap._feats_by_start` is built in `load_record`; the linear renderer uses bisect to find the upper bound of visible features and walks only those, instead of iterating every feature. Negligible cost on small plasmids; decisive on multi-thousand-feature WGS contigs.
- **`LargeFileConfirmModal`.** A `File → Open` on a `>5 MB` file pushes a confirm modal with **No focused by default** and `Yes, load` styled as a warning. Threshold respects `_LARGE_LOAD_DISK_BYTES` (5 MB on disk) and `_LARGE_LOAD_SEQ_BP` (200 kb parsed). Replaces the prior two-click inline warning so a stray Enter bails out of an accidental large-file load instead of committing to it.
- **Plasmid-load topology default.** Records carrying `topology=linear` (PCR products, sequencing fragments, mitochondrial linear DNA) open in linear view; everything else defaults to circular. `map_mode` is no longer persisted across sessions — every plasmid load re-derives the default from the record itself.
- **Bulk-import progress bar.** `NewCollectionModal` now runs the import in a `@work(thread=True)` worker with a determinate `ProgressBar` and per-file ticker (`ok  filename.gbk  (37/47)` / `FAIL  …`). UI stays responsive even on a 500-plasmid archive. Cached on the modal instance so the caller's `_picked` callback skips the foreground re-import.

### Hardening

- **Clean shutdown.** `main()`'s `finally` block now also catches `KeyboardInterrupt`, cancels pending Textual timers, and explicitly calls `logging.shutdown()` so rotating-file log handlers flush before process exit. The agent-API HTTP server already shut down via `_stop_agent_api` (and removed its token file); `KeyboardInterrupt` no longer dumps a stack trace on its way out.

### Changed

- **`PlasmidMap.on_click` and friends use smallest-enclosing feature** for both circular and linear paths (already in 0.5.3.0; reaffirmed by the linear redesign).
- **HelpModal** documents the new linear-view zoom + pan keys (`+`/`-`/`0`).

### Tests

- +12 new tests across `test_smoke.py` and `test_modal_boundaries.py`: linear corner-triangle render, zoom in/out, pan-clamping, auto-fog target window, zoom-no-op-in-circular, topology-driven default view, restriction-scan cache identity / separation / LRU eviction, sorted-by-start index, lazy chunk rendering speed budget, `LargeFileConfirmModal` boundary check. Cumulative: 1,280 tests.

---

## [0.5.3.0] — 2026-05-03

### Added

- **Plasmidsaurus alignment skeleton** — `File → Align sequencing run (Plasmidsaurus .zip)…` opens a directory-tree picker that highlights `.zip` archives lime-green; click one to list every `.gbk` / `.gb` / `.genbank` member inside, pick a target plasmid from the active collection, click Align. A full-screen `AlignmentScreen` shows the pairwise result: identity %, score, mismatch / gap counts, parallel target / query rows with mismatches in red, gaps as `─`, and a feature-annotation lane between the strands so it's immediately obvious whether a mismatch lands inside a CDS. Helpers (`_list_gbk_members_in_zip`, `_extract_gbk_member`, `_pairwise_align`) are size-capped (500 MB zip / 50 MB member / 200 kb per-side alignment) and reusable from the agent API or future Plasmidsaurus-account API tab.
- **Settings menu** — new tab in the menu bar between File and Edit. Currently surfaces "Show feature hover tooltips" and "Click debug echo (Alt+M)" as boolean toggles; designed for easy expansion (append a `(label, action)` tuple in `open_menu`'s `Settings` entry).
- **Persistent user preferences** — `show_feature_tooltips`, `click_debug`, `show_restr`, `restr_unique_only`, `restr_min_len`, `show_connectors`, and `map_mode` now persist across sessions via `settings.json`. Each `action_toggle_*` writes through `_set_setting`; `PlasmidApp.compose()` hydrates the in-memory mirror at startup. Defensive: `restr_min_len` falls back to 6 if a hand-edited settings.json carries a non-(4|6) value.
- **Hover tooltips on feature bars + labels** — both plasmid map and sequence-panel lane art surface a `Type Label / start..end bp (strand) · length bp / [optional /note or /product]` popup on hover. Wrap-aware (shows `951..1000, 1..50` style for origin-spanning features). Toggle off via the Settings menu. Skipped during drag so selection gestures don't flicker.
- **Shift / Ctrl + click feature → extend selection** — works on the plasmid map, sequence-panel lanes, and sidebar rows. Anchor stays put across chained extensions (click A, ctrl-click B, ctrl-click C → A through C, not B through C). Smallest-enclosing feature wins on nested clicks so an inner annotation anchors at its own start, not the surrounding CDS's. **Ctrl is offered as a synonym for Shift** because many terminals (xterm, macOS Terminal, GNOME Terminal) intercept Shift+click for native text-selection so the click never reaches the app — Ctrl+click is the reliable cross-terminal default. Documented in the help modal.
- **Click-debug toast** (`Alt+M`) — every click in the map / seq-panel / sidebar posts a notification echoing the modifier state (`shift=False  ctrl=True`), so users on terminals that swallow Shift+click can confirm what arrives. After 4 modifierless clicks the modal surfaces a one-time hint pointing at Ctrl+click.
- **Linear plasmid map redesign** — features now render as 2-row cell-based block bars with corner-triangle arrowheads (`◥/◢` forward, `◤/◣` reverse) instead of the old single-row braille arrows, mirroring the sequence-panel's per-feature footprint. Single-column features still render visibly (arrowhead-only). Restriction sites moved to the gap row adjacent to the backbone via cell glyphs (`─`) so they no longer collide with lane-0 feature rows.

### Fixed

- **Shift+Arrow on a feature-clicked selection collapsed to ~half the feature.** Pre-fix, the cursor was at the click bp (often mid-feature) and `_sel_anchor` was at the feature's 5' end, so the first Shift+Arrow computed `(min(anchor, cursor+1), max(anchor, cursor+1)+1)` — selection collapsed to anchor … one-past-cursor. Now the cursor snaps to the **free end** (opposite the anchor) before stepping by 1 bp, matching every text editor's selection-extend convention.
- **Nested-feature clicks resolved to the wrong feature.** Both `PlasmidMap._feat_at` (circular) and `_feat_at_linear` returned the first feature whose bp range contained the click; clicking an inner annotation routed to the surrounding outer feature, anchoring shift+click extends from the wrong span. Both now return the smallest-enclosing feature, mirroring the sequence-panel's existing fallback.

### Tests

- +20 new tests across `test_smoke.py` and `test_modal_boundaries.py`: pairwise-align engine + edge cases, zip ingestion + size-cap protection, persistence hydrate / fall-back, Plasmidsaurus modal flow with directory-tree selection, shift+arrow boundary fix, ctrl-as-synonym, click-debug toggle, hover-tooltip format / wrap / persistence, settings tab presence + position, linear-view corner-triangle render. Cumulative: 1,264 tests.

---

## [0.5.2.0] — 2026-05-03

### Added

- **Bulk import (GenBank or popular commercial plasmid editor format)** — clicking `+` on the LibraryPanel collections view opens a redesigned `NewCollectionModal` with an embedded `DirectoryTree`; pick a folder, click "Create", and every `.dna` / `.gb` / `.gbk` / `.genbank` file inside is loaded into a fresh collection. Per-file failures isolated; notify summary calls out counts. Designed so an archive from the popular commercial plasmid editor migrates in one shot.
- **Headless bulk-import CLI** — `scripts/bulk_import.py` is a thin wrapper around the same `_bulk_import_folder` core for very large archives / CI / automation.
- **Min-size guard on launch** — `main()` checks `shutil.get_terminal_size()` before `app.run()`; below 100×30 SpliceCraft prints a friendly resize-and-retry message and exits with code 2 rather than rendering a clipped UI.
- **Agent-API parity** — eight new endpoints so external CLI agents can drive every flow the GUI offers:
  - `add-current-to-library` (Ctrl+Shift+A equivalent)
  - `create-collection` / `delete-collection` / `rename-collection` / `set-active-collection`
  - `bulk-import-folder` (server-side folder import into a target collection)
  - `blast` (BLASTN / BLASTP against the user's collections; mirrors the GUI BlastModal)
  - `hmmscan` (HMMER 3 profile scan via pyhmmer)

### Hardening

- **Token-comparison timing oracle closed** — `_AgentRequestHandler._check_token` now uses `secrets.compare_digest` instead of `==`, eliminating the per-byte timing leak that a local-process attacker could have exploited to recover the bearer token byte-by-byte.
- **Token-file create race closed** — `_start_agent_api` now writes the token via `os.open(..., O_CREAT | O_EXCL, 0o600)` to a `.tmp` and `os.replace`s it into place, so the token file is mode 0600 from creation. The prior `write_text` + `chmod` sequence left the file briefly readable under the default umask (0644).
- **Type-strict sanitisers** — `_sanitize_label` / `_sanitize_feat_type` / `_sanitize_accession` / `_sanitize_path` now reject non-string payload values (dict, list, int, None) instead of silently coercing via `str()`. A JSON `{"name": {"x": 1}}` to `create-collection` no longer becomes a collection literally named `"{'x': 1}"`; it returns 400.
- **Numeric overflow on float `Infinity` / `NaN` closed** — new `_coerce_int` helper rejects `float('inf')` and `float('nan')` with a clean 400, replacing the implicit `OverflowError → 500` path that bit `int(payload["max_hits"])` and equivalents. All existing `int(payload[...])` sites also widened their except-tuple to include `OverflowError`.
- **Dispatcher defends against non-dict bodies** — `_AgentRequestHandler._handle` normalises any body that isn't a dict (including `None`, lists, scalars) to `{}` before handing off to handlers, removing a class of `AttributeError on .get()` crashes.
- **Bulk-import per-file isolation** — `_bulk_import_folder` catches `OSError` / `PermissionError` on `iterdir`, `is_file`, and `stat` calls; folders that don't exist or can't be read return a single folder-level failure rather than crashing. Per-file size cap (`_BULK_IMPORT_MAX_BYTES = 50 MB`), zero-length-sequence skip, and Biopython `struct.error` rewrap (truncated `.dna` files) all surface as friendly per-file failures.
- **Display-name sanitisation** — `_record_to_library_entry` strips control chars (`\n`, `\t`, NUL) from the source filename and caps display names at `_BULK_IMPORT_MAX_NAME_LEN = 256` chars.
- **Markup-injection prevention** — LibraryPanel cells render via `Text(name)` (opaque to Rich's markup parser); `notify` calls in the bulk-import callback use `markup=False`; the modal "Selected: …" label escapes the path via `rich.markup.escape`. A folder named `[red]EVIL[/red]` in the picker now renders as the literal string instead of injecting style.
- **Modal-input normalisation** — `CollectionNameModal` and `NewCollectionModal` route typed names through the same `_normalize_collection_name` helper the agent API uses (strip control chars, trim, cap length).

### Changed

- **README rewrite** — leads with capability and robustness; new dedicated "Robustness is a feature" section documenting atomic writes, sacred invariants, no-external-blast install, hardened input boundaries, and bulk-import isolation. Maintainer narrative ("actively maintained by a practicing bioengineer who uses it as their primary day-to-day tool") added in the hero block and reinforced in a closing Maintenance section.
- **CLAUDE.md trimmed** from 396 → 89 lines: kept the ten sacred invariants and seventeen pitfalls, dropped per-section subsystem walkthroughs, line-range tables, and per-file test tables (all derivable from the source). Updated stale claims (line count, latest version).
- **conda-recipe** brought current — version bumped from 0.2.2 → 0.5.2.0, dropped pLannotate from the description, added `pyhmmer ≥ 0.12` and `splicecraft-cli` entry point. Recipe README de-personalised (no hardcoded `/home/seb/...` paths).

### Removed

- Stale `screenshot.jpg` (superseded by `splicecraftScreenshot.png`); pyproject sdist include now ships the canonical `splicecraftScreenshot.png` + `splicecraftLogo.png`.
- Dead `# pLannotate integration removed —` comment block in `splicecraft.py` (removal predates 0.4.0; the marker was just clutter).
- Legacy untracked user-data files from the repo root (`parts_bin.json`, `plasmid_library.json` + `.bak`, `primers.json` + `.bak`) — pre-`_DATA_DIR` artifacts; the one-shot migration in `splicecraft.py` already moved equivalents into the user data dir on first run.

### Tests

- **+36 hardening tests** across three sweeps (1,197 → 1,233): `TestBulkImportHardening`, `TestNewCollectionModalFlow`, `TestTokenHardening`, `TestNewLibraryEndpoints`, `TestNewSearchEndpoints`, `TestAdditionalAgentHardening`, `TestTypeStrictSanitisation`, `TestNumericCoercionHardening`, `TestRequestDispatcherHardening`. Every adversarial input class (path traversal attempt, oversized file, empty sequence, control-char filename, markup-bearing filename, JSON `Infinity`/`NaN`, dict-as-string-field, non-dict body) has at least one regression guard.

---

## [0.5.1.2] — 2026-05-01

### Changed

- **HelpModal** (`?` key) now renders via Textual's `Markdown` widget instead of a `Static` with manual `[bold]…[/]` markup. Body is structured as Markdown tables (one per topic group) so users can drag-select a key combo to copy it. Added missing post-0.5.1.0 keybinds (Ctrl+B BLAST, Ctrl+N New Plasmid, Ctrl+A select-all, Ctrl+P primer design, Ctrl+Q quit).

---

## [0.5.1.0] — 2026-05-01

Versioning switched to 4 components (MAJOR.MINOR.PATCH.MICRO) to allow finer-grained micro-releases without burning patch numbers.

### Added

- **BLAST modal (`Ctrl+B`)** — three-tier similarity search against the user's plasmid collections:
  - **BLASTN** (DNA → DNA) and **BLASTP** (protein → protein) default to a `pyhmmer`-backed engine (HMMER 3 in-process via `nhmmer` / `phmmer`); a hand-rolled pure-Python BLAST stays in tree as a fallback for very short queries (< 20 bp DNA / < 6 aa) where HMMER's profile builder won't bite.
  - **HMMscan** reads any HMMER 3 `.hmm` / `.h3m` / `.h3p` profile file directly via `pyhmmer.hmmer.hmmscan`; lazy file read so Pfam-scale (~1 GB) databases don't pre-fetch into RAM.
  - DB build + search run in a `@work(thread=True)` worker so the UI stays responsive on a 50-plasmid index.
  - 4-entry LRU DB cache, auto-invalidated by `_save_collections`.
  - HMM database path persists in `settings.json` across sessions.
- **New Plasmid modal (`Ctrl+N`)** — paste a sequence, optionally name it + set topology, then commit via plain Create / "Annotate from library" (substring match) / "Annotate via BLAST" (BLASTN against all collections; ≥ 90 % identity hits become `misc_feature` annotations).
- **Help modal (`?`)** — full keyboard-shortcut reference; dismisses on any key.
- **`Ctrl+A`** — select the entire plasmid sequence for clipboard copy.
- **`Ctrl+Q`** — Quit (replaces `q`, which is too easy to type by accident).
- **Footer keys**: `f`, `Ctrl+O`, `Ctrl+S`, `Ctrl+N`, `Ctrl+A`, `Ctrl+F`, `Ctrl+P`, `Ctrl+B`, `Ctrl+Q`, `?` show in the bottom row.
- **`pyhmmer ≥ 0.12`** added as a hard runtime dependency (wheels ship HMMER 3 source pre-compiled — no system-package install).

### Changed

- Runtime dep floors bumped: `textual ≥ 8.2.5`, `platformdirs ≥ 4.9`, `pyhmmer ≥ 0.12`. Dev deps: `pytest-xdist ≥ 3.8`, `hypothesis ≥ 6.152`. Verified against the full 1,170-test suite.
- `release.py` runs `pytest -n auto` instead of serial — release flow drops from ~13 min to ~5–7 min total.

### Hardening

- BLAST query sanitisation centralised in `_detect_query_program`: FASTA-header strip (with leading-whitespace tolerance), alphabet filter (BLASTN: IUPAC; BLASTP: 20 AAs + B/Z/X/*), 100 KB length cap with a soft "(query truncated)" warning.
- `_annotate_seq_from_feature_library` capped at 5,000 hits to keep a chromosome paste with a common library entry from blowing up.
- `_blast_search_pure` capped at 200,000 ungapped extensions per search to bound runtime on tandem-repeat queries.
- `rich.markup.escape` on subject names + collection labels in the BLAST results panel — a malicious / odd qualifier with `[red]…[/red]` can't inject styling.
- **Modal-active gate**: `App.on_key` and `App.on_click` early-return when a modal is on top of the screen stack so seq-cursor moves, selection slides, and RE-highlight clears can't fire underneath. `Ctrl+Z` / `Ctrl+Y` stay above the gate as global fallbacks.
- BlastModal re-entrancy guarded by `_busy` so mashing **Run** drops extras instead of queuing.

### Tests

- New `tests/test_blast.py` (49 tests): BLOSUM62 sanity, BLASTN / BLASTP both backends, dispatcher fallback (monkeypatch spies), HMMscan via on-the-fly built `.hmm` fixture, query sanitisation, modal-active gating, HMM-path persistence, markup-injection regression.
- New `tests/test_new_plasmid.py` (17 tests): `_annotate_seq_from_feature_library` + NewPlasmidModal Create / Annotate-from-library / Annotate-via-BLAST flows.
- New `tests/test_integration_realistic.py` (9 tests): exercises the new modals + keybindings against a 2.7 kb synthetic plasmid (`realistic_plasmid` fixture).
- `tests/test_modal_boundaries.py`: HelpModal, NewPlasmidModal, BlastModal added to the per-modal layout regression suite.

---

## [0.5.0] — 2026-05-01

### Added

- **Agent API expansion** (14 new endpoints): `get-sequence`, `replace-sequence`, `delete-feature`, `update-feature`, `get-feature`, `export-genbank`, `export-fasta`, `list-library`, `list-collections`, `delete-from-library`, `list-restriction-sites`, `list-codon-tables`, `optimize-protein`, `load-file` (bypasses the 1 MiB JSON-body cap for chromosome-scale imports). Now covers every GUI action external AI agents need.
- **`Alt+D` debug mode** — toggleable hover-status diagnostic row in the seq panel; shows raw bp-resolution under the cursor for bug-report transcripts.
- **Centralised input sanitisers**: `_sanitize_label`, `_sanitize_feat_type`, `_sanitize_accession`, `_sanitize_path`, `_sanitize_bases` — applied at every user-input boundary (modals, agent-API endpoints, NCBI fetch).
- **Path-traversal + control-char defenses**: feature labels / qualifier values strip control chars; NCBI accessions whitelist-validate; agent-API request bodies cap at 1 MiB by default.

### Changed

- **Codon "harmonization" → "optimization"** rename throughout the UI and code paths. We do frequency-matching codon optimization (Hatfield/Kazusa), not Angov-style harmonization (which requires a source organism's codon-usage table). Old name was confusing.

### Hardening

- Oversized request bodies, malformed payloads, and shell-meta in NCBI accessions are now rejected at the boundary with a clean error rather than reaching internal helpers.

---

## [0.4.8] — 2026-05-01

### Added

- **Hover diagnostic mode** (`Alt+D`) toggles a one-line debug strip in the seq panel showing under-cursor metadata. Off by default, so the strip doesn't eat real estate during normal use.

### Performance / UX

- Sequence-panel render-cache improvements; cleanups around the inline-AA painter.

---

## [0.4.7] — 2026-04-30

### Fixed

- **Click-resolution divergence** — the renderer (`_render_packed_strand`) and the click resolver (`_click_to_bp` / `_hover_at`) sorted features differently, so a click could land on a different feature than what the user saw. Now both paths use the same insertion order — the "click the bar I see, not a different one underneath" invariant is restored.
- **Feature creation visibility** — newly added features auto-highlight their DNA span on creation so users see what landed.
- **Tiny-jiggle absorption** — micro-movements during a click on a feature bar no longer drop into "drag-select" mode.
- **Plasmid-map label clicks** — clicking a feature label routes to the same feature as clicking its arc.
- **AA-row empty-cell click** — clicking an empty cell in the inline-AA row now clears the prior selection rather than no-op'ing.
- **Lane click semantics** — picks the actually-clicked feature, not "smallest at bp" (which surprised users on overlapping bars).
- **CDS divisibility gate** — features whose length isn't a multiple of 3 are no longer rendered as CDS (no AA strip, no nonsensical translation).

### Added

- **Theme + focus visuals** — pinned `splicecraft-black` theme; consistent focus borders.
- **`Home` / `End` / `Ctrl+Arrow` seq-panel keys** — jump to row start / end / coarse step.
- **New-features-stack-on-top packing** — recently added features render above older ones for visibility.
- **Insert-feature button** — annotate a selection range without splicing DNA (label-only).

### Diagnostics

- **`SIGUSR1` stack-dump handler** for hang debugging in the field.
- Mouse-down + slow-path event logging for bug-report transcripts.

---

## [0.4.6] — 2026-04-29

### Added

- **Agent API (initial)** — localhost JSON-over-HTTP surface (`--agent-api` flag) so external AI agents can drive a running SpliceCraft session: status, fetch, load-entry, add-feature, save, plus tools-discovery. Bearer-token auth on write endpoints.
- **Selection prefill on `Ctrl+F`** — opening the Add-Feature modal with an active selection pre-fills the start/end and unlocks the "Insert feature" button.

### Hardening

- Codebase-wide review of error paths; narrow `except` types replace bare `except Exception` in I/O paths; `_log.exception` adopted in workers.

---

## [0.4.5] — 2026-04-30

### Added

- **Inline amino-acid translation in the sequence panel.** Each CDS
  feature now has an extra row of one-letter AA codes drawn at codon
  midpoints, directly above (forward) or below (reverse) its bar. No
  more popping the translation strip in/out — the protein is always
  visible alongside the bases. Wrap-around CDS features (those that
  span the origin) translate correctly across the join.
- **Click an AA letter → highlight that codon's three bases on the
  DNA strand.** Cursor parks at the codon centre; Ctrl+C copies the
  3 bp. Empty cells between AA letters are no-ops by design.
- **Per-strand restriction-cut visualization.** Clicking a sticky
  cutter (EcoRI, HindIII, …) in the lane art now tints the upstream
  bases on each strand blue and the downstream bases red, showing
  the staggered overhang correctly — top and bottom strands carry
  different bg colours over the offset bps.
- **Library search input.** Pre-fills "Search"; clears on focus;
  Enter applies a fuzzy subsequence filter to the visible table
  (collections or plasmids); empty Enter clears the filter and
  restores the prefill.
- **Bottom-strand copy** — Alt+C (and Ctrl+Shift+C as an alias for
  terminals that distinguish it from Ctrl+C) reverse-complements
  the current selection before copying.
- **Enter on the seq cursor** highlights the smallest feature
  enclosing that bp — keyboard equivalent of clicking a feature.
- **Up arrow on the focused map** resets the origin to bp 1
  (keyboard partner to Home).
- **Pure-black UI theme** (`splicecraft-black`) — pinned at startup
  so panels and modals match the logo's true-black backdrop instead
  of textual-dark's near-black greys.
- **Toast notifications carry semantic colour.** Saves/loads/copies
  flash green ("success"); information stays neutral; warnings amber;
  errors red. Notifications fired while the splash is up are queued
  and replayed on dismiss so startup messages aren't lost.

### Changed

- **2D feature-lane packer** replaces the three-tier RE / 1bp /
  multi-bp lane stack. Every feature now sits in lane 0 (adjacent
  to the DNA strand) by default; only bp-range collisions push a
  feature up. Restriction sites participate in the same lanes as
  ordinary features — the parens row prints far from DNA, the cut
  arrow close. Lane depth is uncapped — features pile up as deep
  as the data demands.
- **Layout rework.** The library, plasmid map, and feature sidebar
  share one horizontal top row; the sequence panel sits beneath
  them and spans the full window width. The old per-feature detail
  box in the sidebar was removed (info still surfaces via the row
  + map highlight); the redundant `Sequence` header strip in the
  seq panel is gone too.
- **Map / sidebar feature picks now park the cursor at the feature's
  5' end** rather than its midpoint. Long CDS rows used to land the
  cursor mid-feature; the new behaviour anchors at the feature's
  start so users read top-down.
- **Lane clicks no longer scroll the seq panel.** The user clicked
  something they were already looking at, so jumping the viewport
  away from their cursor would be jarring.
- **Map rotation keys are focus-gated.** `[` / `]` and arrow keys
  rotate only when the plasmid map has focus — they no longer
  fire from modal screens or the seq panel.
- **Arrow keys clear the active RE highlight** and park the cursor
  immediately upstream (Left) or downstream (Right / Up / Down) of
  the top-strand cut.
- **Arrow keys exit a feature highlight** at the matching end and
  step one base in the arrow's direction, instead of being absorbed
  by the highlight.
- **Backbone clicks on the map** (or anywhere outside the four main
  panels) now clear every panel's highlight in one go.
- **Loading a library entry that's already loaded is now a no-op,**
  instead of clobbering undo/redo and any unsaved edits.
- **Performance budgets bumped** for the inline AA row + inter-chunk
  gap (`50 KB cursor ≤ 50 ms`, `150 KB cursor ≤ 120 ms`).

### Fixed

- **Wrap-CDS inline AA painting.** The new AA row was placing letters
  at the wrong bps with the wrong reading frame for any CDS that
  crosses the origin (head halves were translated as if they were
  fresh 0-indexed CDS fragments). `_feats_in_chunk` now stamps the
  original `(start, end)` on each split half as `_orig_start` /
  `_orig_end`, and `_paint_cds_aa` / `_cds_aa_list` / the AA-letter
  click handler all use those for codon math. Regression test in
  `TestWrapCDSInlineTranslation`.
- **`SequencePanel.on_mouse_down` AttributeError on first click.**
  The new lane-click skip-scroll logic read `self._last_lane_click`
  before any prior `on_click` had a chance to initialise it. Now
  set in `__init__` and reset before every `_click_to_bp` call so
  the flag reflects only the current click.

### Removed

- Three vestigial helpers (`_build_chunk_translation`, `_emit_aa_row`,
  `_chunk_has_cds`, ~125 lines) from an earlier AA-row prototype that
  was superseded by `_paint_cds_aa`. No callers, no tests.
- Sequence-panel header strip and translation footer (`#seq-hdr`,
  `#seq-trans`).
- Feature-sidebar detail box (`#detail-box`); `show_detail` is now
  a no-op kept for caller compatibility.

---

## [0.4.4] — 2026-04-29

_Catch-up entry covering features and fixes that accumulated across
the 0.2.x and 0.4.x development arc, prior to the per-release
changelog convention. Listed under 0.4.4 — the last untagged-in-
CHANGELOG release before structured logging began at [0.4.5]._

### Added

- **`.dna` file import (popular commercial plasmid editor format)** —
  `File → Open` and the `o` hotkey now accept the native binary `.dna`
  format via Biopython's built-in parser. No manual GenBank export step
  required. Files are dispatched by extension (`.gb`, `.gbk`,
  `.genbank` → GenBank; `.dna` → binary parser), case-insensitively.
  Malformed `.dna` files produce a user-friendly error pointing to the
  likely cause.

### Fixed

- **Golden Braid primer validation** — `_design_gb_primers` now returns a
  clear error when the selected region is shorter than 18 bp, instead of
  silently producing a too-short primer with `Tm=0.0`. `_run_goldenbraid`
  surfaces that error in red in the results pane.
- **pLannotate race condition** — if the user loaded a different plasmid
  while pLannotate was still running, the worker would silently replace
  the newly-loaded plasmid with the merged old one. The worker now checks
  `self._current_record is record` before applying and drops the stale
  result with a warning.
- **Undo stack leaked across plasmid loads** — pressing `Ctrl+Z` after
  switching plasmids could yank the user back to an unrelated edit on
  the previous plasmid. `_apply_record` now clears undo/redo on a fresh
  load (fetch / file open / library pick). In-place record changes
  (pLannotate merge, sequence edits) keep their undo entries intact.
- **Wrap-around restriction sites** — enzymes whose recognition sequence
  spans the origin of a circular plasmid are now found and rendered as
  two linked pieces (labeled tail + unlabeled head). Previously those
  sites were silently invisible.
- **Zero-width feature click detection** — a malformed feature with
  `start == end` used to match every click on the backbone in linear
  view. The linear click handler now shares `_bp_in`'s half-open
  `[start, end)` semantics, making zero-width features unclickable.
- **Shrink-guard widened** — the data-safety guard now logs any library
  shrink (not just nukes to zero entries), making accidental entry
  deletion easier to audit in `/tmp/splicecraft.log`.

### Added

- **Feature deletion** — press `Delete` to remove the selected feature (annotation only,
  sequence is untouched); fully undo/redo-able with `Ctrl+Z` / `Ctrl+Shift+Z`.

- **Toggleable linear map view** — press `v` to switch the circular map panel between
  circular and horizontal linear views.  Linear view uses the same braille-pixel rendering
  with per-strand feature bars, arrowheads, lane stacking, and feature labels.

- **Strand-aware DNA sequence panel layout** — forward-strand features always appear
  *above* the DNA sequence line; reverse-strand features always appear *below*, making
  strand identity immediately apparent.  Overlapping features on the same strand stack
  into additional lanes on their respective side.

- **Braille feature bars in sequence panel** — annotation bars now use solid braille
  block characters (`⣿`) matching the aesthetic of the map viewer, with `▶`/`◀`
  arrowheads at the true start/end of each feature.

- **Single-bp feature triangles** — features that are one base-pair wide render as `▼`
  (above DNA) or `▲` (below DNA), pointing inward toward the sequence line.

- **Label-above / label-below layout** — feature names appear outside the bar (above the
  bar for forward features, below for reverse), keeping the braille bar itself clean.
  Multiple non-overlapping features share a single horizontal row pair.

- **Feature connector lines** (`l` key toggle) — draws a `┊` connector between each
  feature label and its braille bar in the sequence panel, and a dotted radial leader
  line from the arc to the label in the circular map.  Both panels respond to the same
  toggle.

- **Full NEB restriction enzyme catalog** — ~200 enzymes from New England Biolabs,
  including Type IIS (BsaI, BsmBI, BbsI, …) with non-palindromic cut sites.  Each hit
  is visualized as two distinct overlays:
  - **Recognition sequence bar** (`resite`) — thin braille arc outside the backbone for
    forward-strand hits, inside for reverse-strand hits; same strand-above/below layout
    in the sequence panel.
  - **Cut site marker** (`recut`) — `↓` (forward) or `↑` (reverse) arrow in the
    sequence panel; radial `┼` tick on the circular and linear map at the exact cut
    position.  Type IIS cut sites appear displaced from the recognition sequence as
    expected.
  - Recognition sequence IUPAC codes (R, Y, W, S, M, K, B, D, H, V, N) are handled
    via regex; both strands are scanned.  Enzyme labels appear in the circular map
    alongside regular feature labels using the same proximity placement algorithm.

- **Circular map: inside tick marks** — bp graduation marks and labels now sit *inside*
  the backbone ring rather than outside, keeping the outer ring clean for feature labels.
  Two constants (`TICK_DR_MARK`, `TICK_DR_LABEL`) control the inset depth and scale
  automatically with the `,` / `.` aspect-ratio keys.

- **Circular map: full-length feature labels** — removed the 16-character truncation;
  labels now display their full name.

- **Circular map: proximity label placement** — labels are placed as close to the arc as
  possible, greedy-stepping radially outward only when a label would overlap an
  already-placed one.  `LABEL_DR_MIN` (default `9`) sets the minimum clearance.

- **Default library entry** — MW463917.1 (pACYC184) is fetched and added to the library
  automatically on first launch.  The NCBI fetch dialog pre-fills with this accession.

---

## [0.1.0] — 2026-03-23

### Added

- Initial release: braille-canvas circular plasmid map, NCBI live fetch, local `.gb`
  file loading, persistent plasmid library, feature sidebar with CDS translation,
  sequence panel with click-to-cursor, drag selection, undo/redo, and restriction-site
  overlay.
- ASCII logo and README.
