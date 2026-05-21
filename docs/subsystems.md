# Subsystems — feature-area deep dives

Companion to `CLAUDE.md`. Holds the long-form documentation for self-contained subsystems whose details are only relevant when touching that area.

## How agents should use this file

**Before editing one of these subsystems, grep this file for the tag.** Each section carries a `[SUB-xxx]` anchor + topic keywords.

```bash
grep -n '\[SUB-plasmidsaurus\]' docs/subsystems.md
grep -ni 'experiment\|project\|gel' docs/subsystems.md
```

## Tag index (grep table)

| Tag | Topic keywords |
|---|---|
| `[SUB-plasmidsaurus]` | Plasmidsaurus zip ingestion; pairwise alignment; SequencingScreen; sub-tabs |
| `[SUB-experiments]` | Experiments lab-notebook; projects; cross-refs `@`/`!`/`&`; spellcheck; ImageAttachModal |
| `[SUB-gels]` | Gels; gels.json; GelLibraryModal; SimulatorScreen |

---

## [SUB-plasmidsaurus] Plasmidsaurus ingestion + pairwise alignment (0.5.3+, sub-tabs 0.9.5+)

Two-stage pipeline:

1. **Zip ingestion** — `_list_gbk_members_in_zip(path)` lists `.gbk`/`.gb`/`.genbank`; `_extract_gbk_member(path, name)` reads one out. Size-capped (`_PLASMIDSAURUS_ZIP_MAX_BYTES = 500 MB`, `_PLASMIDSAURUS_MEMBER_MAX_BYTES = 50 MB`, `_PLASMIDSAURUS_MAX_MEMBERS = 2000`).

2. **Structured parse** — `_parse_plasmidsaurus_zip(path)` groups per sample (`{gbk, fasta, summary, perbase, histogram, coverage_plot, interactive_map, ab1_files, summary_text, perbase_coverage}`). Run-level extras (`<run>_gel.png`, README) → `run_files`. Category folders matched on `_<suffix>` anchor (`_genbank-files`, `_fasta-files`, `_summary-files`, `_per-base-data`, `_histograms`, `_coverage-plots`, `_interactive-map`, `_ab1-files`); run-ID by majority vote. Standalone `.gbk` outside category folders still surfaced. Summary bodies (≤`_PLASMIDSAURUS_SUMMARY_MAX_BYTES = 4 KB`) streamed inline; per-base TSVs stream-summarised into `{mean, min, max, n_pos, above_20x}`.

3. **Alignment** — `_pairwise_align(query, target, mode='global'|'local')` wraps `Bio.Align.PairwiseAligner`. Returns `{mode, score, identity_pct, aligned_q, aligned_t, n_matches, n_mismatches, n_gaps, q_len, t_len}`. Capped at `_PAIRWISE_MAX_LEN = 200_000`. **Aligned strings come from `Alignment[0]`/`Alignment[1]`**, NOT `format()`-parsing.

Entry: `Sequencing → Plasmidsaurus` on `SequencingScreen`. Nested `TabbedContent` with 4 sub-sub-tabs: **General** (always enabled, `_ZipAwareDirectoryTree` + overview), **Samples** (DataTable `#align-members`, row keys carry gbk member name), **Quality** (`#plasmidsaurus-quality-table` + `#plasmidsaurus-runfiles-table`), **Align** (query + target Select `#align-target` + `#btn-align-go`). Gating via `_apply_subtab_gating(enabled)`. Same-path re-pick short-circuits at top of `_on_zip_picked`. `PlasmidsaurusAlignModal` module-level alias of `SequencingScreen` for back-compat.

Hardening (0.9.4):
* **Per-base TSV zip-bomb defence** — `_PLASMIDSAURUS_PERBASE_MAX_BYTES = 100 MB` two-layer cap (central-directory check + chunked `codecs.getincrementaldecoder` 64 KB).
* **Single-pass zip-open** — `_batch_extract_gbk_meta` reads every sample in one `ZipFile` open.
* **NUL-anchored sentinels** — `_NO_GBK_KEY_PREFIX = "\x00no-gbk\x00"`, `_EMPTY_LIBRARY_SENTINEL = "\x00no-library\x00"` (collision-proof).

## [SUB-experiments] Experiments lab-notebook (0.9.6+, projects refactor 0.9.7+)

Top-level (Menu → Experiments) → `ExperimentProjectsPickerModal` first (mirrors `PartsBinPickerModal` → `PartsBinModal`). Picking pushes full-screen `ExperimentsScreen`. Split-pane:

* **Top row** — active project label + `Projects… [^P]` button (Open/New/Rename/Duplicate/Delete/Close).
* **Left pane** — entries DataTable `Updated` + `Title`, natural-sort `updated_at` desc. Width 1fr (~20%) `min-width: 24`. New/Open/Rename/Delete.
* **Right pane** — `TabbedContent[Compose | Attachments]`. Disabled until entry loaded.
  * **Compose** — `TextArea` markdown source. Live preview dropped 2026-05-18; `_render_plasmid_refs` preserved for export.
  * **Attachments** — per-entry image grid via `ImageAttachModal`; Win/Mac clipboard paste via `Pillow.ImageGrab.grabclipboard()` (Linux/WSL disabled — no pure-Python API).

**Projects layer (projects:experiments :: collections:plasmids):** `experiment_projects.json` holds named projects, each with `experiments: list[dict]`.
* `_load_experiment_projects`/`_save_experiment_projects` — cache + `_cache_lock` + deepcopy.
* `_get_active_project_name`/`_set_active_project_name` — `settings["active_project"]`.
* `_ensure_default_project` — first-run wraps existing `experiments.json` into `_DEFAULT_PROJECT_NAME = "Main Project"`. Called from `compose()` per pitfall #9.
* `_sync_active_project_experiments` — called by `_save_experiments` after every save.

**Sacred — Experiments mirror:** every save MUST go through `_save_experiments` → `_sync_active_project_experiments(entries)`. Routing around bypasses mirror (same threat as pitfall #10).

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

## [SUB-gels] Gels (saved agarose-gel snapshots, 2026-05-19+)

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
