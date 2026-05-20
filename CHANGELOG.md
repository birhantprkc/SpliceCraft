# SpliceCraft Changelog

> **Archive policy:** entries older than 12 months are rotated into
> `CHANGELOG-archive/YEAR.md` and linked from the bottom of this
> file. Until then, everything stays here for grep-ability. Current
> active window: **2026-03-23 → present** (nothing eligible for
> rotation yet — the project shipped its first release in March 2026).

> **Per-release format:** versions follow `MAJOR.MINOR.PATCH`, ISO
> dates (`YYYY-MM-DD`), and one-line headlines. The release script
> auto-prepends a stub from the commits since the last tag; the
> maintainer hand-fills the body before tagging. See
> `RELEASE_CHECKLIST.md` for the full release workflow.

---

## [0.9.10] — 2026-05-20

_(auto-generated from commits since v0.9.9)_

* Entry-vector UX + lenient TU classifier + sequence integrity audit · invariants #46-#47

---

## [0.9.9] — 2026-05-20

_(auto-generated from commits since v0.9.8)_

* Master Delete · File menu wipe-all-user-data · invariant #46

---

## [0.9.8] — 2026-05-19

_(auto-generated from commits since v0.9.7)_

* Every save is a full plasmid + cross-platform contract · invariants #44–#45

---

## [0.9.7] — 2026-05-19

_(auto-generated from commits since v0.9.6)_

* Adversarial audit sweep #9 · pre-v1.0.0 hardening · 21 HIGH/MEDIUM + 11 deferred

---

## [0.9.6] — 2026-05-19 — Experiments lab notebook · projects layer · gels library

Builds the Experiments toolbar entry from scratch (markdown editor,
image attach, spellcheck) and follows it with a same-day refactor
turning Experiments into a multi-project workspace mirroring the
parts-bin / collections pattern. Promotes saved agarose gels to a
first-class persisted object via `gels.json`. Tag system overhauled
to single-sigil syntax (`@` plasmid, `!` action, `&` gel) with
in-editor coloring and cursor-position click-to-open dispatch.

### Experiments lab notebook (new)

- **`Menu → Experiments`** opens the full-screen `ExperimentsScreen`
  for cloning runs, protocol notes, and observations. Split-pane
  layout: always-visible entries list on the left, `TabbedContent`
  with `Compose` (markdown `TextArea`) and `Attachments` (image
  grid) on the right.
- **Image attach** via `ImageAttachModal` — `DirectoryTree` filtered
  to image extensions. On Win/Mac the modal also exposes a "Paste
  from clipboard" button via `Pillow.ImageGrab.grabclipboard()`; on
  Linux/WSL the button is disabled (no pure-Python clipboard image
  API on those platforms).
- **Spellcheck** via pyspellchecker (bundled English wordlist, no
  network). `F7` or "Spellcheck" button → `SpellcheckModal` lists
  misspellings + suggestions with per-row Replace / Add-to-dict /
  Skip. Custom dictionary persists via the `experiments_custom_dict`
  setting. Code spans, URLs, markdown links, and plasmid xrefs are
  masked before tokenisation so non-prose regions don't pollute the
  list.
- **Persistence**: `experiments.json` envelope-v1 with the full
  four-layer data-safety net (invariant #31). Sized caps: 1 MB body,
  10 MB image, 100 MB per-entry attach dir. Registered with
  `_USER_DATA_FILE_ATTRS` / `_USER_DATA_DIR_ATTRS` so
  `splicecraft update` snapshots them before any install subprocess.

### Experiments projects (refactor)

- **`experiment_projects.json`** holds all named projects, each
  carrying its own `experiments: list[dict]`. Mirrors the parts-bin
  pattern — cache + `_cache_lock` + deepcopy-on-read+save.
- **`Menu → Experiments`** now opens `ExperimentProjectsPickerModal`
  first (1:1 mirror of `PartsBinPickerModal` — Open / New / Rename /
  Duplicate / Delete / Close). Picking a project sets it active and
  pushes `ExperimentsScreen` for that project's entries.
- **First-run migration** wraps existing `experiments.json` entries
  into `_DEFAULT_PROJECT_NAME = "Main Project"`. Called from
  `compose()` (not `on_mount`) per invariant #9.
- **New sacred invariant — Experiments mirror**: every entry save
  MUST go through `_save_experiments`, which calls
  `_sync_active_project_experiments` so `experiment_projects.json`'s
  active-project field stays in lockstep with `experiments.json`.
  Same threat model as invariant #10 (collections mirror).

### Gels library (new)

- **`gels.json`** holds named Simulator gel snapshots. The
  Save / Load / Rename / Delete flow lives in the Gel pane's new
  `Library` button. `GelLibraryModal` is dual-context (Simulator
  save+load OR Experiments tag-insert OR click-to-open
  scroll-to-entry).
- **Schema**: `{id, name, lanes, agarose_pct, notes, created_at,
  updated_at}` with envelope v1. Agarose clamped to 0.3–5.0 %
  (NaN / inf rejected); lane list capped at 20.

### Tag system — single sigils

- **`@<id>`** — plasmid xref (lime `#9AFF80`).
- **`!<id>`** — action xref (purple `#C77FFF`), curated catalog of
  19 actions across Design / PCR / Restriction / Assembly /
  Purification / Biological / Validation buckets.
- **`&<id>`** — gel xref (orange `#FFB347`).
- **In-editor coloring** via `_ExperimentMarkdownTextArea` — subclass
  overrides `_build_highlight_map` to inject regex-based highlights
  into the active theme's `syntax_styles`. ASCII fast-path skips
  per-match UTF-8 encoding; non-ASCII path builds a codepoint→byte
  table once per line.
- **Backspace at tag end** deletes the entire tag instead of one
  char. Mid-tag and prose backspaces fall through to default.
- **Lookbehinds** reject email (`user@example.com`), double-sigil
  (`@@`, `!!`, `&&`), and word-prefix false positives. The
  next-char-must-be-letter rule keeps markdown image syntax
  (`![alt](url)`) safe.
- **Legacy migration**: the pre-2026-05-18 `@plasmid:<id>` /
  `@actions:<id>` format is rewritten to single-sigil on every
  `_load_experiments` call. One-way migration; once a body lands
  back on disk through `_save_experiments`, the old format is gone.

### Click-to-open

- **`Ctrl+G`** scans the cursor's line for any tag spanning the
  cursor column and dispatches to the matching modal.
- **Double-click** in the `TextArea` posts a `TagOpenRequested`
  event that routes to the same handler (single-click cursor
  placement is untouched).
- Plasmid hit → auto-save dirty compose → search every collection
  via `_search_collections_library` → switch active + load via
  `_apply_record`. Gel hit → `GelLibraryModal(initial_gel_id)`
  scrolled to that entry. Action hit → `ActionsPickerModal` scrolled
  to that catalog row. No-tag / unknown id → friendly notify, screen
  stays put.

### Gel renderer polish

- **Lane bands align column-for-column with wells**
  (`line_left.ljust(label_col)`) — pre-fix, labelled rows shifted
  bands one column left.
- **Out-of-window fragments extrapolate via soft-asymptote** so
  multiple sub-resolution bands retain size ordering (pre-fix, two
  below-window fragments hard-clamped to the same row).
- **Sub-row fractional rendering** paints a faint `─` tail on the
  adjacent row for bands whose mobility falls between row centers —
  visible granularity below integer-row resolution.

### Hardening

- **Full-ancestor symlink walk** in `_experiment_attach_dir` (was
  2-level pre-refactor). A symlink at any depth — `_EXPERIMENTS_DIR`
  itself, `_DATA_DIR`, or any ancestor up to root — refuses the
  path.
- **Body-over-cap detection BEFORE save** with user notify (was
  silent truncate).
- **Dedup-by-id save** replaces ALL matches, not just the first —
  defensive against hand-edited JSON.
- **Clipboard tmp file cleanup** — `_EXPERIMENT_CLIP_TMP_PREFIX`
  files are unlinked after the bytes are copied (pre-fix the OS
  tmpdir slowly accumulated orphan PNGs).
- **Unsaved-changes guard** — `ExperimentsScreen.action_cancel` no
  longer silent-saves on Esc/Close when the compose buffer is dirty.
  `ExperimentUnsavedChangesModal` (Save / Abandon / Cancel) pops
  with default-Cancel; save failure keeps the screen alive so the
  user can retry without losing their buffer.

### Hard deps added

- `Pillow>=10.0` — image bytes + Win/Mac clipboard grab.
- `pyspellchecker>=0.8.0` — English wordlist.
- `rich-pixels>=3.0.0` — Unicode half-block image render in any
  terminal (kitty / sixel / iTerm protocols NOT required).
- All three are pure-Python wheels; no system shell-out.

### Tests

- **+260** across new `test_gels.py` + `test_experiment_projects.py`
  plus additions to `test_experiments.py`, `test_simulator.py`, and
  `test_modal_boundaries.py`. **2,841 passing.**

---

## [0.9.5] — 2026-05-18 — Auto-load picks natural-sort-first entry

`PlasmidApp.on_mount` was picking `_load_library()[0]` (insertion
order) on no-arg launch, but `LibraryPanel` sorts entries by
`_natural_sort_key` for display. A user whose first-inserted plasmid
sorted to the bottom of the visible list (e.g. a stray 1 kb `X`
record at the top of insertion order in a library full of `pBin*`
and NCBI-accession entries) would see the canvas load that bottom
plasmid instead of the visually-first one.

Fix: natural-sort the library BEFORE picking `[0]`. Sacred invariant
#33 (display sort and lookup sort must agree) — same class of bug
that landed in 0.7.4.5 for the Constructor / Parts Bin / Primer
pickers. Regression test in
`test_smoke.py::TestLibraryAutoLoadMatchesPanelSort`.

---

## [0.9.4] — 2026-05-18 — Sequencing toolbar · per-plasmid map mode · hardening sweep #8

Reshapes the Sequencing pane from a single-purpose Plasmidsaurus
alignment modal into a full-screen toolbar designed to absorb future
sequencing-ingest sources without rewriting the alignment path.
`map_mode` becomes per-plasmid (was per-app). Sweeps the rest of the
codebase for natural-sort consistency and narrow-terminal CSS
issues. Hardens the Plasmidsaurus zip ingester against a per-base
TSV zip-bomb path.

### Sequencing toolbar

- **`SequencingScreen` replaces `PlasmidsaurusAlignModal`** —
  full-screen with 4 nested sub-tabs (`General` / `Samples` /
  `Quality` / `Align`). `PlasmidsaurusAlignModal` is kept as a
  module-level alias for test + agent-API back-compat.
- **Structured Plasmidsaurus parser** (`_parse_plasmidsaurus_zip`)
  walks the zip and groups files per sample (`gbk`, `fasta`,
  `summary`, `perbase`, `histogram`, `coverage_plot`,
  `interactive_map`, `ab1_files`, summary text, per-base coverage
  stats). Run-level extras (`<run>_gel.png`, `README`) land in
  `run_files`. Summary bodies stream inline so the QC tab parses
  k-mer + contamination without re-opening the zip.
- **Sub-tab gating** runs through `_apply_subtab_gating(enabled)` —
  Samples / Quality / Align panes are disabled until a zip is
  loaded, and `tabs.active` is redirected back to General when
  disabling so the user can't be stranded on a disabled-now-empty
  pane.

### Per-plasmid map mode

- Library entries now carry a `map_mode` field; the active value is
  loaded via `_tui_map_mode` stash + `pm.load_record` honour, and
  persists across reloads via `_persist_map_mode_for_active`.
- **Sequencing-aligned plasmids auto-tag `linear`** so re-opens
  default to the diff-friendly view.

### Restriction scanner

- **Placement key change**: now `(start, end, recognition)` instead
  of `(start, end)`. HF / iso variants of the SAME enzyme still
  collapse, but two genuinely-different enzymes with accidental
  position overlap stay independent on the map.

### No-arg launch defaults

- **Removed the 1 kb synthetic demo preload** from `main()` — the
  demo plasmid used to fire on every empty-canvas launch, confusing
  users into thinking it was one of their saved plasmids. Still in
  source (`_make_demo_record` / `_DEMO_PLASMID_SEQ`) for tests + ad
  hoc dev.
- **Suppressed the first-run NCBI seed** of `MW463917.1` —
  `main()` now sets `_skip_seed = True`. Dev / demo builds wanting
  the historical auto-seed-on-empty-library behaviour can flip
  `_skip_seed = False` before `app.run()`.

### Library UX polish

- **Sticky delete cursor** — `LibraryPanel` captures `cursor_row`
  BEFORE library mutation (`LibraryDeleteConfirmModal` holds focus
  so the row index is still authoritative) and parks the cursor on
  `deleted - 1` after repopulate. Top-row delete clamps to 0;
  last-row delete skips restore cleanly. Locked in by 4 new
  `TestDeleteFocusRouting` cases (regression guard 2026-05-18).
- **Natural-sort sweep across remaining pickers** —
  `_list_gbk_members_in_zip` used case-folded lex sort and the
  Plasmidsaurus + Domesticator modals iterated load order; all
  three now route through `_natural_sort_key` so picker ordering
  matches `LibraryPanel` (`pA1, pA2, pA10` not
  `pA1, pA10, pA2`).

### Simulator CSS — narrow / short terminals

- PCR pane wrapped in `VerticalScroll`; primer + params rows bumped
  to `height: 3` so Input borders render (Textual clips Inputs
  below `height: 3`).
- Gel hint pulled out to its own row so the dot-separator stops
  wrapping.
- Lane `source` Select widened to 24 cols to fit "Plasmid
  (uncut)".
- Left / right gel split moved from fixed 64 to `1fr min 56 max
  72` so the lane config + gel image both adapt to width.

### Hardening sweep #8

- **Per-base TSV zip-bomb defence** —
  `_PLASMIDSAURUS_PERBASE_MAX_BYTES = 100 MB` two-layer cap:
  refuses upfront when central-directory `file_size` overshoots,
  and `_summarize_perbase_tsv` chunked-reads (64 KB) via
  `codecs.getincrementaldecoder` so a hostile zip decompressing
  into a single multi-GB line without newlines can't OOM
  `io.TextIOWrapper`'s line buffer.
- **`_batch_extract_gbk_meta`** reads every sample's gbk inside one
  `ZipFile` open instead of 50× re-opens. Test asserts the open
  count via `monkeypatch` on `zipfile.ZipFile.__init__`.
- **Narrowed bare `except Exception`** in `_target_options` and
  `_on_zip_picked` to typed surfaces per invariant #1.
- **NUL-anchored sentinels** —
  `_NO_GBK_KEY_PREFIX = "\x00no-gbk\x00"` and
  `_EMPTY_LIBRARY_SENTINEL = "\x00no-library\x00"` replace
  ambiguous string sentinels. NUL is rejected by
  `_is_safe_zip_member_name` and never appears in LOCUS-safe ids →
  collision-proof against any real row key.

### Tests

- 79 alignment-overlay (10 new hardening cases), 519 smoke (2 new
  no-arg-launch cases). **2,681 passing.**

---

## [0.9.3] — 2026-05-17 — Professional-audit sweep + adversarial audit sweep #7

Audit-pass sweep landing 17 of the 20 action items from a
professional code-review pass; 1 partial (`_run_update_subcommand`
refactored from 532 lines into a 200-line dispatcher + 6 named
helpers; the three cloning functions get documented deferred-refactor
notes pending dedicated regression scaffolding); 1 deferred (the
PlasmidApp controller split, with a written extraction plan).

Adversarial audit sweep #7 layered on top: 4 parallel audit agents
(concurrency, security, biology correctness, data integrity) found
3 HIGH race conditions in the agent-API feature handlers and 1
MEDIUM silent-failure in legacy-data migration.

### Adversarial sweep #7 — agent-API race + migration visibility

- **HIGH — `_record_load_counter` guards on three agent endpoints**:
  `_h_add_feature`, `_h_delete_feature`, `_h_update_feature` did
  all their work inside `_apply` closures dispatched via
  `call_from_thread`, but never captured `_record_load_counter`
  at handler entry. A user-triggered plasmid reload between agent
  dispatch and the queued UI-thread execution would cause the
  handler to mutate the WRONG molecule (delete feature idx N from
  the newly-loaded plasmid, write annotation coordinates that point
  into the new sequence, etc.) — silent cross-record data
  corruption. Now each handler captures the counter at entry and
  the `_apply` closure returns 409 `"canvas reloaded mid-edit"`
  if it shifted. Sacred invariant #28 now covers these three
  endpoints. Regression tests in
  `TestAddFeatureHandler.test_stale_load_counter_rejects` and
  `TestDeleteUpdateFeatureStaleLoadGuard` (3 tests).
- **MEDIUM — legacy-data migration visibility**: `_migrate_legacy_data`
  pre-0.9.3 silently swallowed `OSError` on `_atomic_copy` and
  marker write. A RO mount, disk-full, or permission-denied on the
  destination data dir left users thinking their library had
  vanished — the new data dir was missing entries that lived at
  the source path. Now writes a clear stderr message at startup
  listing each unmigrated file and pointing at
  `$SPLICECRAFT_DATA_DIR` as the workaround. The logger isn't
  wired up that early in import, so stderr is the right surface
  (pipx / pixi / dev shells all show it).
- **Test fixture warning**: corrected LOCUS-line column widths in
  `_minimal_gb_text()` to match Biopython's SeqIO formatter; the
  two `TestEntryVectorEndpoints` tests no longer emit
  `BiopythonParserWarning: malformed locus line`.

### Professional audit — hardening

- **Real bug fix**: 4 sites where an `except Exception as exc:`
  closure later referenced `exc` from a `call_from_thread` callback,
  which would raise `NameError` after PEP 3110 cleared the binding.
  Captured the message into a closure-safe local at the `except`
  site. Affected `OpenFileModal` open-failed path and the
  Plasmidsaurus alignment worker's error reports.
- **F-rule sweep**: 12 ruff auto-fixes (unused imports, extraneous
  f-prefixes), 10 manual F841 cleanups (dead `starts_here`,
  `ends_here`, `reh_color`, `gff_strand`, `hover_dna`,
  `grammar_id_for_color`, `dropout_start`/`end`, `total`, `t`).

### Code organisation

- **Extracted `splicecraft_biology.py`**: first deliberate
  extraction from the single-file rule. Pure biology primitives
  (`_IUPAC_RE`, `_IUPAC_COMP`, `_DNA_COMP_PRESERVE_CASE`,
  `_PATTERN_CACHE`, `_iupac_pattern`, `_rc`, `_feat_len`,
  `_seq_len`, `_slice_circular`) live in their own module and are
  re-imported into `splicecraft.py` so `sc._rc(...)` keeps working
  for every existing caller. Sacred invariants #3, #4, #8 are now
  owned by the new module.
- **Four giant functions refactored** into helpers with explicit
  signatures (no shared closure state, behaviour bit-identical,
  every existing test still passes):
  - `_run_update_subcommand` (532 → 210 line dispatcher + 7
    `_update_*` helpers). Sacred invariant #39 (snapshot before
    install subprocess) preserved.
  - `_simulate_gibson_assembly` (370 → 100 line dispatcher + 7
    `_gibson_*` helpers). All 47 Gibson tests pass.
  - `_assembly_fragment_from_source` (346 → 100 line dispatcher
    + 7 `_assembly_fragment_*` helpers). Cache contract preserved
    (invariant #17). All 569 domesticator + traditional-cloning
    tests pass.
  - `_clone_part_into_entry_vector` (355 → 95 line dispatcher
    + 6 `_clone_part_*` helpers). Both digest paths (primer-flanked
    overhang match and synthetic-insert fallback) extracted. All
    616 cloning tests pass.
- **PlasmidApp controller split — evaluated, declined as cosmetic**
  (V1_GATE.md S6 resolved). The candidate mixin clusters (Undo,
  Autosave, Settings, RestrictionScan) cut through call chains
  rather than around them — every method touches widget queries,
  `_current_record`, `_record_load_counter`, etc. A mixin
  extraction would be a pure rename of the class hierarchy with
  no decoupling benefit. The real decoupling project is a v2.x
  viewmodel layer beneath PlasmidApp. Documented at length in
  PlasmidApp's class docstring.

### Tests

- **`tests/test_perf_regression.py`** (`@pytest.mark.slow`):
  best-of-N regression gates against `tests/perf-baseline.json`.
  Best-of-N (not p50/p99) because under pytest-xdist load the
  median drifts; the MINIMUM sample is "is the code capable of
  meeting the budget" which is the real regression signal.
- **`tests/test_cli_client.py`** (31 tests): covers
  `splicecraft_cli.py` — token-file size cap + boundary, malformed-
  token rejection, response-size cap + boundary, HTTP error decode,
  connection-refused messaging, argparse surface, every documented
  subcommand registered.

### CI

- **`ruff check .`** as a CI gate (F + E9 rules; intentional E-style
  rules disabled to match codebase conventions; tests excluded).
- **`pyright`** as a CI gate (strict; production code only —
  splicecraft.py + splicecraft_biology.py + splicecraft_cli.py).
- **`coverage`** + Codecov upload on Python 3.12 (informational;
  doesn't gate merges).
- Concurrency: cancel in-flight CI on rapid push to same ref.

### Documentation

- **`SECURITY.md`** — disclosure channel, threat model, in-scope /
  out-of-scope, scope-mapping table from each sacred invariant to
  the defence it owns.
- **`CONTRIBUTING.md`** — local setup, single-file rule + three-test
  extraction criteria, test cadence, no-bare-except rule,
  security-sensitive code surfaces.
- **`V1_GATE.md`** — 14 hard gates + 8 soft gates for v1.0.0,
  documents the maintainer-approval-required principle.
- **`.github/ISSUE_TEMPLATE/bug.yml` + `feature.yml` + `config.yml`** —
  bug template requires version / Python / OS / terminal / install
  method up front; feature template asks for the bench workflow.
  Security disclosures routed to email via `config.yml`.
- **`docs/` tree + `mkdocs.yml`** — split the README's deep dive
  into `install.md`, `getting-started.md`, `features.md`,
  `keybindings.md`, `data-safety.md`, `agent-api.md`, `cli.md`,
  `architecture.md`. README slimmed from 598 → 126 lines.
- **CHANGELOG**: 12-month archive-rotation policy documented at
  the top.
- **Conda recipe**: clarified that the in-repo `meta.yaml` IS the
  reference copy (not a stale mirror).
- **RELEASE_CHECKLIST.md**: per-terminal video-archive convention
  added for diff-on-regression workflows.

### Repo metadata

- Description: "Terminal-based plasmid map viewer, sequence editor,
  and cloning/mutagenesis workbench in pure Python".
- Added discovery topics: textual, primer3, golden-braid, moclo,
  biopython, cloning, mutagenesis, restriction-enzymes. Removed
  vague topics (next-generation-sequencing, research, science,
  laboratory, sequence).

### Hygiene

- `.gitignore` explicit `.hypothesis/` + coverage artifacts.

---

## [0.9.2] — 2026-05-17

_(auto-generated from commits since v0.9.1)_

* Launch-time update prompt modal · never-stale CHANGELOG auto-gen · structured update.* events

---

## [0.9.1] — 2026-05-17 — Friendlier agent launch · downgrade-recovery via `splicecraft update VERSION`

Two ergonomic improvements layered on the existing data-safety net.

### New: `--agent` / `--agent-port` aliases

Launching the side-door for an external AI agent (Claude Code, Cursor,
aider) is now a single short flag:

* `splicecraft --agent` — alias for `--agent-api`. The original
  surface is preserved (stable contract per invariant in CLAUDE.md);
  the alias is purely additive.
* `splicecraft --agent-port=PORT` — alias for `--agent-api-port`.

Discoverable in `splicecraft --help` and in the "no SpliceCraft
session found" error message from `splicecraft-cli`.

### New: `splicecraft update VERSION` (downgrade / version pin)

If a release ships broken code, you can roll the install itself back
to the previous working version without remembering the
pip/pipx/uv/pixi incantation:

```
splicecraft update 0.9.0          # positional (most ergonomic)
splicecraft update --pin 0.9.0    # explicit flag form
```

Implementation:

* PEP 440-lite validator (`_validate_pin_version`) accepts canonical
  PyPI version strings — including `vX.Y.Z` from a git tag — and
  rejects shell-injection, extras syntax, environment markers, and
  comparison operators at the input boundary. An unvalidated string
  would land in the install subprocess argv as `splicecraft==<raw>`.
* `_build_upgrade_command(..., pin_version=...)` produces the correct
  `--force`-style command for every supported install method
  (`pipx install --force`, `uv tool install --force`,
  `uv pip install --reinstall`, `pixi global install --force`,
  `pip install --force-reinstall …`). Required because every front
  end refuses to "upgrade" to an older version; pinning needs an
  explicit reinstall.
* Refusal methods (editable / source / pixi-project) remain refused —
  the user's working tree / project manifest is still the source of
  truth.
* **Pre-update snapshot still runs first.** The sacred invariant from
  the 0.7.x data-safety pass holds: the pinned install is itself
  reversible via `splicecraft update --restore-pre-update latest`.
  Tested by `test_update_pin_snapshot_still_taken`.
* Same-as-current pin without `--force` is a no-op.
* Confirm prompt explicitly flags **DOWNGRADE** direction and points
  at the restore command for an extra layer of safety net.

### Discoverability

Main `splicecraft --help` now surfaces the recovery escape hatches so
a panicking user under stress can find them:

* `splicecraft update 0.8.10` (downgrade / pin)
* `splicecraft update --restore-pre-update latest` (roll back the
  library / collections / parts / primers to the snapshot taken
  before the last `splicecraft update` run).

README documents the future-proof-updates guarantee end-to-end.

### Tests

21 new regression tests (`TestUpdateVersionPin` × 17 + `TestAgentFlagAlias` × 3 +
the new positional + flag conflict guards). Full smoke + agent_api
suite (694 tests) green.

---

## [0.9.0] — 2026-05-17 — Simulator workbench (PCR + agarose gel)

A new menu-bar workbench — `Simulator` — that pairs in-silico PCR with
an agarose-gel renderer. Built behind the same hardening bar the rest
of the codebase has been held to: bounded inputs, narrow exception
types, structured event logging, and 56 regression tests (37 agent
endpoint + 19 pure-function / hardening guards) before the merge.

### New: in-silico PCR

`_simulate_pcr` enumerates every legal amplicon for a `(fwd, rev)`
primer pair on the loaded template. Binding model is exact-match
(verbatim primer on top strand for fwd; reverse-complement on top
strand for rev) — no mismatch tolerance, no Tm-aware annealing. The
MVP is intentionally minimal: cloning primers with 5' tails are
designed via the existing Primer Design workbench; this surface is
for hypothesis-testing "do these primers actually amplify what I
think they do?" against a real template.

* Wrap-aware on circular plasmids — amplicons that cross the origin
  are detected, reported with `wraps=True`, and the amplicon
  sequence is reconstructed as `seq[fwd:] + seq[:rev_end]`.
* `_PCR_MAX_AMPLICONS = 50` cap on result count so a mispriming
  primer on a repetitive template can't generate thousands of rows
  (a `(result capped at 50)` hint surfaces in the status bar when
  this fires).
* `_PCR_MAX_TEMPLATE_BP = 5 Mb` cap so a chromosome-scale FASTA
  import doesn't freeze the UI; refusal surfaces a clear message.
* `_PCR_MIN_PRIMER_LEN = 10` / `_PCR_MAX_PRIMER_LEN = 80`; primers
  outside this range are rejected at the input boundary.
* Primers must be ACGT only — IUPAC ambiguity is rejected at the
  boundary (the exact-match model can't honour `N`, and silently
  failing to amplify would be worse than refusing).
* **`_PCR_MAX_PRIMER_HITS = 5,000` defence-in-depth cap.** A
  pathological case (e.g. a 10-bp all-A primer on a polyA template)
  could yield millions of binding positions, blowing up the
  `fwd × rev_rc` double loop into trillions of pairings. The cap
  refuses with an empty result rather than freeze the UI; surfaces
  the same "no amplicons" status the user sees for any other
  null-result case.

Amplicons round-trip to the plasmid library as linear DNA entries
with `primer_bind` features auto-annotated at both ends (the fwd
primer at `[0, len(fwd))` on the top strand, the rev primer at
`[len(seq) − len(rev), len(seq))` on the bottom). The save path
respects the stale-canvas guard (invariant #28) — if the user paged
to a different plasmid between PCR run and Save click, the save is
refused with a re-run hint.

### New: agarose gel renderer

`_agarose_mobility` translates fragment size + DNA form to a
relative migration distance using the Helling-Goodman-Boyer (1974)
empirical observation: within each agarose %'s resolution window,
migration distance ∝ −log₁₀(bp). Plus the standard form corrections
from Lewis & Slater (1986): supercoiled migrates at 0.7× its linear
size, nicked / open-circle at 1.4×.

Per-lane sources cover the realistic bench workflow:

* **Ladder** — 1 kb Plus / 1 kb / 100 bp / Lambda-HindIII; sized to
  the loaded ladder, smallest band at dye front.
* **Plasmid (uncut)** — circular templates resolve into supercoiled
  + nicked bands; linear templates resolve as one band.
* **Digest** — restriction-digest the template with one or more
  enzymes (comma-separated in the lane's detail field); each
  fragment becomes one linear band.
* **PCR amplicon** — the currently-selected amplicon from the PCR
  sub-tab; `Send to Gel lane` puts it in scope.

Agarose percentage snaps to the configured choices (0.5 / 0.7 / 0.8
/ 1.0 / 1.2 / 1.5 / 2.0 / 2.5 / 3.0 / 4.0%) with each %'s
resolution window from Sambrook & Russell 3e Table 5-1. Up to
8 lanes per gel in the UI (16 via agent for batch flows). The
rendered image is a single Rich `Text` block — one column per lane,
well-at-top to dye-front-at-bottom, with the leftmost ladder
contributing a bp-label tick column.

### New: agent endpoints — `simulate-pcr`, `simulate-gel`

Both read-only (no token required), mirror the `simulate-gibson`
shape:

* `POST /simulate-pcr {template_seq, fwd_primer, rev_primer,
  circular?, max_amplicon?}` → `{ok, n, capped, amplicons:[...]}`.
* `POST /simulate-gel {lanes:[{source, name?, detail?}],
  agarose_pct?, template_seq?, template_circular?, pcr_amplicon?,
  height?, lane_width?, include_image?}` → `{ok, agarose_pct,
  height, lane_width, lanes:[{index, name, source, detail,
  bands:[{bp, form, mobility, row}]}], image?}`.

Same validation policy as `simulate-gibson`: types checked at the
boundary, size caps enforced (template ≤ 5 Mb, lanes ≤ 16, primers
10–80 bp ACGT, height ≤ 200), unknown sources return 400 with the
allowed set listed. `include_image=true` returns the rendered gel
as a plain-text string the agent can paste into a terminal or LLM
context window.

### Structured event logging

Six new events (`_log_event`) on the user-visible state transitions:

* `simulator.pcr.run` (UI) / `simulator.pcr.agent` (agent) — template
  length + circularity + primer lengths + max amplicon + result
  count + whether the result was capped.
* `simulator.amplicon.saved` — entry id + size + wrap flag.
* `simulator.amplicon.sent_to_gel` — selected idx + amplicon length
  + wrap flag.
* `simulator.lane.added` / `simulator.lane.removed` /
  `simulator.lane.removed_specific` — lane count after the
  operation (+ suffix for the targeted-delete case).
* `simulator.gel.run` (UI) / `simulator.gel.agent` (agent) — lane
  count + agarose % + per-source histogram + whether a PCR amplicon
  was in scope.

Sacred privacy invariant #38 (no sequence content in logs) is
preserved — events log lengths, counts, and flags, never bases.

### Defensive hardening

* **Malformed `pcr_amplicon` in `_gel_bands_for_lane`.** The agent
  endpoint accepts an arbitrary dict for `pcr_amplicon` (not just
  the shape `_simulate_pcr` produces). A hostile payload with a
  non-numeric `length` field used to surface as a 500 from
  `int("garbage")`; now coerces to 0 and renders an empty lane.
* **Non-dict `pcr_amplicon`.** Bare list / int / string would raise
  AttributeError on `.get("length")`. Now gates on `isinstance`.
* **`_PCR_MAX_PRIMER_HITS = 5,000` cap.** See above.

### UI polish

* **`on_mount` focus on `SimulatorScreen`** — the fwd primer Input
  is the natural first action; the screen mirrors the
  PrimerDesignScreen focus-the-primary-input pattern.
* **Help text now lists menu workbenches.** The `?` Help modal had a
  keyboard-shortcut-only convention which left Parts / Constructor
  / Mutagenize / Simulator undiscoverable from the help. New `Menu
  workbenches` section covers all four with one-line descriptions.
* **Duplicate CSS rule removed.** `OpenFileModal { align: center
  middle; }` appeared twice in `PlasmidApp.CSS`. Cosmetic but
  loud-in-diff cleanup.

### Tests

* `tests/test_simulator.py` — 19 new regression cases (primer-hit
  cap refusal, just-below-cap acceptance, malformed `pcr_amplicon`
  defensive paths, non-dict amplicon defensive paths).
* `tests/test_agent_api.py` — 37 new cases across
  `TestSimulatePcrHandler`, `TestSimulateGelHandler`, and
  `TestSimulatorAgentRegistration` (read-only / write-flag check).
* `tests/test_modal_boundaries.py` — `SimulatorScreen` added to the
  160 × 48 baseline-terminal fit matrix.
* File-scope `# pyright:` pragma added to `test_agent_api.py` and
  `test_simulator.py` to silence the `dict | tuple[dict, int]`
  narrowing noise. The project's `pyproject.toml` already excludes
  `tests/**` from pyright; the file pragma keeps editor / harness
  diagnostics aligned with that policy.

2,513 tests pass (up from 2,457; +56 new).

---

## [0.8.10] — 2026-05-15 — sweep #6 · deferred items · bioconda lint

Closes out the items deferred from sweep #5 + adds regression tests +
clears the bioconda lint blockers from PR #65440 so the recipe is
mergeable. CLAUDE.md invariant #46 documents the sweep. 2,457 tests
pass (up from 2,436; +21 new regression tests).

### Regression tests for sweep #5 safety paths

New `tests/test_sweep5.py` (21 cases) locks in the data-integrity
fixes a future refactor could quietly regress:

* Sidecar case-collision discrimination + legacy-path migration +
  path-length cap (T1a)
* `.bak` recovery atomicity (T1b) — verifies `_atomic_write_bytes`
  is the recovery path, not `shutil.copy2`
* SHA-256 mandatory on pre-update restore (T1c) — both refuse and
  accept paths
* Pre-update manifest size cap (T1d)
* Backup-prune glob matches collision-bumped `.bak.<ts>.<N>` files
  (T1f)
* `_safe_save_json` symlink refusal (T1g)
* `_load_dna_original` size cap (T1j)
* Orphan tempfile sweep (T1k) — old removed, recent kept, user files
  with `.tmp` substring left alone

### Cross-thread cache-reassignment lock

New module-level `_cache_lock` (RLock) wraps every `_save_*` JSON
helper's disk-write + cache-reassignment pair. Without it, two
concurrent saves could land their `os.replace` calls on disk in
order A → B while their cache reassignments land in order B → A —
leaving `_<label>_cache` pointing at older state than what's on
disk. Applied to 11 helpers: `_save_library`, `_save_collections`,
`_save_custom_grammars`, `_save_entry_vectors`, `_save_settings`,
`_save_parts_bin`, `_save_parts_bin_collections`, `_save_primers`,
`_save_features`, `_save_feature_colors`, `_codon_tables_save`.
Reads don't take the lock — `_typed_clone`-on-return plus the GIL
already protect callers from partial states.

### Stale-collection guard on `_index_usage_worker`

PrimerDesignScreen's primer-usage indexer now captures the active
collection name at dispatch and refuses-on-apply if it switched
mid-scan. Pre-fix, a scan running against the OLD library's
contents at the moment of switch would land its result in the NEW
collection's `_primer_usage_index`, surfacing wrong counts until
the next save invalidated the cache. Mirrors the
`_record_load_counter` pattern (CLAUDE.md invariant #28) along the
collection axis.

### MultiAlignPickerModal off-thread parse

`action_open_align_picker._on_picked` no longer parses GenBank
synchronously on the UI thread. Selecting 10 multi-Mb targets used
to block the picker for several seconds before the worker even
started. Now the worker receives `(entry_id, gb_text)` tuples and
parses + size-checks each off-thread, surfacing per-target warnings
via `call_from_thread`.

### Observability: heavy-op timing + state-transition + net-retry events

* `@_timed` added to 9 heavy ops (load_genbank, simulate_traditional_cloning,
  write_commercialsaas_dna_bytes, parse_commercialsaas_history,
  bulk_import_folder, create/restore_pre_update_snapshot,
  clone_assembly_into_entry_vector, create_diagnostic_bundle).
* `_set_active_collection_name` emits `collection.switched`.
* `_set_setting` emits structured `settings.changed` (covers
  active_grammar / active_parts_bin switches via the key=).
* `_migrate_entries` emits `migration.step` / `migration.step.done`
  / `migration.failed`.
* Network retries (NCBI + PyPI) emit `net.retry` events with
  endpoint, attempt, and exception class.

### Defence-in-depth

* `_gb_text_to_record` caps input at 64 MB before handing to
  BioPython's GenBank parser. Library entries are already gated via
  `_safe_load_json`'s 1 GB cap and zip extracts via the 50 MB member
  cap, but the parser itself was internally unbounded.
* `_h_capture_snapshot._apply` narrows the broad `except Exception`
  to `(ValueError, AttributeError, TypeError)` so a genuine bug (or
  KeyboardInterrupt during shutdown) propagates rather than being
  mapped to a generic 500.

### Bioconda recipe — pre-merge lint fixes

PR #65440 (the 0.8.9 first-submission) was rejected by the bot for
two recipe issues; both fixed here:

* `run_exports` added under `build:` with `max_pin="x.x"` (0.x.y
  semver permits breaks between minor releases, so downstreams must
  pin to the same minor to stay compatible). Bumps to ≥1.0.0 should
  relax this to `max_pin="x"`.
* `about.summary` trimmed to a short title; the long description
  moved into `about.description`. Matches bioconda's contributor
  policy where summary acts as a title in listings.

`./release.py` re-runs `_sync_conda_recipe` + `_submit_bioconda_pr`
automatically for 0.8.10, so the bot will see the fixes on the new
PR; the 0.8.9 PR can be closed once 0.8.10's is verified green.

---

## [0.8.9] — 2026-05-15 — adversarial audit sweep #5 · data integrity first

Seven-surface parallel audit on top of sweep #4, with **data integrity
elevated to top priority by the user**. Every corruption-class and
silent-mutation finding lands first; defence-in-depth follows. All
2,436 tests still pass; CLAUDE.md invariant #45 has the full ledger.

### Data-integrity HIGHs

* **Sidecar case-collision (corruption).** `_dna_sidecar_path` now
  case-folds the basename AND appends an 8-char SHA-1 prefix of the
  raw `entry_id`. Pre-fix, on case-insensitive filesystems (macOS
  APFS default, NTFS) two library entries `pUC19` and `puc19`
  collided on the same on-disk `.dna` sidecar path — silently
  overwriting each other's round-trip bytes, so exporting the older
  entry emitted the wrong molecule. Legacy path fallback at load
  time migrates existing sidecars on first access. Basename capped
  at 200 chars to keep the full path under NTFS's 260-char default.
* **`.bak` recovery atomicity (corruption).** `_safe_load_json`'s
  recovery branch routes through `_atomic_write_bytes` instead of
  `shutil.copy2`. Pre-fix, a power loss mid-recovery left the main
  file truncated — paradoxically *less* recoverable than the corrupt
  state we were rescuing from.
* **`_migrate_legacy_data` atomicity (corruption).** The one-shot
  legacy-data-dir → `_DATA_DIR` copy now uses an inline atomic-copy
  helper (tempfile + fsync + `os.replace`). Pre-fix, a crash
  mid-launch left a permanently-corrupt copy that the `not
  dst.exists()` idempotency check would skip on every subsequent
  launch — silent lock-in of the corruption.
* **Pre-update SHA-256 mandatory.** `_restore_pre_update_snapshot`
  refuses restore when the manifest entry's `sha256` field is
  missing or empty (was: silently skipped verify). This was one of
  invariant #39's "sacred-four" checks; the backup directory is
  user-writable, so a tampered manifest with `sha256` stripped used
  to bypass the check entirely.
* **Pre-update manifest size cap.** New
  `_PRE_UPDATE_MANIFEST_MAX_BYTES = 4 MB` applied at both
  `_list_pre_update_snapshots` and `_restore_pre_update_snapshot`.
  Pre-fix a planted multi-GB manifest would OOM the launch path.

### Durability + symlink hardening

* `_save_dna_original`, `_export_commercialsaas_dna`,
  `_create_diagnostic_bundle`, and the `_AGENT_TOKEN_FILE` writer
  all now call `_fsync_parent_dir` after `os.replace`. POSIX rename
  is atomic at the inode level but the directory entry update is
  journalled separately — without the parent-dir fsync, a power
  loss between rename and the next directory sync can leave the
  directory entry pointing at the OLD inode after fsck.
* `_safe_save_json` refuses to save through a symlinked target.
  Pre-fix, a symlinked `_LIBRARY_FILE` pointing at `/etc/passwd`
  would let the backup-read step copy the link target into a
  user-readable `.bak`. Belt-and-braces with the existing agent-side
  `_check_agent_write_path` symlink walk.
* New `_check_agent_read_dir` rejects symlinked folder args on
  `bulk-import-folder` and `create-collection` endpoints —
  `Path.is_dir` follows symlinks, so a pre-placed symlink could
  let an agent caller scan `/etc` etc.

### Operational hygiene

* `_sweep_orphan_tmp_files` collects leftover `.tmp` / `.migrating`
  / `.restoring` files in `_DATA_DIR` from SIGKILL'd / OOM-killed
  previous runs. Called from `main()` only when the data-dir lock
  was acquired AND only for files older than 1 h, so legitimate
  in-flight writes are never collected.
* `_backup_filename_patterns` returns `(base, base + ".*")` so
  collision-bumped `.bak.<ts>.<N>` files (slow-burn disk fill on
  rapid Ctrl+S in the same wall-second) are pruned alongside the
  base.
* Lockfile creation uses `O_EXCL` first so a contention failure
  can clean up only the lockfile we just created — no race-removing
  another process's lockfile.
* `_restore_from_backup` staging tmp switched from deterministic
  `<target>.restoring` to `tempfile.mkstemp` so concurrent UI +
  agent restores can't truncate each other mid-copy.
* `_load_dna_original` now size-capped via `_safe_file_size_check`
  (matching the 50 MB write cap).
* `_sync_active_collection_plasmids` + `_sync_active_parts_bin_parts`
  switched from shallow `dict(e)` to `_typed_clone` (invariant #17
  requires deep on both read AND save sides).

### Concurrency

* `_settings_flush_worker` wrapped in `try/finally` so an
  unforeseen exception (e.g., a non-JSON-serialisable value
  sneaking past `_validate_settings`) cannot wedge
  `_settings_flush_running = True` forever, silently disabling all
  subsequent setting saves. Broadened except envelope catches the
  unexpected case + logs.
* `SPLICECRAFT_SKIP_SETTINGS_FLUSH=1` test bypass mirrors the
  existing `_skip_*` flag pattern (deterministic disk state in
  tests without a trailing daemon thread).
* `_h_set_entry_vector` wrapped in `_agent_save_or_500` (8th
  endpoint); pre-fix, a disk failure here silently returned 200 OK.
* `_h_replace_sequence._apply` catches `NoMatches` / `AttributeError`
  from screen unmount during the big-rebuild apply window.
* `_AgentRequestHandler._read_body` catches socket `OSError` on
  broken connections (cleaner 400 than the dispatch wrapper's
  generic 500).
* `PlasmidsaurusAlignModal._show` adds `is_mounted` guard before
  `self.dismiss(result)` (mirror of the export modals' pattern).
* `_blocks_undo: bool = True` added to `PrimerDesignScreen`,
  `PartsBinModal`, `FeatureLibraryScreen` so Ctrl+Z under those
  screens can't pop the canvas undo stack mid-save.
* Three `@work(group="blast_run")` decorators (HMMscan / BLAST run
  / BLAST build) split into distinct groups so a build no longer
  cancels an in-flight search via shared-group `exclusive=True`.

### Attack surface

* `_h_diff_plasmid` pre-caps both seqs at `_PAIRWISE_MAX_LEN`
  before `_find_circular_alignment_offset` doubles the target — a
  50 MB library entry used to allocate 100 MB before
  `_pairwise_align`'s own cap kicked in.
* Export endpoints (`export-genbank` / `export-gff` / `export-fasta`)
  enforce extension whitelists via `_check_export_extension`.
  Pre-fix an agent could write `~/.bashrc` as GenBank text.
* `_sanitize_path` refuses `~user` syntax (bare `~` for the running
  user is still fine) — pre-fix a user-enumeration oracle via
  agent-side `_h_load_file`'s 404 vs 400 distinction.
* `splicecraft_cli.py` caps both the agent response body (50 MB,
  symmetric with server cap) and the token file (1 KB) at read time.

### Performance

* `_repopulate_plasmids` no longer calls `_load_library()` twice
  per filter keystroke — the caller already loaded into
  `lib_entries`; the redundant second call was a full `_typed_clone`
  of the cached library (hundreds of ms on a 1000-row library with
  `gb_text` blobs).
* Two seq-panel O(N) feature scans (the Enter key handler and
  `_seq_lane_clicked`'s bp-fallback branch) migrated to
  `pm._smallest_enclosing_feature(bp)`. Sweep #4 introduced the
  helper but missed these callers.

### Observability

* `_notify_save_failure` (the central choke point through which
  `_bg_notify_save_failure` and 30+ direct save sites route on
  failure) now emits a structured `save.failed` event with `target`,
  `exc_type`, and `exc_msg` — every disk-full / RO-mount / EACCES is
  now AI-parseable from log dumps.
* `_apply_record` emits `record.loaded` at every canvas swap (rec,
  bp, n_features, topology, clear_undo) so post-load events
  (restriction scan, sidebar populate, overlay paint) correlate
  against a single boundary.
* `_log_event(event, *, _stacklevel: int = 2, **fields)` —
  decorators `@_action_log` and `@_timed` now pass `_stacklevel=3`
  so the logger's `funcName:lineno` prefix lands on the wrapped
  action method, not on the wrapper closure. Restores invariant
  #43's documented contract.
* `lock.acquired` / `lock.contended` / `lock.stale` events route
  the lockfile path through `_scrub_path` so a shared log doesn't
  leak `/home/<user>/`.
* `SPLICECRAFT_DEBUG=1` env var bumps `_log` to DEBUG level —
  surfaces network-retry events and other diagnostic-only signals
  that normally stay below INFO.

### Deferred to sweep #6

Cross-thread cache-reassignment races on
`_parts_bin_collections_cache` / `_grammars_cache` /
`_entry_vectors_cache` / etc.; stale-collection guard on
`_index_usage_worker` (primer-usage scan landing in the wrong
collection's index after a switch); `MultiAlignPickerModal._on_picked`
UI-thread GenBank parse. All three require structural refactors with
dedicated test scaffolding that sweep #5's scope didn't budget.

---

## [0.8.8] — 2026-05-15 — dep bumps · Python 3.13 in CI · primer3 error logging

### Dependency floors bumped to latest

Test suite re-verified against every bumped version (2,436 tests
passing, ~5 min on 8 cores). Pins tightened from `>=` to the floor
we tested at so end-user `pipx install splicecraft` pulls the same
minimum we shipped against.

| Package | Old floor | New floor | Notes |
| --- | --- | --- | --- |
| `textual` | 8.2.5 | **8.2.6** | text-selection UX patch; no API changes |
| `pyhmmer` | 0.12 | **0.12.1** | `Profile.transition_scores` removed (we didn't use it); `Sequence.L`, `HMM.emit_sequence`, `Profile.emit_sequence` added |
| `platformdirs` | 4.9 | **4.9.6** | patch-stream |
| `pytest` | 9.0 | **9.0.3** | patch-stream |
| `pytest-asyncio` | 1.3 | **1.3.0** | unchanged effective floor |
| `pytest-xdist` | 3.8 | **3.8.0** | unchanged effective floor |
| `hypothesis` | 6.152 | **6.152.7** | shrinker / explain polish |
| `build` (dev) | unpinned | **1.5.0** | drops Python 3.9 (we already required 3.10+) |
| `twine` (dev) | unpinned | **6.2.0** | first explicit floor |

No breaking changes affecting any SpliceCraft call site. Verified
via `python -m build && twine check dist/*` end-to-end.

### Python 3.13 added to CI matrix

`.github/workflows/test.yml` and `pyproject.toml` classifiers extended
to include Python **3.13** alongside 3.10 / 3.11 / 3.12. Verified that
none of the Python 3.13 stdlib removals (`imp`, `crypt`, `imghdr`,
`distutils`, `telnetlib`, etc.) are used in SpliceCraft or its test
harness.

### Edge-case + error-logging audit (CLAUDE.md known pitfall #1)

Four `except Exception:` blocks around primer3 calls (`_mut_tm`,
`_mut_hairpin_dg`, `_mut_homodimer_dg`, and the bulk-import `_calc_tm`
fallback) were silently swallowing exceptions. Per the convention that
bare `except Exception` always pairs with `_log.exception`, each now
emits a diagnostic log line before falling back to the GC approximation
(or returning 0.0 for the secondary-structure helpers). A wave of
degenerate-sequence primers will now show up as a diagnosable bundle
entry instead of silent mis-Tm on every primer.

### Adversarial audit sweep #4 (CLAUDE.md invariant #44)

Six-surface parallel audit (exception handling, attack surface,
concurrency, data safety, performance, observability) consolidated
150+ raw findings into the patches below. All 2,436 tests still pass.

**Privacy invariants restored** — `seq.chunk_dump` no longer routes
raw DNA bases through `_log.info`; `seq.hover_copy` logs `text_len`
not the DNA letter under cursor; `_format_ui_snapshot._kv_block`
scrubs settings values through `_scrub_path` (so `hmm_db_path` etc.
don't leak the username when a raw `.md` snapshot is shared without
bundling); stale clipboard-helper docstring corrected.

**Atomic backup writes** — new `_atomic_write_bytes` byte-mode
counterpart to `_atomic_write_text`. Legacy `.bak`, timestamped
`.bak.<ts>` rotation, and daily-snapshot copies all route through
it; a mid-write crash can no longer truncate the recovery files that
the four-layer safety net depends on. Collision protectors on the
rotating-backup + lost-entries spill paths so two saves in the same
wall-second don't silently overwrite each other.

**Agent-API save contract uniform** — new `_LIVE_APP_REF` +
`_agent_save_or_500` + `_bg_notify_save_failure` helpers. 7 agent
write endpoints (`delete-from-library`, `create-collection`,
`delete-collection`, `rename-collection`, `set-active-collection`,
`bulk-import-folder`, `set-plasmid-status`) now return explicit
`500 {"error": "save failed for X: ..."}` AND notify the UI user
on disk failure (was: opaque generic 500 with the cache desynced
from disk). `set-active-collection` rolls back the active pointer
when the library save fails. 4 daemon-thread save sites
(`_drain_collection_sync_loop`, `_settings_flush_worker`,
`_sync_active_parts_bin_parts`, delete-bin re-seed) now route
through `_bg_notify_save_failure`.

**Stale-canvas guards extended** — `_h_transfer_annotations` now
captures `_record_load_counter` at handler entry and returns `409`
on concurrent canvas swap (mirrors `_h_replace_sequence`).
`ConstructorModal._save_to_library_worker` wraps
`_clone_assembly_into_entry_vector` in try/except (silent worker
death is gone). `_persist_assembly` splits the library + parts-bin
saves into per-call try blocks so partial commits surface a
`library saved as X but parts-bin write failed` toast instead of a
misleading "Save failed".

**Attack surface tightening** — `_check_agent_write_path` walks the
FULL ancestor chain via `resolve()` divergence + per-segment
`is_symlink()` check (was: immediate parent only); `_h_hmmscan`
routes `hmm_path` through `_safe_file_size_check` with a 2 GB cap
(was just `exists()` — `/dev/zero` would DoS via `pyhmmer.HMMFile`);
`_backup_info` / `_restore_from_backup` / `_safe_load_json` `.bak`
fallback now apply the 1 GB cap symmetrically (recovery paths
used to bypass the cap the main load enforces).

**Performance** — new `_smallest_enclosing_feature(bp)` helper uses
the existing `_feats_starts_sorted` bisect index + wrap second pass;
replaces the O(N) `enumerate(self._feats)` scan in `_feat_at` /
`_feat_at_linear` (UI hang on 1000+ feature WGS contigs). Settings
load/save/set swapped from `deepcopy` to `_typed_clone` (pure win
on every persistable-toggle keystroke). Startup banner reuses
cached `_RUNTIME_PLATFORM` instead of re-shelling `platform.platform()`.

**Observability** — 6 user-facing actions decorated with
`@_action_log` (`app.save.trigger`, `app.library.add`,
`app.feature.add`, `app.diff_plasmid.trigger`,
`app.export.commercialsaas`, `app.whats_new.show`); 3 heavy ops
decorated with `@_timed` (`op.blast_search`, `op.hmmscan`,
`op.annotation_transfer`); `action_undo` / `action_redo` wrappers
emit structured `undo.trigger` / `undo.refused` / `redo.trigger` /
`redo.refused` (were unstructured `_log.info`); lock + drain
lifecycle now emits `lock.acquired` / `lock.contended` /
`lock.stale` / `lock.released` + `shutdown.drain.ok` /
`shutdown.drain.timeout`; `app.click_debug_toggle` renamed to
`app.click_debug.toggle` to match the `<area>.<verb>` convention.

### Pyright config

Added `[tool.pyright]` to `pyproject.toml`:

```toml
[tool.pyright]
include = ["splicecraft.py", "splicecraft_cli.py"]
exclude = ["tests/**", "build/**", "dist/**", ...]
```

Tests duck-type heavily on Textual's `App.push_screen()` return
(typed as `Screen[object]`) and the BioPython `Position |
ExactPosition` stubs — the resulting hundreds of diagnostics
drowned out genuine errors on the application surface. Real test
breakage is still caught by `pytest -n auto -q` which already runs
against the same files.

---

## [0.8.7] — 2026-05-15 — argparse migration · pyright sweep #2 · comment hygiene

### CLI (issue #11, Psy-Fer)

* `main()`, `splicecraft logs`, and `splicecraft update` now parse
  arguments via `argparse` instead of manual `sys.argv` scanning.
  Same flag surface (every existing flag works in any position),
  same error wording (`unknown argument …`, `--out requires a path
  argument`), and the agent-API contract (`--agent-api`,
  `--agent-api-port`, `SPLICECRAFT_AGENT_API` env) is preserved
  verbatim.
* New `_SubcommandParser` / `_CliExit` scaffolding converts parser
  errors into int return codes so `_run_logs_subcommand` /
  `_run_update_subcommand` keep their historical "returns an exit
  code, never sys.exit" shape.
* Small UX improvement: flag-after-positional now works — e.g.
  `splicecraft L09137 -V` prints the version instead of erroring
  with "takes at most one positional argument".

### Pyright sweep #2

* New `_seq_len(record)` helper — guards `len(record.seq)` against
  BioPython's `Seq | MutableSeq | None` typing. Applied at every
  call site where pyright was flagging implicit None propagation.
* `_coerce_int(value, *, name)` return shape changed from
  `tuple[int | None, str | None]` to `int | str`. Callers narrow
  via `isinstance(result, str)` — no separate `assert value is not
  None` needed at every callsite. All 22 callsites migrated.
* Three `action_*` overrides renamed to avoid Textual's
  `reportIncompatibleMethodOverride` flag:
  - `HistoryViewerModal.action_dismiss` → `action_dismiss_history`
  - `MultiAlignPickerModal.action_toggle` → `action_toggle_selection`
  - `LoadPartSourceModal.action_toggle` → `action_toggle_selection`
  Bindings updated to match. `DomesticatorModal._design` attribute
  renamed to `_design_result` (the button-handler method `_design`
  was unchanged).
* ~30 narrow `# type: ignore[...]` annotations added where pyright
  couldn't follow attribute access on third-party types (Textual
  `App` / `Screen`, BioPython `Location.start`, primer3 kwargs).
  All changes are typing-system only — no runtime behavior change.

### Comment hygiene

Six stale comments fixed across `splicecraft.py` and tests:

* Two line-number refs (`"line 2569"`, `"line 916"`) replaced with
  function-name refs (`_scan_restriction_sites`, etc.) — line
  numbers rot; function names are stable.
* `LoadPartSourceModal._update_status_line` docstring referenced
  the pre-rename `action_toggle`.
* `test_domesticator.py:4296` mentioned the pre-rename `_design`
  attribute.
* `tests/conftest.py` module docstring listed three JSON files but
  the actual `_DATA_FILES` list has twelve.
* `release.py` usage example bumped from `./release.py 0.4.0` to a
  current-style example.

---

## [0.8.6] — 2026-05-14 — Polish: flake fix · Pyright sweep · docs

Cleanup release. No new features; four small categories of fixes.

### Test stability

* `test_unmount_cancels_pending_debounce` (test_smoke.py) was flaking
  ~5% of full-suite runs under xdist — the assertion fired before the
  `Input.Changed` message could dispatch the debounce timer. Added
  `await pilot.pause()` between the value mutation and the timer
  check so the test passes deterministically regardless of parallel
  load.

### Pyright tech debt

Knocked down the persistent module-level errors that have been
bleeding into every diagnostic stream:

* `_bulk_import_folder`: `progress_cb` annotation switched from the
  builtin `callable` to `_Callable` (the actual type).
* `Style.from_rich_style = classmethod(...)` monkey-patch: explicit
  `# type: ignore[assignment]` since Pyright can't model dynamic
  attribute assignment on third-party classes.
* `PlasmidMap._draw` / `_draw_linear_map` use `getattr(record, "name",
  None)` on the post-`render`-guard record so Pyright can't see a
  spurious `None` access.
* `_pairwise_align` reads `first.score` via `getattr` (the attribute
  is stable BioPython API but missing from Pyright's stubs).
* `_echo_click_modifiers` calls on `self.app` (PlasmidMap /
  FeatureSidebar / SequencePanel) routed through `getattr` — Pyright
  was complaining about attribute access on `App[Unknown]` for a
  method that only exists on the `PlasmidApp` subclass.
* `_do_save` / `_discard_changes` on `LibraryPanel._btn_back` same
  fix.
* `PlasmidApp._preload_record` annotated `"object | None"` so test
  scaffolding can assign a `SeqRecord` to it without a type error.

These are all typing-system limitations, not behavior changes. The
remaining splicecraft.py errors are BioPython stub gaps (Position
arithmetic in CompoundLocation) or test-file Screen-attr access —
not actionable in the main module.

### GitHub issues

* **#4 closed** — the `[ v = linear ]` discoverability hint on the
  circular view shipped in v0.7.8.1 alongside Koeng101's open-issue
  sweep. The issue was still open; reading the code confirms the
  fix is already in place.

### Documentation

* `README.md`:
  - **Cloning** section now mentions **Gibson assembly** as a
    Constructor tab and describes the RC-orientation hint.
  - **Agent API** section enumerates the ~60 endpoints across
    Records / Files / Library / Parts / Design / Alignment /
    History / Codon tables / Search / Data safety / Settings /
    Utility — bringing the doc in sync with the 0.8.x growth.
  - **Key bindings** table: `F1`–`F4` focus modes, `F5` restore
    panels, `F6` / `Ctrl+H` history viewer, `Alt+D` UI snapshot,
    `Alt+Shift+D` hover-debug. The pre-fix `Alt+D` description
    was stale (it moved from hover-debug to UI-snapshot in 0.7.x).
  - **Menus** table updated: Settings menu present; Enzymes
    custom-enzyme entry; History menu tab; Constructor tab
    breakdown (Traditional + Gibson + GB / MoClo).
  - **Data files** table: parts_bin_collections, entry_vectors,
    dna_originals, logs, ui_snapshots, snapshots/lost_entries,
    pre-update backups.

### Release checklist

* `RELEASE_CHECKLIST.md` de-versioned (was hard-coded to "1.0.0.0
  release checklist") so the file is reusable across releases. The
  per-terminal matrix, agent-API smoke list, and documentation-freeze
  steps all generalise; only the literal version strings changed.

### Tests

Full suite: 2436 passed, 5 skipped (451 s on 8 cores).

---

## [0.8.5] — 2026-05-14 — Plasmidsaurus agent endpoints + diff-plasmid circular

Cleans up the last deferred item from 0.8.4 — the Plasmidsaurus
alignment flow now has an agent-API surface — plus a related fix
to `diff-plasmid` that the 0.8.1 alignment work missed.

### `diff-plasmid` runs circular rotation

Pre-fix the endpoint passed query and target straight into
`_pairwise_align` without the seed-kmer rotation the UI path
adopted in 0.8.1 (GH #16). For a circular target whose origin
didn't match the query's, the C-loop paid hundreds of gap
penalties to slide the smaller offset back into register. The
fix mirrors the UI path:

* Auto-detect `circular` from the target's topology annotation.
* Probe a unique-kmer seed via `_find_circular_alignment_offset`.
* Rotate the target before alignment when an offset is found.
* Return `rotation_offset` so agents can map matches back to the
  target's original coords.

`circular` can be passed explicitly (`true`/`false`) to override
the auto-detect — useful when the source GenBank doesn't carry a
topology stamp.

### Plasmidsaurus zip alignment (2 new endpoints)

* **`list-plasmidsaurus-members`** (read; cap-protected by
  `_PLASMIDSAURUS_ZIP_MAX_BYTES` = 500 MB). Body: `{path}`.
  Returns `{members: [{name, size}], count, path}`.

* **`align-plasmidsaurus-zip`** (read). Body: `{path, member,
  target_id? | target_name?, mode? = "global", circular?}`.
  Runs the same extract → parse → rotate → align pipeline the
  UI's `_align_worker` uses, returns the full `_pairwise_align`
  result plus `rotation_offset` + `query_name` so the agent can
  label matches.

Symlinks, oversized zips, oversized members, and bad zip
signatures all bounce at the boundary (the helpers'
`_safe_file_size_check` + `_is_safe_zip_member_name` + zip-lib's
own signature check). The `_PAIRWISE_MAX_LEN` cap on each side is
surfaced as 413 before the alignment kicks off so the error is
specific.

### Tests

`tests/test_agent_api.py::TestPlasmidsaurusEndpoints` adds 10
tests covering list-members happy / sad paths (missing path, bad
zip, non-gbk filtering) and align happy / sad paths (self-vs-
self, target-by-name resolution, 404 / 422 / 400 error shapes).
`TestDiffPlasmidHandler` got three new tests for the circular
rotation behaviour. Full suite: 2436 passed, 5 skipped (462 s on
8 cores).

---

## [0.8.4] — 2026-05-14 — Agent-API parity + collections async + screen resume

Closes the 0.8.3 "deferred items" list: 19 new agent endpoints
bringing the side-door surface to UI parity, three hardening
fixes on existing endpoints, async collections-save in the
LibraryPanel, a BLAST stale-collection guard, and `on_screen_resume`
refresh hooks on FeatureLibraryScreen / PartsBinModal /
PrimerDesignScreen.

### New agent-API endpoints (19)

* **Parts bin CRUD** — `list-parts` (filterable by grammar / level /
  position; compact rows), `get-part` (full entry incl. `gb_text`),
  `delete-part` (write), `classify-part` (read; runs
  `_classify_part_from_plasmid` against a candidate sequence).
* **Codon tables** — `add-codon-table` (write; Kazusa fetch by
  taxid OR raw `{codon: count}` dict — 64-codon cap, IUPAC
  validation), `delete-codon-table` (write; built-ins refused).
* **Design + simulation** — `simulate-gibson` (read; dry-run),
  `gibson-assemble` (write; simulate + save), `design-mutagenesis`
  (read; SOE-PCR primers from a `W140F`-style mutation string),
  `design-gb-part` (read; Golden Braid / MoClo domestication
  primers), `design-primers` (read; generic Primer3 detection +
  RE-cloning).
* **Data safety** — `list-backups` (read; per-label, the four
  recovery tiers), `restore-backup` (write; verifies the
  source_path belongs to the label's backup set before applying),
  `list-pre-update-snapshots` (read), `restore-pre-update-snapshot`
  (write; sacred four checks enforced before `os.replace`).
* **Utility** — `get-history` (read; returns the parsed
  `_CommercialSaaSHistoryNode` tree as nested JSON),
  `check-primer-duplicates` (read; flags shared-sequence groups),
  `capture-snapshot` (write; same content as Alt+D, returns path).

All write endpoints carry `write=True` (token-gated). Input
validation:
* Sequence caps: 1 Mbp on classifier, 30 kbp on mutagenesis CDS,
  `_PAIRWISE_MAX_LEN` on primer-design template.
* Grammar lookup goes through `_all_grammars()` (built-ins +
  user-defined).
* Backup `source_path` parameter is verified against the live
  `_list_recoverable_backups` output — agents can't read or write
  arbitrary files through the restore path.
* Snapshot id rejected at the wire boundary against
  `_PRE_UPDATE_NAME_RE` before reaching the underlying restorer.

### Existing endpoint hardening

* `_h_search_library` caps the `query` parameter at 200 chars.
* `_h_export_genbank` / `_h_export_gff` / `_h_export_fasta` now
  run `_check_agent_write_path` — refuses to write through a
  symlink at the destination OR a parent-dir symlink (TOCTOU
  defense). Parent dir must exist (no auto-mkdir for arbitrary
  paths).
* `_h_set_setting` got `write=True` in 0.8.3; coverage assertion
  added.

### Collections save → async pattern

`LibraryPanel`'s collection rename / delete / new flows
(splicecraft.py:12814/12862/12934) used to call `_save_collections`
synchronously — a 100+ MB collections.json froze the UI 5–10 s on
every click. New `_save_collections_async` helper updates the
in-memory cache + invalidates dependent caches (BLAST, primer-usage)
synchronously and dispatches the disk write to
`_collections_save_to_disk`
(`@work(thread=True, exclusive=True, group="collections_save")`).
Errors surface via `_notify_save_failure` on the worker side.

### BLAST stale-collection guard

`_BLAST_CACHE_GENERATION` counter bumps on every
`_blast_clear_cache()` (collection mutation paths fire this).
`BlastModal._do_build` captures the generation at entry; the
`_build_done` callback compares and surfaces "Index discarded —
collections changed during the build. Click Index again to
rebuild" instead of displaying an index tied to the old
collection set.

### Screen resume hooks

`FeatureLibraryScreen.on_screen_resume`,
`PartsBinModal.on_screen_resume`, and
`PrimerDesignScreen.on_screen_resume` now re-fetch their backing
list when the screen comes back to focus — so agent mutations
underneath (`delete-part`, `update-primer`, etc.) are reflected
without close+reopen. `FeatureLibraryScreen` skips the reload
when there are pending edits so unsaved work isn't silently
discarded.

### Tests

Targeted suite: 264 passed (agent + collections + BLAST). Full
suite: 2422 passed, 5 skipped (328 s on 8 cores). One initial
failure (`test_edit_replaces_entry_and_marks_dirty`) flagged that
`FeatureLibraryScreen.on_screen_resume` was eagerly reloading
in-flight edits; fixed by gating the reload on `_dirty_indices`.

### What's not in this release

* Agent endpoints for the Plasmidsaurus zip alignment flow (would
  need a worker pattern + result-streaming, deferred).
* Per-endpoint integration tests beyond the sanity passes above.

---

## [0.8.3] — 2026-05-14 — Audit sweep: consistency, hardening, observability

Mostly-mechanical follow-up sweep driven by four parallel audits
(atomic persistence, event-logger coverage, agent-API completeness,
stale-state across UI transitions). Twelve fixes landed; gap items
are tracked for a future release.

### Security + correctness

* **Agent-API `set-setting` now requires the bearer token.** The
  `@_agent_endpoint("set-setting")` decoration was missing
  `write=True`, so the token gate was skipped — any local process on
  the loopback (when `--agent-api` is on) could mutate `settings.json`
  without the token. Allowlist + validators bounded the damage but
  the contract was violated. Regression guard in
  `test_agent_api.py::test_write_flag_is_correct` now asserts
  `set-setting` carries `write=True`.

* **Agent-API `delete-from-library` now clears the canvas when the
  loaded record is the deleted entry.** Pre-fix the agent path left
  the canvas pointing at the now-deleted entry; a subsequent Ctrl+S
  would re-create the row from the stale in-memory record. Mirrors
  the manual delete path's cleanup.

* **Constructor `_save_to_library_worker` captures
  `_record_load_counter` at dispatch.** When the user navigates the
  canvas mid-assembly, the save still completes (the assembly is
  library-bound, not canvas-bound) but the `reveal_entry_id` scroll
  is skipped — the panel repopulates without yanking the user's
  cursor away. Matches the Gibson worker's pattern (invariant #28).

### Persistence consistency

* **`LibraryPanel.add_entry` now uses the sync-cache + async-disk
  pattern** the delete path adopted in 0.7.15.1. The `_save_library`
  was synchronous on every add; a 100+ MB library froze the UI for
  5–8 s per Save. New `_add_save_to_disk`
  (`@work(thread=True, exclusive=True, group="library_add_save")`)
  writes off-thread; `_notify_save_failure` via `call_from_thread`
  surfaces disk errors.

* **Eight `@work(thread=True)` decorators got `exclusive=True,
  group=…` kwargs**: `OpenFileModal._do_load` (`file_open_load`),
  `BlastModal._do_build` (`blast_run`),
  `PartsBinModal._load_parts_bulk_worker` and `._load_part_worker`
  (both `parts_bin_load`),
  `SpeciesPickerModal._do_search` (`codon_taxid_search`),
  `SpeciesPickerModal._do_fetch` (`codon_kazusa_fetch`),
  `PlasmidApp._check_for_updates_worker` (`pypi_update_check`),
  `PlasmidApp._seed_default_library` (`seed_library`). The
  parts-bin pair is the most consequential — a click+bulk-click race
  could otherwise leave the in-memory cache reflecting the loser's
  snapshot.

* **`notify(f"Save failed: {exc}")` → `_notify_save_failure(...)`**
  at six callsites (feature colors at 25860, custom grammars at
  31067 + 31600, parts-bin edit at 33360 + delete at 34174, feature
  library at 55350). Consistent labeling + log routing.

* **Wrapped bare `_save_*` callsites** in `_codon_tables_load`'s K12
  seed (log-only — no app context) and `_codon_tables_save` from
  SpeciesPickerModal delete (`_notify_save_failure`). Pre-fix a
  disk-full would surface as a Textual crash dialog.

* **`_load_feature_colors` / `_save_feature_colors` now use
  `_typed_clone`** instead of shallow `dict(mapping)`, aligning
  with the rest of invariant #17. Functionally safe today (values
  are `str`) but breaks the pattern silently if a future schema
  bump adds a nested value.

### Observability

* **Event-logger coverage** extended to four previously-unlogged
  surfaces: `gibson.save.ok` / `gibson.save.failed`,
  `alignment.registered`, `alignment.cleared`,
  `history.viewer.open`, `agent.write.ok` / `agent.write.failed`.
  Bug-report archives now carry forensic trails for these flows.

### Performance

* **`LibraryPanel._repopulate` eliminated triple `_typed_clone`
  per Enter.** Pre-fix: `_apply_panel_width` →
  `_compute_name_col_width` → `_load_library() + _load_collections()`,
  then `_repopulate_plasmids` repeated both. Now: load each once at
  the top of `_repopulate` and thread the results down through the
  per-view methods.

* **`_parse_fasta_single` runs `_safe_file_size_check`** before
  parsing. The Domesticator's FASTA picker pre-fix bypassed the
  symlink + size guard; a multi-GB FASTA piped in would OOM the
  worker. Matches `OpenFileModal._do_load`'s protected path.

### Documentation

* **CLAUDE.md invariant #23** updated: `_SAFE_LOAD_JSON_MAX_BYTES`
  is 1 GB (not 50 MB — that's the separate `_BULK_IMPORT_MAX_BYTES`
  cap on the agent-API `load-file` endpoint).

### GitHub issues

* **#9, #13, #15, #16 closed** after Cory Tobin's bug-report sweep
  in 0.8.1.
* **#17 (whitespace → backslash in feature names)** has its
  defensive override regression-tested via
  `tests/test_commercialsaas_io.py::TestGH17LabelOverride` (4 new
  tests). Awaiting Cory's retest on v0.8.2 before close.

### Tests

`tests/test_commercialsaas_io.py` grew by 4 tests
(`TestGH17LabelOverride`); `tests/test_agent_api.py` got the
`set-setting` write-flag guard; `tests/test_domesticator.py` updated
to match the new FASTA size-check error path; `tests/test_smoke.py`
self-isolates `_compute_name_col_width_caps_at_ceiling`. Full
suite: 2422 passed, 5 skipped (518 s on 8 cores).

### Audit findings deferred to future releases

Recorded in audit reports; not addressed here:

* 12 agent-API endpoints missing (Gibson, mutagenesis, GB primer
  design, generic primers, Plasmidsaurus zip alignment, parts-bin
  CRUD, codon-table add/delete, history viewer, restore-backup,
  pre-update snapshots, primer-dup check, UI snapshot capture).
* Several sync `_save_collections` UI callsites (rename / delete /
  edit) — large collections still freeze briefly on commit.
* `BlastModal._do_build` lacks a stale-collection guard.
* Open screens (`FeatureLibraryScreen`, `PartsBinModal`,
  `PrimerDesignScreen`, `BlastModal`) don't re-fetch on
  `on_screen_resume` — agent mutations underneath aren't reflected
  until the screen is dismissed + reopened.

---

## [0.8.2] — 2026-05-14 — Gibson assembly hardening

Six fixes against the new Gibson-assembly pane (introduced in this
working tree, never shipped) caught by a pre-release audit pass before
the feature went live.

* **`_on_save` converted to a `@work` worker.** The save handler used
  to call `_record_to_gb_text` + `_load_library` + `_save_library`
  synchronously on the UI thread; a 50 kb Gibson product would freeze
  the modal for 200–500 ms. New `_gibson_save_worker`
  (`@work(thread=True, exclusive=True, group="gibson_save")`)
  snapshots lane + product on the UI thread, dispatches the heavy
  serialisation + disk write off-thread, and routes failures through
  `_notify_save_failure` via `call_from_thread` — matching the
  Traditional / Constructor save paths and obeying the
  worker-pattern convention in CLAUDE.md invariant #42.

* **Stale-record guard (invariant #28).** Worker captures
  `_record_load_counter` at dispatch; if the canvas moves to a
  different plasmid between Simulate and Save, the entry is still
  saved (lane fragments are self-contained, not pinned to the
  canvas) but its `source` field is tagged
  `constructor:gibson:stale-canvas` for diagnostic clarity.

* **RC-orientation hint at failed junctions.** When forward-orientation
  overlap detection fails at a junction, the simulator now probes
  `_rc(b_seq)` and `_rc(a_seq)` at a 10 bp threshold and surfaces a
  targeted "did you mean to flip 'Fx'?" hint in both the
  `overlaps[i]["rc_hint"]` payload and the user-facing error. Common
  failure mode for PCR products whose primer-pair orientation got
  inverted at the bench; silently failing with "no homology" hid
  the actual problem.

* **Wrap-feature shift refactor.** Old shift loop reasoned about the
  product wrap from `ms <=> me` ordering, which is ambiguous when
  modulo collapses both ends to the same value. New logic decides
  product topology from `span` (linear length, invariant under
  shift), eliminating the `else` ambiguity. Aligns the simulator's
  wrap math with the `_feat_len` semantics every other wrap path
  in SpliceCraft uses.

* **Wrap-pair sentinel tagging + product re-merge.**
  `GibsonAssemblyPane._record_features` now tags both halves of a
  source-plasmid wrap feature with `_wrap_pair` / `_wrap_role` /
  `_wrap_total` markers. The simulator's shift loop detects pair
  adjacency at the product wrap and re-merges into a single wrap
  feature when conditions fire (defensive — Gibson chemistry's
  homology-arm trim makes the merge unreachable today, but the
  scaffold is in place for future wrap-preserving assembly modes).
  Sentinel fields are always stripped before features leave the
  simulator.

* **Negative-offset skip (was clamp).** Features that fall before the
  product start (pathological middle-fragment exhaustion path) are
  now skipped instead of silently clamped to `start=0`. Clamping
  silently shifted biological coordinates; skipping is honest
  about the lost annotation.

### Tests

`tests/test_gibson.py` grew by 9 tests in a new `TestGibsonHardening`
class:

* `test_rc_hint_when_second_fragment_flipped` + the upstream-flipped
  variant + the no-hint-on-real-failure negative case
* `test_wrap_sentinels_stripped_from_output` (no `_wrap_*` keys leak)
* `test_wrap_pair_halves_both_survive_when_split_in_product`
* `test_wrap_pair_remains_split_when_halves_separated`
* `test_wrap_pair_head_inside_leading_overlap_filtered`
* `test_record_features_marks_wrap_pair`
* `test_save_dispatches_worker` (B1 regression — patches the worker
  and asserts dispatch + Save-button disable-on-click)

Full suite: 2418 passed, 5 skipped (310 s on 8 cores).

---

## [0.8.1] — 2026-05-14 — Cory Tobin issue sweep: F5 + alignment offset + custom enzyme list

Four threads, all driven by Cory Tobin's open GH issues:

* **GH #15 — F5 muscle-memory restoration.** F5 was reassigned from
  "restore all panels" to "show construction history" in 0.7.11.0;
  Cory reported that the new binding kept showing "No construction
  history recorded" instead of returning him to the split-window
  layout. Reverted: F5 → `focus_panel_all` (the F1-F4 inverse);
  history moves to F6 + Ctrl+H + the History menu tab. The
  "F5 = restore" hint baked into the focus-mode notify strings now
  matches reality again.

* **GH #16 — Circular alignment offset.** Plasmidsaurus reads + the
  GenBank reference both start at arbitrary origins on a circular
  plasmid, but the pairwise align was naively pairing bp 1 of each
  sequence — producing huge mismatch / gap counts that disagreed
  visibly with the equivalent alignment in another editor. The
  align worker now finds a unique seed kmer in the query, locates
  it in the target, and rotates the target so the seeds register
  before running the global align. Synthetic test case: a 700-bp-
  rotated read aligned at 66% identity / 526 gaps pre-fix vs 100% /
  0 gaps post-fix. The target record's features are rotated by the
  same offset so the AlignmentScreen's feature lane lines up with
  the alignment columns.

* **GH #13 — Custom enzyme list.** New `Enzymes → Edit custom enzyme
  list…` menu entry opens a modal where the user types comma- or
  newline-separated enzyme names. Save commits the parsed CSV to
  `restr_custom_enzymes` (settings.json) and toggles
  `restr_use_custom_list`; when active, the restriction overlay
  shows ONLY those enzymes, with the `unique_only` and
  `min_recognition_len` filters bypassed (the user has hand-picked
  the set, so a multi-cutter or 4-cutter shouldn't be hidden).
  Unknown names are silently dropped with a yellow count summary —
  a typo or HF-variant rename doesn't strand the rest of the list.
  MVP single-list design; multi-named lists can land in a future
  release if users start asking for them.

* **GH #9 verification.** The intron-aware translation fix shipped
  in v0.7.9.0 is still working — `_exons` stamping in
  `PlasmidMap._parse`, splice-aware `_cds_aa_list`, and
  `_spliced_idx_to_genomic_bp` are all intact. Posted on GH asking
  Cory to confirm so we can close.

### Added: `_find_circular_alignment_offset` + `_rotate_seq_record`

Two new helpers in the alignment path. `_find_circular_alignment_offset`
walks the query in even-spaced strides, tries each kmer as a seed
against a doubled target (so wrap-spanning seeds resolve cleanly), and
returns the FIRST unique-hit seed's offset. Low-complexity seeds
(<4 distinct bases) skipped so homopolymer runs don't dominate.
`_rotate_seq_record` builds a new SeqRecord whose sequence + features
are rotated so a chosen position becomes the new origin; features
that straddle the new origin emit as a `CompoundLocation`.

### Tests

- 13 new regression tests:
  * 6 for `_find_circular_alignment_offset` + `_pairwise_align`
    behaviour at the rotation boundary
  * 4 for `_rotate_seq_record` (zero offset, simple shift, metadata
    preservation, wrap-aware feature handling)
  * 8 for the custom-enzyme allow-list filter (min-len override,
    unique-only override, unknown-name drop, empty-list semantics)
- F5 / F6 binding swap reflected in the existing
  `test_app_has_history_and_restore_bindings` assertion.

---

## [0.8.0] — 2026-05-14 — Async save sweep + GB chain hardening + .dna label override

Five threads: every remaining sync `_save_library` / `_save_primers` on
a hot UI path moved to a `@work` worker (collection switch, delete
plasmid, primer status cycle, Traditional cloning Save Fwd/Rev,
Domesticator Save Primers); the Constructor's L2→L3 iteration now has
regression coverage; the Constructor auto-clears the lane and refreshes
the palette after a successful save so the next iteration stages
without manual reset; a yellow hint cues the user to verify the bound
backbone matches the new level after a source-level radio switch; and
the `.dna` import path pins each feature label to the raw 0x0A XML
`name` attribute as a defensive override against upstream parser
whitespace mangling (GH #17).

### Fixed: five more UI freezes on multi-hundred-MB libraries

Same pattern as the 0.7.15.1 rename fix — sync in-memory cache update
+ UI refresh, then dispatch the slow disk write to a
`@work(exclusive=True)` worker. On a 156 MB `plasmid_library.json` +
mirrored collection, each of these used to block the UI for 5-15 s on
a button click or keypress; they're now effectively instant.

- **Collection switch** (`LibraryPanel._collection_switch_save_to_disk`):
  picking a different collection in the side panel updates the cache
  + repopulates the table sync, then writes the new library off-thread.
  Skips the active-collection mirror entirely since the plasmids
  literally came from the collection we just activated — mirroring
  back is a no-op data-wise but used to cost another 156 MB write.
- **Delete plasmid** (`LibraryPanel._delete_save_to_disk`): completes
  the half-fix from earlier where `async_sync=True` only deferred the
  collection mirror — the main library write is now async too. The
  parts-bin cascade (removing bin rows that mirror the deleted
  library entry) also goes off-thread now.
- **Primer status cycle** (`PrimerDesignScreen._primer_status_save_to_disk`):
  Shift+S on a primer-library row cycles Designed → Ordered → Validated.
  Sync save was 5-15 s per keypress on a 10k+ primer library; the
  exclusive group means rapid keypresses cancel earlier writes.
- **Traditional cloning save** (`TraditionalCloningPane._trad_save_to_disk`):
  Save Forward / Save Reverse buttons. Same async pattern.
- **Domesticator primer save** (`DomesticationScreen._dom_primers_save_to_disk`):
  the "Save N primers to library" button after a domestication run.

### Added: L2 → L3 chain regression coverage

The Constructor's MOD-source radio (`source_level=2`) is a catch-all
for level ≥ 2 sources — `_level_matches_tab(N, 2) = True` for any
N ≥ 2 means an L3 plasmid surfaces in the MOD palette and a saved L3
product gets tagged as MOD. The biology works (Alpha ↔ Omega
alternation handles the cycle position; the enzyme auto-detect
fallback in `_assembly_fragment_from_source` finds the right cutter
regardless of stored level) but had no test guard before:

- `TestCloneAssemblyIntoEntryVector.test_l2_to_l3_assembly_uses_primary_enzyme`
  — runs the full L0 → L1 → L2 → L3 chain end-to-end and asserts the
  L3 product carries content from both parent L2 MODs.
- `TestPersistedAssemblyMetadata.test_persist_mod_to_next_stores_level_3`
  — drives `_persist_assembly` directly with `source_level=2` and
  verifies the parts-bin entry gets `level=3`, `type="MOD"`, and the
  right overhang fallback when the digest probe can't release a
  clean L3 fragment.

### Added: Constructor auto-clears the lane after save

`_on_constructor_save_success` now wipes the lane for the matching
grammar and refreshes the palette + validation. Pre-fix the user had
to click "Clear Lane" or switch radios before staging the next
iteration; now the constructor is immediately ready for the next
build. Best-effort — wrapped in try/except so a mid-dismissal modal
doesn't trip on a missing widget.

### Added: backbone rebind hint at source-level switch

The GB cycle alternates vector families (Alpha for Esp3I-dropout
destinations, Omega for BsaI-dropout destinations) every level. A
backbone bound at L0 → TU has the wrong dropout enzyme for TU → MOD,
and vice versa. We don't auto-clear the binding because the user
might legitimately pick a custom vector that breaks the convention,
but the level-radio handler now fires a yellow notify when stepping
up to remind the user to verify the binding fits the new cycle.
Stepping down doesn't trigger (the bound vector was already valid for
the lower level).

### Hardened: `.dna` feature labels pin to raw 0x0A XML

`_augment_dna_record_from_packets` now extracts the `name` attribute
from each `<Feature>` element in the 0x0A packet alongside the colour
extraction, and overrides `feat.qualifiers["label"]` after BioPython's
parse. Whatever the upstream parser does with whitespace, the
displayed label now matches the source XML byte-for-byte (after only
stripping NUL / CR / LF, which would break a single-row sidebar
render). Defensive — Cory Tobin reported feature names with spaces
appearing with backslashes after import (GH #17); we couldn't
reproduce it with synthetic or real fixtures, but the override
ensures whatever the XML actually carries is what the user sees.

### Tests

- 2 new tests for the L2 → L3 chain (above).
- All 788 tests across collections / domesticator / primers /
  traditional cloning / data safety / commercial-format I/O continue
  to pass after the async-save migrations.

---

## [0.7.15.1] — 2026-05-13 — Async rename + display-name preservation + markup hygiene

Three threads: rename now writes off-thread so the UI doesn't freeze
on multi-hundred-MB libraries; saved plasmid / part / TU / MOD names
preserve spaces, `+`, and other printable symbols (id stays sanitised
for GenBank LOCUS validity, but the user-facing `name` field carries
the typed string verbatim); NamePlasmidModal's live-dup-check status
line and the rename Label now `rich.markup.escape` every user-
controlled string so a saved entry like `TU [draft]` can't break the
markup parser.

### Fixed: rename no longer freezes the UI

- `_rename_library_entry` updates the in-memory `_library_cache`
  synchronously (panel + title bar refresh instantly), then
  dispatches the actual disk write to a `@work(exclusive=True,
  group="rename_save")` worker. With a 156 MB plasmid_library.json
  + 160 MB collections.json mirror, the sync save was burning
  ~600 MB of disk I/O (including `.bak` rotation) on every rename
  — 5-15 s of frozen UI. The async path keeps the rename feel
  instant; the disk catches up in the background.
- Exclusive group on the worker means rapid back-to-back renames
  cancel the in-flight write; the second worker's `entries`
  already includes both renames (cache updated sync between
  mutations), so the cumulative state still lands on disk.

### Fixed: spaces and `+` survive in saved display names

- `_persist_assembly` (Constructor TU / MOD save) gained a
  `display_name` parameter; both `lib_entry["name"]` and
  `bin_entry["name"]` use it. SeqRecord `.id` / `.name` stay
  sanitised so the GenBank LOCUS line is valid, but the library /
  parts-bin display name carries the user's typed string verbatim.
  History XML's parent-label also uses the display name.
- `LibraryPanel.add_entry` (Ctrl+Shift+A) reads
  `record._tui_display_name` if set (re-saving an already-loaded
  entry no longer downgrades "MAV 32 + Test" to the sanitised
  LOCUS form).
- `LoadPartSourceModal._resolve_match_to_record` stashes the
  library entry's `name` on the picked record as
  `_tui_display_name`. The Load Part worker (single + bulk) reads
  it so parts-bin entries inherit the user-typed name instead of
  the sanitised LOCUS.
- Existing path / control-char sanitisation in
  `_sanitize_plasmid_name` is untouched — `/`, `\`, NUL, and C0
  control chars are still stripped before any name reaches the
  persist layer.

### Added: title bar + map header show the typed display name on reload

- New `PlasmidApp._record_display_name(record)` helper reads
  `_tui_display_name` (stashed on every library-load path) with a
  fallback to `record.name` for unsaved records.
- Three callsites updated: plasmid map circular header (`_draw`),
  linear flag header (`_draw_linear_flag`), and window title in
  `_mark_dirty` / `_mark_clean`. Reload "MAV 32 + Test" from the
  library and every visible header reads it back exactly as
  typed — the underscored LOCUS form lives on the on-disk file
  only.
- `_rename_library_entry` also updates the loaded record's
  `_tui_display_name` so the title bar reflects the rename
  immediately.

### Hardened: Rich markup injection in NamePlasmidModal + RenamePlasmidModal

- `NamePlasmidModal._refresh_dup_state` now `rich.markup.escape`s
  every user-controlled string (existing entry names, soft-hit
  list, the cleaned-input preview) before interpolating into the
  `Static(markup=True)` status line. Without this, a library
  entry called `TU [draft]` would render `[draft]` as a malformed
  Rich-markup tag.
- `RenamePlasmidModal` escapes `current_name` before passing it
  to the "Current name:" Label. Same hygiene that the History
  viewer (invariant #11) already follows.
- New test `test_markup_chars_in_existing_name_dont_break_status`
  guards.

### Added: live dup-check is more sensitive

- Three severity tiers replace the prior exact-only check:
  * **Exact** case-folded name OR sanitised-id match → bold red
    `✗ DUPLICATE` + Save disabled.
  * **Substring** match in either direction (typed name is a
    substring of an existing entry, or vice versa, and not an
    exact match) → yellow `⚠ similar to: ...` (up to 3) + Save
    enabled. Catches "MAV 32" while you're typing "MAV 32 V2"
    without blocking the legit distinct name.
  * **Available** → bold green `✓ Name available`.
- Cleaning-hint preview ("will save as: X") shows as the user
  types, replacing the prior 2-press confirmation cycle.
- Reference table below the input lists every library entry in
  the active collection (natural-sorted; dim placeholder when
  empty) so the user can scan for collisions at a glance.

### Tests

- 5 new tests for `PlasmidApp._record_display_name` (precedence:
  `_tui_display_name` > `record.name` > `record.id` > `"?"`;
  whitespace-only stash falls through).
- 4 new tests for `NamePlasmidModal` (existing library listed,
  empty-library placeholder, substring soft warning, markup chars
  don't break status).

---

## [0.7.15.0] — 2026-05-12 — Multi-bin parts + GB classifier rewrite + naming-modal duplicate guard

Five threads land in this release: multi-bin parts storage (mirrors the
plasmid Collections architecture), a multi-select Load Part picker
(bulk-classify TUs in one shot), a classifier rewrite that fixes
MAV-25-in-Alpha-2 + MoClo TUs in any acceptor (try BOTH digest
fragments + drop enzyme-parity inference + per-acceptor stuffer-pair
matching), `gb_text` storage on L1+ parts so the Constructor can chain
them into MODs (with a library-fallback for existing parts-bin entries
saved before this version), and a NamePlasmidModal that lists existing
library entries + refuses duplicate names in real time.

### Added: parts-bin collections (multi-bin storage)

- New `parts_bin_collections.json` mirrors the plasmid
  `collections.json` architecture: each bin is a named snapshot with
  ``{name, description, parts, saved}``. Active-bin pointer in
  `settings.json` (`active_parts_bin`).
- New `PartsBinPickerModal` opens before the parts bin proper: list
  bins with name / #parts / description / saved date, plus
  **New / Rename / Duplicate / Delete / Open** buttons. Refuses to
  delete the last remaining bin (notify rather than dismiss).
- `_ensure_default_parts_bin` migration runs in `App.compose()` —
  wraps any pre-existing `parts_bin.json` contents into a "Main Parts
  Bin" wrapper on first launch; idempotent on subsequent launches.
- `_save_parts_bin` mirrors into the active bin's `parts` list via
  the new `_sync_active_parts_bin_parts` helper (same sacred-contract
  as `_save_library`'s collection mirror — invariant #10).
- Constructor scope = active bin only (mirrors the plasmid
  Library / Collections architecture so bins are project-isolated).

### Added: multi-select Load Part picker

- `LoadPartSourceModal` now has a Sel-checkbox column; **space** or
  **click** toggles the cursor row. Renamed the action button to
  **Load Selected**.
- Dismiss payload changed from a single `SeqRecord` to
  `list[SeqRecord]`; the Open file… path wraps its single record in
  a 1-item list for uniform downstream handling.
- New `_load_parts_bulk_worker` classifies every toggled plasmid in
  one batch, accumulates the resulting parts, and writes them to the
  active bin in ONE `_save_parts_bin` call. Per-record failures
  (linear topology, parse error, unclassifiable overhangs) skip with
  per-row diagnostics; the batch always proceeds. Single summary
  toast on completion.
- Empty-selection on Load Selected is refused with a notify (no more
  silent no-op dismiss).

### Fixed: TU/MOD classifier rewrite — covers both Golden Braid conventions

- `_classify_part_from_plasmid` now iterates **both** digest fragments
  instead of `_pick_insert_fragment`'s single guess. Library entries
  without `rep_origin` / antibiotic-resistance annotations no longer
  fall through to the wrong half when the insert outgrew the carrier
  (the MAV 26 family in Cory's EDEN collection: 3250 bp body with
  the correct GGAG/GTCA overhangs but a 1850 bp backbone with the
  mirrored GTCA/GGAG — pre-fix, the backbone got picked and matched
  nothing).
- **Dropped the enzyme-parity-based MOD vs TU distinction.** Overhang
  shape alone can't tell L1 from L2 across both Golden Braid
  conventions — the pDGB1 / GB 2.0 convention used in real labs has
  BsaI at L0 and Esp3I at L1, opposite from splicecraft's earlier
  assumption (Esp3I = primary = L0). Any TU-boundary or per-acceptor
  match now returns `level=1`; users tag L2 MODs manually via Parts
  Bin → Edit when needed.
- New `_grammar_acceptor_tu_pairs(grammar_id, enzyme)` helper digests
  each configured entry vector and extracts its stuffer's overhang
  pair. The classifier's third-pass check matches against these
  pairs so a TU assembled into Alpha2 / Omega1 / Omega2 (with
  non-canonical boundary overhangs) classifies as
  `TU ({role})`. Cached per `(grammar_id, enzyme)` and invalidated
  by `_save_entry_vectors`.
- Verified live on EDEN: all 7 MAV 25-31 plasmids now classify
  correctly with the right Alpha role surfaced.

### Fixed: Constructor assembly from Load-Part-saved TUs (MOD-from-TU chaining)

- `_load_part_worker` + `_load_parts_bulk_worker` now stash `gb_text`
  on the parts-bin entry when `level >= 1`. The Constructor's
  `_assembly_fragment_from_source` needs the full plasmid gb_text to
  re-digest TUs at the level-up enzyme for chaining into MODs.
  Pre-fix, parts-bin entries only carried the released body
  sequence — enough for L0 chaining but not for L1 → L2 cycles.
- `_assembly_fragment_from_source` library fallback: when a
  parts-bin entry has no inline gb_text, cross-reference the library
  by `id` OR `name` to recover it. Parts-bin entries saved before
  this version auto-fix on next Constructor use without re-Load.
- Verified live on EDEN: all six possible MAV 26-31 × MAV 25 → Omega1
  MOD assemblies now succeed (each 10,409 bp or 10,730 bp depending
  on insert variant).

### Added: NamePlasmidModal — duplicate guard + reference list

- Reference DataTable below the Input lists every plasmid in the
  active collection (natural-sorted; dim italic placeholder when the
  collection is empty).
- Live duplicate-name detection on `Input.Changed`: case-folded
  match against existing display names AND against the sanitised
  id space (catches `MAV 32` vs `MAV/32` both → `MAV_32`).
- Save button disabled while a duplicate is detected; red status
  line names the existing entry. Cleaning hint preview as the user
  types ("will save as: X") replaces the old 2-press confirmation
  cycle.
- `_try_submit` re-validates on Enter so a programmatic Enter
  bypassing the disabled button is still refused.
- Logs a warning at modal open if the library already contains
  case-fold duplicate names — surfaces pre-existing data-integrity
  issues via the diagnostic bundle.

### Hardened

- `_pick_insert_fragment` / `_pick_backbone_fragment` log a warning
  when falling back to size-based pick AND no fragment has any
  backbone-marker features. Surfaces the "wrong fragment because no
  `rep_origin` annotation" failure mode in the diagnostic bundle —
  relevant for Traditional cloning paths.
- `PartsBinPickerModal._open` defensively filters the bin's `parts`
  field through `isinstance` before re-seeding `parts_bin.json` —
  a hand-edited `parts_bin_collections.json` with non-list `parts`
  no longer corrupts the live bin.
- `_grammar_acceptor_tu_pairs` exception handling promoted from
  debug to warning — a misconfigured EV (missing gb_text, parse
  failure) is now surfaced in the diagnostic bundle instead of
  silently failing the classification.
- `_alignments_generation` counter (alignment overlay): bumps on
  `_clear_alignments` even when the band is already empty, so
  in-flight workers stop registering after Alt+Shift+A.
- `_register_alignment` refuses degenerate input (empty
  `aligned_q` / `aligned_t`).

### Tests

- 17 new tests for `tests/test_parts_bins.py` (round-trip + deepcopy
  hygiene, active-bin pointer, migration edge cases, mirror sync).
- 4 tests for per-acceptor TU classification
  (`TestClassifyPartFromPlasmidPerAcceptor`).
- 4 tests for MoClo classifier paths
  (`TestClassifyPartFromPlasmidMoClo`).
- 3 tests for `_assembly_fragment_from_source` library fallback
  (`TestAssemblyFragmentFromSourceGbTextFallback`).
- 5 new tests for `NamePlasmidModal` (existing-library listed,
  empty-library placeholder, duplicate-name detection, duplicate-id
  collision, Enter-on-duplicate refused).
- `PartsBinPickerModal` registered in
  `tests/test_modal_boundaries.py::_MODAL_CASES`; fits in 160 × 48.
- `NamePlasmidModal` CSS bumped 70 × auto → 80 × 32; still fits.

**Contributors:** Cory Mozza (issue: MAV-25-in-Alpha-2 misclassification,
EDEN collection diagnostic data).

---

## [0.7.14.1] — 2026-05-12 — CHANGELOG backfill + release.py gate

Five releases (0.7.11.0 through 0.7.14.0) shipped without
`CHANGELOG.md` entries; the What's New modal users saw on upgrade
still showed 0.7.10.x at the top of the brief list. Backfilled every
missing entry from the matching feature commit, then gated
`release.py` on a `## [<new_version>]` heading so the gap can't
recur silently.

### Fixed: missing CHANGELOG entries for 0.7.11.0 → 0.7.14.0

- All five releases now have full entries in the same voice as the
  existing 0.7.10.x sections (intro paragraph + categorised
  bullets). Users upgrading to this version (or any prior 0.7.1x
  release) will see the actual change list when they open
  `File → What's New…`, instead of the stale 0.7.10.x top entries.

### Hardened: release.py gates on CHANGELOG entry

- New `_ensure_changelog_entry(version)` check runs before the
  version bump and tag check in `release.py`. Aborts the release
  with a friendly message if the target version doesn't have a
  `## [<version>]` heading in `CHANGELOG.md`.
- Prevents the silent five-release drift from recurring. Trying to
  release `X.Y.Z` without a CHANGELOG entry now fails fast before
  the test suite even runs, so the writer notices and fixes the
  brief before the version is bumped.

---

## [0.7.14.0] — 2026-05-12 — Linear-map alignment overlay

Stack sequencing reads / library diffs as a band of coloured bars below
the rev-feature lanes on the linear plasmid map. Blue match · red
mismatch · gray gap, with the same 3-colour scheme switching from bars
to query base letters once zoom exceeds 1 col/bp. IGV-style greedy
first-fit packing so short reads share rows; click a read lane to drill
into the full AlignmentScreen viewer.

### Added: stacked alignment overlay below the linear-map rev features

- New band paints each registered alignment as a single row. Bar mode
  at <1 col/bp uses solid `█` blocks (blue match, red mismatch) and
  dithered `░` for gaps; letter mode at ≥1 col/bp renders the query
  base at each target column in the same 3-colour palette so per-base
  divergence is visible without leaving the map.
- IGV-style greedy first-fit lane packing — alignments sort by target
  start ascending, length descending, and each one slots into the
  first lane where its column extent doesn't overlap an already-placed
  read. Short alignments pile into the same row; the lane count grows
  only when there's no fitting row.
- Strand arrowhead `▶` at the right tip of each bar; read name drawn
  in dim white to the left if there's room.
- Force-linear when alignments are present: the first registration
  against a circular plasmid pins `_map_mode` to "linear" and
  `action_toggle_map_view` refuses to flip back until the band is
  cleared (Alt+Shift+A), with an explanatory toast.

### Added: Alt+A multi-target alignment picker

- New `MultiAlignPickerModal` — multi-select library plasmids (space
  toggles the cursor row's checkbox column) to align against the
  currently-loaded record. Filters the current plasmid out so you
  can't accidentally self-align. Capped at 20 targets per batch.
- `_multi_align_worker` runs the picks sequentially in a non-exclusive
  worker group. Unlike `_diff_align_worker`'s `exclusive=True`, a
  second Alt+A doesn't cancel the first — both batches contribute to
  the overlay band.
- **Alt+Shift+A** clears every registered alignment with the count
  surfaced in a toast. Help modal lists both keybinds under Cloning +
  analysis.

### Changed: alignment entry points register on the overlay instead of pushing AlignmentScreen

- `PlasmidsaurusAlignModal`'s worker and `_diff_align_worker` (the
  "Compare against library plasmid" path) now append to
  `app._alignments` and refresh the map rather than auto-pushing the
  full-screen viewer. AlignmentScreen stays reachable via a click on
  the read lane, so detail review is one click away — but the default
  after each alignment is the in-context overlay row, which is what
  users actually want for comparing against features + restriction
  sites in the same view.

### Hardened: clear-vs-worker race + degenerate-result rejection

- `_alignments_generation` counter bumps on every `_clear_alignments`
  call (even when the band is already empty). All three worker
  callbacks (`_multi_align_worker._apply` / `_summary`,
  `_diff_align_worker._show`,
  `PlasmidsaurusAlignModal._align_worker._show`) capture the counter
  at entry and refuse to register if it advanced — so hitting
  Alt+Shift+A mid-batch doesn't leave the user with "cleared"
  alignments partially reappearing as later workers land.
- `_register_alignment` refuses degenerate input (empty `aligned_q`
  or `aligned_t`); without this a corrupted result would surface as a
  phantom zero-width row in the lane stack.
- Lane click drill-in guards against missing `target_record` or empty
  `result` before pushing AlignmentScreen — the viewer's body
  dereferences `target_record.seq`, so a malformed entry would crash
  the push.
- `MultiAlignPickerModal._ok` refuses to dismiss with an empty
  selection — surfaces a notify nudging the user to space-toggle a
  row.

### Tests

- 28 unit tests for `_alignment_to_target_segments` +
  `_alignment_to_target_letters` covering match / mismatch / gap
  classification, target-resolution coordinate math (target gaps
  consume no target column), case-insensitive matching, and
  segmenter↔letters consistency across 7 parametrized inputs.
- 4 pilot-driven lifecycle tests asserting the clear-generation
  contract, that empty-band clear still bumps, and that
  `_register_alignment` rejects degenerate input.
- `MultiAlignPickerModal` registered in
  `tests/test_modal_boundaries.py::_MODAL_CASES`; fits in 160 × 48.

---

## [0.7.13.1] — 2026-05-12 — UI safety + honesty sweep: 18 fixes from a misleading-display audit

Audited the codebase for UI features that mislead the user — silent
failures, lying success notifications, wrong-coordinate displays, stale
state, and contract violations. Eighteen fixes across three severity
tiers.

### Fixed: HIGH severity (scientifically wrong or data-loss)

- **HMMscan "id%" column was `-log10(evalue)`, not identity.**
  Biologists read "50.0" in an id% column as 50 % identity; it was
  actually a score transform. Now computes real identity from the
  best domain alignment; score-only hits render "—" rather than fake
  a percentage.
- **AnnotationTransferModal displayed 0-based half-open coords under
  GenBank-style "Target start" / "Target end" headers.** Every other
  coord display in the app is 1-based inclusive; this modal silently
  disagreed by one.
- **`action_add_to_library` / library rename / 9 other `_save_*`
  callsites fired green "Saved" toasts regardless of write outcome.**
  `add_entry` now returns `bool` and all paths route success
  notifications through `_notify_save_failure` (the existing helper
  that re-raises and notifies on disk-full / RO mount / permission
  denied) instead of green-lighting writes that didn't happen.
- **8 agent-API write endpoints bypassed the documented
  `_agent_dirty_guard` contract** (`delete-from-library`, create /
  delete / rename collection, `set-active-collection`,
  `bulk-import-folder`, `set-plasmid-status`, `set-entry-vector`).
  Without the guard, an agent could mutate persisted state while the
  user has unsaved record edits, leaving the on-disk library
  inconsistent with what the user sees.

### Fixed: MEDIUM severity

- **PlasmidsaurusAlign Cancel didn't stop the worker pushing
  AlignmentScreen on completion.** PairwiseAligner's C-loop is
  uncancellable, so a clicked Cancel left the worker mid-compute;
  when it finished, it happily painted an alignment screen the user
  had dismissed. Cooperative cancel flag added.
- **Trad cloning + diff_align stale-record drops left "Simulating…" /
  "Aligning…" placeholders hanging forever.** Workers that captured
  `_record_load_counter` and detected staleness silently exited; the
  modal's status string never updated, so the user assumed the
  operation had hung. Now notify "Cancelled — active plasmid
  changed".
- **BLAST modal showed previous Program / Source results until the
  next Run.** Stale cache wasn't invalidated when the modal reopened.
- **Restriction-scan worker swallowed exceptions silently.** A scan
  failure produced empty overlays with no toast — the user assumed
  the plasmid had no sites. Now surfaces a notify.
- **PrimerDuplicatesModal said "X entries share" where X was the
  to-be-removed count.** Off by one per group; the entry that wins
  the dedupe wasn't part of "X".
- **`_feat_span_label` produced 0-based output while every callsite
  expected 1-based.** FeatureSearchModal and trad cloning span
  columns silently disagreed with the sidebar / tooltip by one
  position.
- **LibraryPanel didn't refresh after agent-API library mutations.**
  Agent-driven add / delete / rename happened on disk but the
  in-process panel never reloaded.

### Fixed: LOW severity (polish + consistency)

- EditSeq / ORFFinder / AnnotationTransfer apply paths now check
  `_record_load_counter` — guards against agent-driven plasmid swap
  while a modal is open.
- `_h_save` returns `{"error": "<reason>"}` from a new
  `_last_save_error` attribute so agents can distinguish disk-full
  from "no source path" without parsing the user-facing toast.
- Truncation ellipsis on primer seq, BLAST subject / collection, plus
  a `S+1..0..E` wrap-feature indicator in FeatureSidebar.
- Primer delete count reports actual entries removed, not requested.
- `_pairwise_align` grew `ungapped_identity_pct`; AlignmentScreen
  summary shows both flavours to disambiguate gap-inflated global
  alignments (a 200 bp insert vs 5 kb backbone reads as ~4 %
  gap-inclusive even when the aligned region is 100 % matched).

---

## [0.7.13.0] — 2026-05-11 — Biology audit: enzyme catalog + `codon_start` + wrap-cut highlight

Cory Mozza (issue #14) led a deep biology audit that surfaced
24 cleavage-tuple errors in the enzyme catalog, a `/codon_start`
qualifier ignored everywhere except GFF3 export, and several
wrap-feature edge cases that flattened to whole-plasmid spans.

### Fixed: enzyme catalog drift vs REBASE

- ~24 enzyme cleavage tuples corrected against BioPython / REBASE.
- **BsbI** and **BspLU11III** removed (no commercial supplier / not a
  real enzyme).
- **BtsImutI** renamed to **BtsIMutI** (canonical REBASE
  capitalisation).
- Recognition sites corrected for **BstXI**, **AccI**, **BaeI**.
- Four new regression-test classes lock the catalog: existence,
  recognition, cleavage tuple, and HF / v2 isoschizomer parent
  agreement.

### Fixed: GenBank `/codon_start` qualifier silently ignored

- `_translate_cds`, `_cds_aa_list`, `_paint_cds_aa`, the AA-click
  handler, and Ctrl+C protein copy all assumed `codon_start=1`. Any
  NCBI-fetched fragment with `codon_start=2` or `3` was frame-shifted
  by 1–2 bp past the leading partial codon — protein sequences
  silently wrong on every operation that wasn't GFF3 export.
- All five paths now honour the qualifier.

### Fixed: wrap-CDS rendering + Type IIS wrap-cut classification

- `_resite_highlight_dict`: Type IIS cuts that wrap the origin no
  longer drag `hi_start` across the plasmid. Wrap-encoded as
  `hi_end < hi_start`, with a wrap-aware renderer and per-strand cut
  classification.
- Mutagenize wrap-CDS loader: routed through `_feat_bounds` so
  `CompoundLocation` head-first `join()` ordering no longer flattens
  the wrap to whole-plasmid.
- `_design_detection_primers`: `% total` gating prevents a primer
  3'-ending at `bp total - 1` from being encoded as a wrap
  `CompoundLocation`.
- `_rebuild_record_with_edit`: insertion at bp 0 or bp `total`
  preserves the wrap-feature canonical shape (head / tail anchor
  invariants survive origin-edge inserts).
- `_prefill_from_feature`: wrap-aware via `_feat_bounds`;
  wrap-feature qualifiers no longer drop silently when capturing
  through AddFeatureModal.

### Changed: GFF3 split-feature ordering + mutagenize alt-start warning

- GFF3 split-feature rows now in 5'→3' biological order (was
  insertion order, which surprised downstream tools that assume
  monotonic ascending coords).
- Mutagenize modal: explicit warning when the target feature starts
  with GTG or TTG. The AATG fusion overhang silently substitutes ATG;
  users designing primers against alt-start ORFs were ending up with
  ATG-replaced inserts and didn't realise.

**Contributors:** Cory Mozza (issue #14).

---

## [0.7.12.0] — 2026-05-11 — Robustness sweep #3: worker conversions + primer dedupe modal

22-finding audit closed. The headline change is a fanout of synchronous
heavy operations to background workers — exports, primer design,
mutagenize, trad cloning, constructor, multi-FASTA import. Worker-shaped
now also = stale-record cancellable now, extending sacred invariant
#28 from canvas-mutating workers to modal / screen workers.

### Changed: heavy operations now run off the UI thread

- **3 export modals** (GenBank / GFF / FASTA) → `@work`-decorated with
  `is_mounted` dismiss guards.
- **PrimerDesignScreen**: 4 handlers fan out via a shared
  `_design_worker`.
- **MutagenizeModal**: `_optimize` + `_design` workers.
- **TraditionalCloningPane**: full off-thread via
  `_trad_simulate_worker`; `_build_*_fragment` refactored to return
  `(frag, err_msg)` tuples so the worker can surface failures cleanly.
  UI thread pre-captures `_collect_simulate_inputs` to avoid touching
  widgets from a thread.
- **ConstructorModal**: `_save_to_library_worker` for the 5–15 s
  persist (deep multi-step assemblies were freezing the UI).
- **OpenFileModal**: `_fasta_collection_worker` for multi-FASTA
  import.
- **Agent-API `_h_replace_sequence`**: rebuild runs off-thread against
  a deepcopy snapshot via a new `source_record=` param on
  `_rebuild_record_with_edit`; `_apply` guarded by `entry_counter`.

All new workers capture `_record_load_counter` at entry and drop
results if the canvas reloaded mid-flight — extends invariant #28 from
canvas-mutating workers to modal/screen workers.

### Added: PrimerDuplicatesModal — two-pass primer DB cleanup

- Runs at splash dismiss when the legacy primer DB carries duplicates
  (common after `.dna` imports).
- Two passes: sequence-collisions (existing sacred dedupe policy) AND
  name-collisions (longest-sequence wins). Defaults to KEEP (focus +
  Escape both choose Keep) so a stray Enter during splash dismiss
  can't accidentally delete data.
- `_skip_primer_dedupe_check` test flag matches the existing
  `_skip_seed` / `_skip_update_check` / `_skip_snapshot` pattern.

### Changed: primer dedup UX in PrimerDesignScreen

- Save-time warning now names the colliding entry: "matches
  `P-amp-1-F`, saved 2026-04-15".
- Design-time results pane shows a yellow "⚠ Already in primer
  library — Save will be refused" hint so users see the collision
  before they get to the naming step.

### Hardened

- `_snapshot_data_files` iterates all 10 `_USER_DATA_FILE_ATTRS`
  (was 4).
- `_restore_from_backup` preserves higher-than-current
  `_schema_version` so a v2 backup restored on v1 doesn't demote
  the on-disk version.
- OpenFileModal `lstat` + `S_ISLNK` rejects symlinks at the
  large-file confirm step.
- `_augment_dna_record_from_packets` wrap-aware via `_feat_bounds`.
- `_record_to_gff3` per-part strand for mixed-strand compound joins.
- `_save_settings` deepcopy on read AND save (closes a
  cache-poisoning hole, see invariant #17).
- `_blocks_undo` annotation on Constructor / Domesticator /
  Mutagenize / PrimerDuplicates modals.
- 3 unwrapped `_save_library` callsites routed through
  `_notify_save_failure`.
- `FetchModal._do_fetch` got `exclusive=True, group="ncbi_fetch"` to
  prevent racing fetches on rapid clicks.
- `_load_part_worker` `is_mounted` check.
- Trademark scrub closed 5 verbatim regressions.

---

## [0.7.11.0] — 2026-05-11 — History viewer + Constructor history wiring

New top-bar **History** tab + fullscreen `HistoryScreen` for the loaded
plasmid's construction lineage. `ConstructorModal` now attaches
`history_xml` to every saved entry, with parents inheriting their
nested subtree so L0 → TU → MOD lineage chains correctly through
multi-step builds (matches the TraditionalCloningPane pattern).

### Added: construction-history viewer

- New `History` menu tab + fullscreen `HistoryScreen` for the loaded
  plasmid.
- Bound to **Ctrl+H** and **F5**; the previous F5 binding
  (`focus_panel_all`) moved to **F6**.

### Changed: ConstructorModal persists assembly history

- `_persist_assembly` attaches `history_xml` to the library entry on
  every save.
- Parent records inherit their nested subtree so L0 → TU → MOD
  multi-step lineage chains correctly (matches the
  TraditionalCloningPane pattern).

### Hardened: history viewer + CLAUDE.md trim

- Iterative tree build with a node-count cap (no recursion-limit
  ceiling on deep histories).
- `rich.markup.escape` on every XML-controlled string (name,
  operation, manipulation, enzyme, parents) so a hostile `.dna`
  import can't inject styling into the viewer.
- Title / label / list truncation with `+N more` overflow indicators
  when the lineage is wider than the viewport.
- CLAUDE.md trimmed from 41 k → 32 k chars (items #36–41 condensed to
  operational summaries; full rationale lives in git).

---

## [0.7.10.1] — 2026-05-11 — `.dna` import recovers colours, primers, and feeds the primer library

The 0.7.10.0 release shipped the `.dna` augmentation path that recovers
per-feature colours and primer information BioPython's commercial-SaaS-
format parser silently drops, but two follow-up issues surfaced once it
landed in real use: bulk-folder imports never reached the primer library,
and legacy duplicate sequences accumulated in `primers.json` without a
cleanup path.

### Fixed: bulk-folder `.dna` imports now seed the primer library

- `_bulk_import_folder` (used by **New Collection → Bulk import from
  folder…** and any other folder-walk import) called `load_genbank` on
  every file — which DID run the augment helper and stash
  `_dna_primer_entries` on each SeqRecord — but the rec was discarded
  after being converted to a library entry, so the primer entries
  never reached `primers.json`. The user saw imported plasmids in the
  library but no primers in the Primers tab.
- Now `_bulk_import_folder` accumulates `_dna_primer_entries` across
  the batch and merges them into `primers.json` at the end of the
  walk. Dedupe by sequence (case-insensitive) so re-importing the
  same folder doesn't pile up duplicates, and cross-file dedupe so a
  M13 fwd/rev appearing in every plasmid of a 5-plasmid pUC-derived
  collection only lands once.
- Save failures are logged but don't abort the import — the library
  entries themselves are still built and the caller persists them;
  the primer DB sync is a side-effect convenience.

### Fixed: pre-stamped `primer_seq` qualifier no longer skips the DB append

- `_augment_dna_record_from_packets` early-`continue`d when a
  `primer_bind` feature already carried a `primer_seq` qualifier.
  That skipped both the sequence-derivation AND the primer-DB entry
  append — so any `.dna` file round-tripped through splicecraft (or
  exported from ApE / any tool that stamps `primer_seq`) silently
  lost its primers from the imported DB.
- The audit caught it: of 6 `.dna` fixtures, AB303066 (a splicecraft
  round-tripped file) reported `0/2` primer entries queued while the
  5 FFE fixtures reported `2/2`. Fixed by restructuring the
  primer_bind loop: when `primer_seq` is pre-stamped, use it verbatim
  (preserves any 5' flap longer than the bound region — deriving
  from the bound region would drop the flap); when absent, derive
  from the bound region. Either way, queue the DB entry.

### Fixed: `tm=None` no longer crashes the primer library table

- The `_refresh_library_table` row builder formatted Tm as
  `f"{p.get('tm', 0):.1f}°C"`, which crashed with
  `unsupported format string passed to NoneType.__format__` if any
  primer carried `tm=None` (legacy hand-edited entries, imports from
  before this release). Now non-numeric Tm renders as `—` instead.
- Also: every `.dna`-imported primer now gets a computed Tm at
  augment time (`primer3-py` if available, 2+4 rule fallback) — same
  shape as designed primers, no more `None` to defend against.

### Fixed: legacy duplicate primers in `primers.json` finally clean up

- The 0.7.10.0 dedupe paths (`_apply_record`, `_bulk_import_folder`)
  only filtered NEW additions — they didn't remove duplicates that
  already existed in `primers.json` from earlier sessions (manual
  JSON edits, pre-dedupe imports, etc.). The user accumulated "many
  copies of the same primer with identical sequence" over time
  without any way to clean them up short of editing the JSON.
- Now `_save_primers` itself dedupes by sequence (case-insensitive)
  on every write. First-by-position wins so callers that prepend MRU
  at index 0 keep their newest copy. Sacred policy now enforced
  end-to-end: **one entry per unique sequence** across every save
  path. Existing duplicates collapse on the next save (any import,
  design, status cycle, rename, or delete triggers cleanup).
- Defensive: entries without a usable `sequence` (string-typed and
  non-empty) are kept verbatim — losing them silently would be
  worse than leaving the user a one-off oddity to investigate.

### New: `PlasmidMap._parse` honours `ApEinfo_*color` qualifiers

- Pre-fix, every feature got a deterministic-but-unrelated colour
  from `_FEATURE_PALETTE` rotation regardless of what the source
  file said. Now the parse path reads `ApEinfo_fwdcolor`,
  `ApEinfo_revcolor`, or `color` qualifiers first (validated as
  CSS-hex shape) and falls back to the palette only when none is
  present. Helps `.gb` files from ApE and Geneious too, not just
  `.dna` imports.

### Tests

- **`test_commercialsaas_io.py`** — 13 new tests in
  `TestDnaImportAugmentation` / `TestAugmentHelperUnit` /
  `TestColorQualifierReadInPlasmidMap` pin every fix above:
  per-feature colour recovery from real FFE fixtures, primer_seq
  derivation from bound region, RC for reverse-strand primers,
  primer DB stash, palette overridden by qualifier, pre-stamped
  `primer_seq` still appending DB entry, flap-preservation when a
  longer `primer_seq` is present, bulk-import flush to `primers.json`,
  cross-file dedupe in the bulk path, malformed colour rejected,
  colour qualifier wins in `PlasmidMap._parse`, palette fallback
  when no qualifier.
- **`test_primers.py`** — 6 new tests in
  `TestPrimerLibraryShowsImported` / `TestPrimerLibraryScrollable`:
  imported primer appears in the library DataTable on screen open,
  `tm=None` legacy entry renders without crash, library scrolls past
  viewport (60 unique primers, cursor navigates to row 59), dedupe
  on save collapses 5 entries → 2 by sequence, and a missing-sequence
  entry survives the dedupe.
- Net: 2208 → 2229 passing (+21 from this release).

---

## [0.7.10.0] — 2026-05-10 — full GB 2.0 grammar + performance sweep + CDS start-codon fix

### New: full Golden Braid 2.0 grammar (`_GB_POSITIONS` expanded 7 → 17 slots)

The Golden Braid L0 grammar now exposes every position from the
canonical GB 2.0 fusion-site table (Sarrion-Perdigones et al. 2013),
not just the BASIC subset. Users can now domesticate and assemble
parts for the **SECRETED**, **CT-FUSION**, **NT-FUSION**, **OP-PROM-A**,
and **OP-PROM-B** workflows from the official figure.

- **5' Non-Transcribed** — `Promoter` (combined PromUTR+ATG; Pos 01-12,
  unchanged for BASIC workflow), `Promoter-only` (PromUTR without LINK),
  `Operator-A` (Pos 01-02, OP-PROM-A operator), `Operator-B` (Pos 02,
  OP-PROM-B operator), `Min Promoter` (Pos 03-12, pairs with either
  Operator).
- **5' UTR** — `5' UTR` (kept under this name for backward compat; it's
  technically the LINK position Pos 12 in canonical GB 2.0), plus
  `Distal 5' UTR` (Pos 03-11, the actual GB 2.0 5'UTR upstream of LINK).
- **Translated region** — `Signal peptide` (Pos 13, SECRETED workflow
  N-terminal coding extension), `CDS` (Pos 13-16, full CDS with stop;
  unchanged for BASIC), `CDS-NS` and `C-tag` (legacy 2-part split,
  preserved for back-compat), plus canonical variants `CDS-NS (CT)`
  (Pos 13-15, CDS no-stop for CT-FUSION), `CT-tag` (Pos 16, canonical
  C-terminal tag), `CDS-after-SP` (Pos 14-16, CDS body after a signal
  peptide).
- **3' Non-Translated** — `Terminator` (Pos 17-21, combined 3'UTR+TER;
  unchanged for BASIC), `3' UTR` (Pos 17 alone), `Terminator-only`
  (Pos 21 alone).
- Each new slot has its own colour in `_GB_TYPE_COLORS` (5'NT shades of
  green, 5'UTR shades of cyan, translated shades of yellow/orange, 3'NT
  shades of blue) and INSDC mapping in `_GB_PART_TYPE_TO_INSDC`
  (`Signal peptide → sig_peptide`, `Distal 5' UTR → 5'UTR`, `3' UTR →
  3'UTR`, etc.).
- **No breaking changes**: existing user parts classified under the
  pre-existing slot names keep their `position` / `oh5` / `oh3` fields
  unchanged. Legacy `Pos 1` / `Pos 1a` / `Pos 1b` / `Pos 3-4` / `Pos 3`
  / `Pos 4` / `Pos 5` labels are preserved on the back-compat slots so
  parts-bin entries that hardcoded these stay readable.
- **Cross-grammar collision trade-off** — three new GB positions
  (`3' UTR` GCTT/GGTA, `Terminator-only` GGTA/CGCT, plus the existing
  `Promoter` GGAG/AATG) share overhangs with MoClo Plant Pos 4 / Pos 5
  / Pos 1. Post-cloned MoClo C-tag / Terminator / Promoter parts (no
  BsaI sites left to disambiguate from GB's level_up enzyme) will
  classify as gb_l0; the user can re-tag via the Parts Bin Edit modal.
  Same precedent as the 0.7.7.2 Promoter expansion.

### Fixed: CDS annotation in cloned L1 plasmids now includes the ATG start codon

**User report**: "CDS's cloned seem to lose the annotation of their
ATG because it also occupies the AATG overhang."

Real bug. The GB 2.0 fusion overhang at the Pos 12→13 boundary is AATG
(= A + ATG, where the A is a spacer and ATG is the start codon). The
domesticator's forward primer absorbs the ATG into this overhang and
PCR-binds at codon 2 of the source CDS — so the L0 part's `sequence`
field (the body between overhangs) starts at codon 2 and the ATG lives
only inside the AATG fusion. When the L0 part was assembled into an L1
plasmid, the CDS feature on the assembled product spanned only the
body, visibly dropping the start codon from the plasmid map.

- New helper `_atg_offset_for_part(part_oh5, part_type)` returns the
  3-nt upstream extension that pushes a coding-part feature's 5'
  boundary back into the AATG overhang so the ATG is included. Returns
  3 for any coding part type (`CDS`, `CDS-NS`, `Signal peptide`,
  `CDS-NS (CT)`) whose 5' overhang is AATG; 0 otherwise. Defensive
  against `None` / non-string inputs.
- Applied at two fix points: `_reconstruct_l0_features_in_seq`
  (re-deriving L0 part features on legacy L1 plasmids) and the
  chained-features loop in the assembly path (where new L1 cassettes
  get their features synthesised). Strand-aware: forward parts get
  the extension at the lower coordinate, reverse-strand parts at the
  upper coordinate. Clamps to `[0, insert_len]` so the linear-insert
  case can't produce a negative start.
- Regression guarded by `TestAtgOffsetForPart` (5 tests on the helper)
  + `TestReDerivedCdsIncludesStartCodon` (4 integration tests covering
  the forward CDS path, the Promoter-doesn't-extend path, the Signal
  peptide path, and the origin-clamp edge case).

### Performance sweep

A profile-driven pass shaved hot-path costs on multi-plasmid workflows
and library I/O. Each change was bench-validated via
`scripts/perf_probe.py` and `scripts/perf_probe_render.py` (both new,
not pytest targets — kept for future regression detection).

- **`_typed_clone` replaces `copy.deepcopy` in 19 cache sites** —
  library / collections / features / parts-bin / primers / grammars /
  entry-vectors / codon-tables / assembly-fragment caches. Shares
  immutables by reference (strings — the bulk of `gb_text` payloads —
  don't need re-allocation), recursively clones dict / list / tuple,
  falls through to `deepcopy` for any unexpected type so sacred
  invariant #17 ("caller mutations can't poison the cache") is
  preserved end-to-end. **Bench**: 2.4–3.1× faster on every library
  size from 10 entries × 5 kB up to 100 entries × 100 kB. `_load_library()`
  is called from 50 sites across the codebase, so the speed-up
  compounds on the Ctrl+S / library-add hot paths.
- **`@lru_cache(maxsize=4)` on `_rc(seq)`** — repeated reverse-complement
  calls on the same sequence drop from 0.95 ms to 8 µs on a 200 kb
  cosmid (~100× on cache hit). Tiny cache (cached strings can be
  200 kB+) covers the dominant workload (per-keystroke restriction
  scan on the current sequence).
- **`_PATTERN_CACHE` converted to bounded LRU(256)** — was an
  unbounded `dict`. Defensive (memory, not speed); enzyme catalog is
  ~120 unique sites so steady-state is well under the cap.
- **`_BUILD_SEQ_CACHE_MAX` + `_CHUNK_LAYOUT_CACHE_MAX` bumped 4 → 16**
  to match the downstream `_CHUNK_STATIC_CACHE` / `_CHUNK_OVERLAY_CACHE`
  sizes. The previous cap evicted entries during multi-plasmid LRU
  hopping; on a 50 kb × 5-plasmid cycle the working set drops from
  19 ms to 11 ms per hop (1.8× faster). `_CHUNK_STATIC_CACHE` and
  `_CHUNK_OVERLAY_CACHE` already absorbed cursor + selection moves
  correctly, so no deeper render-pipeline reshape was warranted.

### Tests

- `tests/test_data_safety.py::TestTypedClone` — 11 tests pinning the
  invariant-#17 contract (immutables shared, mutables cloned, mutation
  isolation, deepcopy-equivalence for JSON-typed payloads, LRU bump,
  max-size enforcement, bool/int discrimination, cycle awareness).
- `tests/test_domesticator.py::TestAtgOffsetForPart` (5 tests) +
  `TestReDerivedCdsIncludesStartCodon` (4 tests) — the start-codon
  fix.
- Updated existing tests touched by the GB 2.0 expansion: hardcoded
  INSDC mapping list (`TestGbPartTypeToInsdcMap`), `test_grammar_pos_slots_includes_cds_ns_alias`
  (made robust to grammar expansion by asserting the alias relationship
  rather than a hardcoded slot index), and the MoClo fall-through
  classification test (moved from `(GGTA, CGCT)` to `(AATG, AGGT)`
  since the former now collides with the new GB `Terminator-only`).
- Net: 2199 → 2208 passing tests (+9 from the CDS start-codon
  regression suite; the GB 2.0 expansion contributed +41 from the
  parametrized iterations over `_GB_POSITIONS.keys()`).

### Tooling

- `scripts/perf_probe.py` — microbench for the data-clone /
  reverse-complement / restriction-scan / save-json hot paths. Reports
  median per-call ms across multiple library sizes.
- `scripts/perf_probe_render.py` — keystroke-to-paint bench for
  `_build_seq_text` under cursor-move / selection-change / per-char
  edit / rotation / 5-plasmid LRU-hop workloads. Reports cache
  occupancy alongside timing.

---

## [0.7.8.0] — 2026-05-10 — community contributions: map/seq resize + crash fix

> **Thank you, Harley King ([@har1eyk](https://github.com/har1eyk)),
> for both of these.** The closed PRs #7 and #8 sat in the repo for
> nearly two weeks because the branches were on v0.3.3 and the
> intervening rewrites made a literal merge impossible. The work was
> still real — this release lands the useful pieces of both, with
> credit going to the human who did the thinking.

### New: drag handle to resize the map/sequence split (PR #8)

- New `MapSequenceResizeHandle` widget sits as a 1-row strip between
  the top row (Library / Map / Sidebar) and the SequencePanel.
  Click + drag vertically to rebalance the split:
  - **Drag UP** grows the SequencePanel, shrinks the top row.
  - **Drag DOWN** shrinks the SequencePanel, grows the top row.
- Clamped on both ends — the seq panel can't go below 6 rows, and
  the top row can't shrink below 14 rows. No matter how aggressively
  you drag, the layout stays usable.
- **Persisted** across sessions: the final height after each drag
  lands in `settings.json` under `seq_panel_height`, hydrated back
  on the next launch. Same pattern as `show_restr` / `restr_unique_only`
  / etc.
- Drag handle's render-cost was the maintainer's biggest concern in
  the review of the original PR — every mouse-move tick was firing
  `app.refresh(layout=True)` which invalidated `PlasmidMap._draw_cache`
  (keyed on viewport height) and triggered a full braille re-render
  ~10×/sec. This implementation refreshes only the SequencePanel; the
  map's draw cache survives the drag and the resize stays smooth on
  50 kb plasmids.

### Fixed: `FetchModal` crash on NCBI fetch failure (PR #7)

- **Real bug that's been live since the modal was written**: when
  NCBI returns an error (timeout, 503, malformed payload, etc.), the
  error-rendering closure inside `_do_fetch` referenced `exc` directly.
  Python 3 deletes the `except` binding at scope exit, so by the time
  the closure ran later via `call_from_thread` it crashed with
  `NameError: cannot access free variable 'exc'`. Caught by Harley
  King in PR #7 with assist from ChatGPT-5.5 — fix captures
  `err_msg = str(exc)` before the closure is built. Thank you, Harley.

### Other cleanups (PR #7)

- `uv.lock` added to `.gitignore` — pure local artifact, shouldn't
  ship.
- `# noqa: E402` annotations on the Textual / Rich imports below
  `_log_startup_banner()`. The placement is intentional (banner
  setup runs before importing the heavy Textual stack so the cold-
  launch path is leaner), but lint tools rightly flag the order; the
  noqa makes the intent explicit.
- Multi-line `import fcntl, os, struct, termios` → one-per-line
  split (in `_detect_char_aspect`). Same for `urllib.request,
  urllib.parse` in `_ncbi_taxid_search`.
- Unused-import / unused-local cleanups: `AddFeatureModal._gather`
  no longer queries `#addfeat-strand-fwd` (the forward state is the
  fall-through default); `_rebuild_record_without_feature` no longer
  imports `FeatureLocation` (it reuses each feature's existing
  `feat.location` directly).

### Tests

- 5 new tests in `test_smoke.py::TestMapSequenceResize` covering:
  handle mount, mouse-down begins drag (end-to-end pilot routing),
  drag-up grows the seq panel, clamp holds below minimum,
  persistence to settings.json, hydration from settings on launch.
- Updated `TestAppBootstrap::test_all_panels_present` to assert the
  resize handle is part of the canonical widget set.
- Full suite: 2,140+ tests passing in ~5 min.

---

## [0.7.6.0] — 2026-05-08 — self-update + diagnostics + robustness pass

> Pre-1.0 sweep adding three connected operational surfaces — a
> cross-package-manager `splicecraft update` subcommand, a diagnostic
> bundle workflow for bug reports, and a 10-item robustness pass —
> plus six future-proofing scaffolds for the upcoming 1.0 freeze.
> NOT v1.0.0; the 1.0 tag remains gated on the commercial SaaS round-trip
> work + explicit user sign-off.

### New: `splicecraft update` subcommand

- Auto-detects the install method (pipx, uv tool, uv venv, pixi
  global, pip --user, pip in venv, system pip, editable install,
  source clone, pixi project) and runs the matching upgrade command.
- **Pre-update snapshot is sacred**: every running upgrade path
  takes a complete, atomic copy of the user's library, collections,
  parts bin, primers, feature library, custom grammars, codon
  tables, settings, crash-recovery autosaves, and `.dna` sidecars
  BEFORE invoking pip / pipx / uv / pixi. Stored at the sibling
  `<DATA_DIR>/../<DATA_DIR.name>-update-backups/` so a hypothetical
  bug that recursively wipes `_DATA_DIR` cannot destroy the recovery
  copy. Override location with `$SPLICECRAFT_UPDATE_BACKUP_DIR`.
- Refusal paths: editable + git-clone + pixi-project installs are
  read-only here (pull from git / run `pixi update splicecraft`
  yourself). System-wide pip prints the sudo command but never
  auto-runs sudo.
- `--check`, `--dry-run`, `--force`, `--yes`/`-y` flags for the
  expected variations.
- `--list-snapshots` and `--restore-pre-update [<id>|latest]` for
  recovering from a botched upgrade. Restore validates the
  manifest's schema_version, restricts `attr` to the published
  user-data whitelist, rejects path-traversal in `name` fields, and
  re-verifies SHA-256 before overwriting live data — all four
  checks are sacred and have dedicated regression tests.
- See CLAUDE.md invariant #39 for the complete contract.

### New: diagnostic logging + UI snapshot + bundle

- **Alt+D** captures a Markdown UI snapshot to
  `<DATA_DIR>/ui_snapshots/ui-snapshot-<ts>.md` containing app
  version + Python + platform, screen stack, focused widget, last
  mouse position, terminal size, current record metadata (id /
  name / topology / length / cursor / view origin / rotation /
  dirty — **never sequence content**), persisted settings, active
  collection / grammar, and a 200-line tail of the rotating log
  with home-directory paths scrubbed (`/home/<user>` → `~`).
  Defensive: every accessor wrapped in try/except so a half-mounted
  app can still capture. The previous Alt+D (seq-panel hover-debug
  toggle) moved to Alt+Shift+D.
- **`splicecraft logs --bundle [--out PATH]`** packs the rotating
  log files + last 5 UI snapshots + sanitized `settings.json` +
  `system_info.json` + a README into a single ZIP, atomically.
  Path scrubbing on every text artifact handles `/home/<user>`,
  `/Users/<user>`, `C:\Users\<user>`, plus `Path.home()` literal.
  Default filename `splicecraft-debug-<sessionID>-<ts>.zip` lands in
  CWD. The user attaches the ZIP to a bug report; no usernames leak.
- Rotating log bumped from 2 MB × 2 backups → 5 MB × 4 backups
  (~20 MB ceiling) for diagnostic depth.
- Structured event logs added to undo / redo, settings changes
  (with bounded `_repr_for_log` so a chatty toggle can't blow out
  the rotation window).
- See CLAUDE.md invariant #38 for the complete diagnostic surface.

### Robustness pass (10 items)

- **Multi-instance lock** at `<DATA_DIR>/splicecraft.lock` (POSIX
  `fcntl.flock` / Windows `msvcrt.locking`) refuses a second
  concurrent splicecraft against the same data dir — without it,
  two processes can desync on the in-memory library cache and
  silently overwrite each other's saves. Lockfile carries holder
  PID for the contention message. Bypass with
  `$SPLICECRAFT_SKIP_LOCK=1`.
- **`threading.excepthook` global hook** routes any unhandled
  worker exception through `_log.error` so a missed try/except in a
  background thread lands in the diagnostic bundle instead of dying
  silently.
- **0o600 perms** on the rotating log + every diagnostic bundle
  ZIP via `_chmod_user_only` (POSIX-only). Protects path/error
  metadata on multi-user hosts.
- **Settings type validation** (`_SETTINGS_SCHEMA` +
  `_validate_settings`): coerces wrong-typed settings.json values
  (`"yes"` for a bool, `True` for an int) back to the schema
  default + logs a warning. Strict bool-vs-int discrimination —
  `True` does NOT slip into an `int` field.
- **Worker drain at exit**
  (`_drain_in_flight_workers(timeout_s=2.0)`) joins non-daemon
  threads with the budget, logs leftovers so the diagnostic bundle
  reflects what was running.
- **One-retry network fetches** for both PyPI (update check) and
  NCBI (`fetch_genbank`) — 250 ms backoff between attempts.
- **Clipboard fallback chain**: app clipboard → OSC 52 → atomic
  file at `<DATA_DIR>/clipboard/<ts>-<label>.txt` (0o600) →
  log-only. Always logs the text so SSH-without-X11 sessions still
  have a way to retrieve copies.
- **Modal stack soft cap** of 12; refuses pushes past the cap
  with a no-op awaitable + warning notification (catches runaway
  `compose`/`on_mount` recursion).
- **Big-plasmid heads-up** when loading a record ≥ 5 Mb so the
  user knows render lag is expected, not a bug.
- **Daily snapshot per-file size cap** of 50 MB. A 1 GB library
  × 30 days = 30 GB of mostly-redundant snapshots; the existing
  `.bak` rotation + pre-update snapshots cover rollback for huge
  files. See CLAUDE.md invariant #37 for the complete list.

### Future-proofing scaffolds

- **Entry-level migration framework**: `_ENTRY_MIGRATIONS`
  registry (per-label `(from_v, to_v)` → callable) walks chained
  migrations on load so every consumer sees current-schema
  entries. Missing intermediate steps are no-ops; failed migrators
  preserve the entry + warn rather than drop user data.
- **PyPI URL override** via `$SPLICECRAFT_PYPI_URL` (validated
  http/https only; rejects `file://`, `javascript:`, etc.).
- **Manifest provenance**: pre-update snapshots record
  `from_python_version` + `from_platform` so cross-Python /
  cross-platform restores are diagnosable.
- **Data-dir version stamp** at `.splicecraft-data-version`
  detects downgrades (older SpliceCraft against newer data) and
  warns the user.
- **Plugin namespace reservation**: `<DATA_DIR>/plugins/` created
  empty at launch + included in `_USER_DATA_DIR_ATTRS` so future
  plugin data is auto-snapshotted. Reserved field name
  `_plugin_data` on entries; round-trip preservation tested.
- **Self-audit tests**: every `_*_FILE` constant is classified
  user-vs-operational; every `_INSTALL_METHODS` entry is
  buildable-or-refused; every method appears in the help text. A
  contributor adding a new persisted file or install method now
  trips an explicit test rather than silently shipping a gap.
- See CLAUDE.md invariant #36 for the complete list.

### Test coverage

- 122 new tests (24 robustness + 39 diagnostics + 36
  future-proofing + 23 update flow). Targeted suite (`test_smoke`
  + `test_dna_sanity` + `test_data_safety`) now runs 631 tests in
  ~1m54s. Full suite ~5 min on 8 cores.

### Documentation

- 4 new sacred invariants in CLAUDE.md (#36 future-proofing, #37
  robustness, #38 diagnostics, #39 update-snapshot). Cross-
  references the test classes that protect each invariant.

---

## [0.7.5.0] — 2026-05-08 — audit + hardening sweep

> Pre-1.0 readiness sweep — bug fixes, cache discipline, worker
> safety, doc refresh. NOT v1.0.0; the 1.0 tag is gated on the
> commercial SaaS round-trip work landing in full and explicit user
> sign-off, neither of which is in this sweep.

### Bug fixes

- **Traditional cloning: cursor → entry mismatch.** `_record_for_table_row`
  AND `_current_source_entries` in `TraditionalCloningPane` now apply
  the same natural sort that `_populate_library_tables` uses for
  display. Pre-fix the reload was unsorted, so a click on display row
  N digested whichever plasmid happened to land at on-disk position N
  — and the construction-history XML recorded the wrong vector as a
  parent. Sacred invariant: any screen that sorts a `DataTable` for
  display must resolve every `cursor_row` lookup against the same sort
  (now codified in CLAUDE.md as invariant #33).
- **Cache poisoning on save.** `_save_library`, `_save_parts_bin`, and
  `_save_primers` now `deepcopy(entries)` when re-seating the in-memory
  cache. Pre-fix the cache shared dict refs with the caller, so a
  caller that kept editing `entries` after save (e.g. cancelled modal
  follow-up edits) leaked post-save mutations into the next reader.
  `_load_library` also now deepcopies on read for symmetry with the
  other `_load_*` helpers (sacred invariant #17 made stricter).
- **Library delete / collection switch silently swallowed disk errors.**
  `LibraryPanel._request_plasmid_delete`'s confirmation callback and
  `_coll_row_selected`'s collection-load path now wrap `_save_library`
  in `try / except OSError` and surface the failure with
  `severity="error"` instead of letting the exception bubble into the
  Textual event loop. The user now sees a notification on disk-full /
  RO mount / permission-denied rather than a silent no-op.

### Performance + UX

- **`PartsBinModal._load_part` runs in a worker.** The Type IIS digest
  loop in `_classify_part_from_plasmid` now executes inside
  `_load_part_worker` (`@work(thread=True)`) so a multi-grammar digest
  on a 50 kbp plasmid no longer freezes the modal for 200–500 ms. The
  pre-flight checks (record present, circular topology) still run on
  the UI thread so warning toasts are immediate. UI updates inside
  the worker bounce through `call_from_thread`.
- **`FeatureLibraryScreen` cursor restore is O(1).** Added
  `_entry_idx_to_row` reverse dict alongside `_row_to_entry_idx`.
  Pre-fix `_repopulate_table` did `_row_to_entry_idx.index(...)`
  (O(N)) on every restore — now O(1) regardless of feature library
  size. The silent fallback (selected_index outside the row map) now
  logs a warning so the underlying drift surfaces in dev mode.

### Documentation

- **CLAUDE.md** refreshed:
  - line count corrected from ~32k to ~39k.
  - test runtime corrected from ~3 min to ~5–6 min for the full suite.
  - sacred invariant #17 made stricter: cache helpers now deepcopy on
    BOTH read and save (not just read).
  - new invariants #33 (natural-sort row-mapping symmetry across the
    7 surfaces that now sort), #34 (`_classify_part_from_plasmid`
    Type IIS digest pattern + worker requirement), #35 (CommercialSaaS
    `.dna` writer's full default packet inventory).
- **README.md** refreshed: 1700+ test count, 35 sacred invariants,
  4-layer data-safety net spelled out, Restore from backup flow
  surfaced, `.dna` round-trip + construction history mentioned, Load
  Part button described, pairwise-alignment / Plasmidsaurus ingestion
  surfaced, cross-collection plasmid search highlighted.

### Tests

- 4 traditional-cloning tests (`test_traditional_pane_pcr_mode_simulate`,
  `test_traditional_pane_save_forward_to_library`,
  `test_traditional_pane_save_records_history_xml`,
  `test_save_buttons_redisable_on_input_change`) updated to match the
  new sort-aware semantics — pre-fix they computed `target_idx` from
  disk order and accidentally passed because the underlying bug
  mirrored the test's own bug.
- 2 new modal-boundary cases (`DropdownScreen`,
  `GrammarEditorModal.builtin`) bring the boundary suite to 56
  modals at 160×48.

### Punch-list follow-ups (audit cleanups)

- **`_search_collections_library` switched to `heapq.nsmallest`** for
  O(N + limit·log(limit)) search ordering. The pre-fix `out.sort()`
  was O(N·log(N)) and noticeable on libraries beyond ~1k plasmids;
  for a 5k catalog the heap form saves ~30 ms per search keystroke.
  Same returned ordering — only the asymptotic complexity changed.
- **Defense-in-depth response cap on the agent API.** Added
  `_AGENT_RESPONSE_MAX_BYTES = 50 MB` (mirrors the read-side
  `_SAFE_LOAD_JSON_MAX_BYTES` / `_BULK_IMPORT_MAX_BYTES`).
  `_AgentRequestHandler._send` now refuses to ship any response above
  the cap — returns a 500 with `{body_too_large: true, body_bytes,
  cap_bytes}` so the client can branch (re-query with a tighter
  filter) instead of blocking the worker thread on a multi-MB serialise
  + loopback write. Catches the unbounded-list class of bugs in any
  future agent endpoint without rewriting per-handler caps.
- **`PartsBinModal` design rule documented in code.** Added a paragraph
  to `_populate` spelling out *why* this modal sorts `_rows` in-place
  (without a separate `_row_to_part_idx` mapping) and what would have
  to change before any code path could mutate `self._rows` mid-flight
  — surfaces sacred invariant #33's reasoning at the call site so a
  future contributor doesn't accidentally introduce the same class of
  bug `TraditionalCloningPane` had.
- **`from copy import deepcopy` hoisted to module level.** Replaced 30
  function-local imports (and 2 `_shallow_copy` ones) with a single
  `from copy import copy as _shallow_copy, deepcopy` at the top.
  Python caches the import either way; the consolidation just makes
  the deepcopy / shallow-copy contract immediately visible from the
  imports block instead of scattered through the file.

---

## [0.7.4.4] — 2026-05-07

### Hardening — origin rotation clamp on sequence shrink

- ``SequencePanel.update_seq`` now clamps ``_view_origin_bp`` to
  ``% len(seq)`` (or 0 for empty seq) at the canonical sequence-change
  entry point. Pre-fix the only clamp lived in ``set_view_origin``
  (called on rotation), so an edit-then-shrink path that bypassed the
  ``pm.load_record`` reset could leave ``_view_origin_bp > len(seq)``,
  silently degrading ``_get_rotated_state`` (no rotation visible, but
  feature shifts mis-aligned). The "rotation survives across edits"
  semantic is preserved — non-shrinking edits still leave the rotation
  untouched.
- ``SequencePanel._get_rotated_state`` defensively re-clamps origin
  via ``% n`` on entry as a belt-and-braces safety net for any future
  path that might leave the origin stale before render.
- Cleaned up a multi-paragraph comment in ``_MutPreview.on_click``
  (landed earlier today for the CSS-gutter off-by-one fix) to a
  single line per CLAUDE.md style.

### Tests

- 2 new ``test_smoke.py::TestOriginRotationCascade`` cases pin the
  shrink-clamp + the render-path re-clamp.

---

## [0.7.4.3] — 2026-05-07

### Fixed

- **Mutagenize AA click is no longer off by one amino acid.**
  ``_MutPreview.on_click`` was using ``event.screen_x - self.region.x``
  which includes the widget's CSS ``border: solid`` + ``padding: 0 1``
  (4 cols of horizontal chrome, 2 on each side); the resolved column
  was 2 cols right of the click target so each click landed on the
  codon ~one AA to the LEFT of where the user actually clicked. The
  handler now reads ``event.x`` / ``event.y`` directly — Textual
  reports those in widget-content coordinates, so the click coord
  matches the rendered AA position exactly. Same fix applies to clicks
  on the lane art (label / bar) and DNA-row rows; they all share the
  ``_click_to_aa`` codon math.
- 1 new end-to-end regression test in
  ``tests/test_codon.py::TestMutagenizeClickAlignment`` mounts the
  modal under a real CSS context, drives synthetic ``Click`` events
  with content-relative ``event.x`` for codons 0 / 1 / 2, and asserts
  the cursor lands on the clicked AA.

---

## [0.7.4.2] — 2026-05-07

### Changed — Mutagenize preview + Feature Library snippet share SequencePanel pipeline

- **Mutagenize CDS preview now renders via ``_build_seq_text``.** The
  ``_MutPreview`` widget previously rolled its own DNA + AA renderer
  (``_mut_build_preview_text``); per user request it now synthesizes a
  full-span CDS feature and hands the visualization off to the same
  ``_chunk_layout`` / ``_paint_feature_label`` / ``_paint_feature_bar``
  / ``_paint_cds_aa`` helpers the main SequencePanel uses. Each chunk
  now renders as label + bar + AA + fwd DNA + rev DNA + trailing
  blank — consistent stacking and layout logic across the app.
  The cursor codon is marked via ``user_sel`` (3-bp subtle white bg);
  a designed mutation is marked via ``sel_range`` (3-bp bold +
  underline). AA-only mode (protein source before optimization) keeps
  its compact one-line-per-row render. Click any cell within a codon's
  column to place the cursor — the AA-click → codon mapping math is
  pinned by ``_MutPreview._click_to_aa`` and the row count per chunk
  is constant (6 rows; lane art always present, no wrap).
- **Feature Library snippet panel ("``_FeatureSnippetPanel``") line
  width tracks terminal width.** Pre-fix used a hardcoded
  ``line_width=60`` so the lane art never expanded past the leftmost
  60 cols of the modal regardless of terminal size; on wide terminals
  that left the right half of the preview empty. The panel now reads
  its own widget width on mount and on every resize and recomputes
  ``line_width`` accordingly.
- **Modal preview pane vertical room.** ``#mut-preview`` ``max-height``
  bumped 10 → 18 so ~3 chunks of the new render fit comfortably; the
  pane still scrolls for longer CDSes.
- **Removed dead helpers.** ``_mut_build_preview_text``,
  ``_mut_click_to_aa_index``, and ``_MUT_PREVIEW_MUT_COLOR`` had no
  remaining callers after the refactor; deleted along with the old
  unit tests in ``tests/test_mutagenize.py::TestPreviewText`` /
  ``::TestClickToAA`` (both classes rewritten to exercise the
  ``_MutPreview`` render path directly).

### Hardening

- **CDS label sanitized.** ``_MutPreview.bind_content`` now routes the
  incoming ``cds_label`` through ``_sanitize_label`` (max 64 chars) so
  a parts-bin / protein-source name with embedded ``\\x1b`` / NUL /
  newline / BEL bytes can't smuggle terminal escape sequences into the
  lane art rendered via ``_paint_feature_label``.
- **Mutation codon length is strict.** ``_recompute_display`` now
  requires ``len(mut_codon) == 3``; pre-fix a 2-nt or 4-nt mutant
  codon would shift every downstream codon's reading frame after the
  splice (``dna[:lo] + mut_c + dna[lo+3:]`` extends / shrinks the
  CDS), silently corrupting the protein for the rest of the visible
  preview.
- **``line_width`` clamped to ``[20, 500]``.** Both ``_MutPreview``
  and ``_FeatureSnippetPanel`` ``_refresh_line_width`` cap an
  unrealistic super-wide widget at 500 cols so a pathological resize
  can't blow up ``_build_seq_text``'s per-row arrays. The ``except
  Exception`` around the ``self.size.width`` read is also narrowed to
  the actual failure modes (``AttributeError`` / ``TypeError`` /
  ``ValueError``).
- **Synth-feats list + dict identity preserved across cursor moves.**
  ``_recompute_display`` mutates the existing dict via ``dict.update``
  instead of reassigning ``list[0]``; both list ID and dict ID stay
  stable so the size-4 ``_BUILD_SEQ_CACHE`` / ``_CHUNK_LAYOUT_CACHE``
  hit on every cursor scroll instead of churning the main
  SequencePanel's entries out of cache. Length / sequence changes
  still flip the cache key (``len(seq)`` + ``hash(seq)`` are part of
  it) so styles get recomputed when needed; label-only swaps land on
  the same dict so cached references see the new value.
- **AA-only mode reassigns ``_synth_feats`` instead of clearing in
  place.** The DNA → AA-only transition now does
  ``self._synth_feats = []`` rather than ``del self._synth_feats[:]``
  — list ID changes so any stale ``_BUILD_SEQ_CACHE`` /
  ``_CHUNK_LAYOUT_CACHE`` entries from the prior DNA mode that still
  hold ``annot_feats`` references to the old dict can't return a
  stale hit if the next DNA load lands at a colliding ``hash(seq)``.
- **``_FeatureSnippetPanel._render_dna`` line_width also capped at
  500.** Defensive even though the panel's own ``_refresh_line_width``
  already enforces the cap; an external caller passing an unbounded
  ``line_width`` can't blow up ``_build_seq_text``'s per-row arrays.
- **Dead ``rerender`` kwarg removed from
  ``_MutPreview._refresh_line_width``.** The parameter was never
  inspected; callers always read the bool return and dispatched
  ``_render_and_update`` themselves.
- 8 new tests in ``tests/test_mutagenize.py::TestPreviewHardening``
  pin the sanitization, codon-length strictness, line_width cap,
  list-identity contracts (across cursor moves AND across DNA
  ↔ AA-only transitions), and dict-identity stability for label-only
  swaps within DNA mode.

---

## [0.7.4.1] — 2026-05-07

### Fixed

- **Cursor + scroll snap to the new origin on rotation.** Previously
  rotating the map (Alt+O / ← / → / wheel) updated the seq panel's
  display rotation and the sidebar order, but left the cursor at
  its old absolute position and the seq panel scrolled wherever
  the user had been reading. Now ``set_view_origin`` snaps the
  cursor to the rotated view's first base (= absolute bp
  ``origin_bp``), clears feature highlight + drag selection (they
  pointed at positions valid only under the previous rotation),
  and scrolls the seq panel to display row 0 so the new starting
  base is at the top — matches the semantic of ``Home`` (reset
  origin → seq panel back to top).
- 2 new regression tests in ``test_smoke.py::TestOriginRotation
  Cascade`` covering the cursor snap + scroll behaviour.

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
  commercial SaaS .dna round-trip remains the long-pole.

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

- v1.0.0.0 scope status: FASTA export ✓, GFF export ✓, diff view ✓, ORF finder ✓, annotation transfer ✓, cross-collection search ✓, commercial SaaS .dna round-trip (in flight), stability gate (next).

---

## [0.6.0.0] — 2026-05-06

### Added

- **Whole-plasmid FASTA export** — File → Export as FASTA (.fa)…  Pushes `FastaExportModal` (already used by the feature-library + parts-bin export flows) pre-populated with the loaded record's name + sequence. The `_h_export_fasta` agent endpoint already existed; this wires the GUI front-door.
- **ORF finder** — Edit → Find ORFs… opens `ORFFinderModal` showing every six-frame ORF over the loaded record. Configurable min length (default 30 aa) + opt-in alternative bacterial starts (GTG / TTG). Wrap-aware on circular plasmids: ORFs crossing the origin are reported with `end < start` matching the existing wrap-feature convention. Row pick highlights the ORF in the seq panel + map. New `_find_orfs` helper + `_h_find_orfs` agent endpoint.
- **Cross-collection plasmid search** — File → Find plasmid (all collections)… opens `LibrarySearchModal` with a fuzzy-matched live-filtered table of every plasmid across every collection on disk. Selecting a row switches the active collection (if needed) and loads the plasmid through the existing `_apply_record` flow. New `_search_collections_library` helper + `_h_search_library` agent endpoint.

### Tests

- 26 new tests covering `_find_orfs` (forward / reverse / wrap / alt-starts / dedupe), `action_export_fasta`, `_search_collections_library`, `action_find_plasmid`, the new agent endpoints (`find-orfs`, `search-library`), and modal-fits-in-baseline-terminal coverage for `ORFFinderModal` and `LibrarySearchModal`.

### Roadmap

- v1.0.0.0 scope locked: FASTA export ✓, GFF export, diff view, ORF finder ✓, annotation transfer, cross-collection search ✓, commercial SaaS .dna round-trip (in flight), stability gate. No CLI — every new feature ships an agent-API endpoint instead.

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
