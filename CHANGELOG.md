# SpliceCraft Changelog

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
- Save flow mirrors the agent-API `_h_update_feature` endpoint so the UI and the API can't drift — both rebuild the SeqRecord via `deepcopy` + per-feature mutation, push undo, and refresh all panels. Color persists via CommercialSaaS/Benchling `ApEinfo_fwdcolor` / `ApEinfo_revcolor`; notes via the standard GenBank `/note` qualifier.

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

- **Bulk CommercialSaaS / GenBank import** — clicking `+` on the LibraryPanel collections view opens a redesigned `NewCollectionModal` with an embedded `DirectoryTree`; pick a folder, click "Create", and every `.dna` / `.gb` / `.gbk` / `.genbank` file inside is loaded into a fresh collection. Per-file failures isolated; notify summary calls out counts. Designed so a CommercialSaaS archive migrates in one shot.
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

## [Unreleased]

### Added

- **CommercialSaaS (.dna) file import** — `File → Open` and the `o` hotkey
  now accept CommercialSaaS's native binary `.dna` format via Biopython's
  built-in parser. No manual GenBank export step required. Files are
  dispatched by extension (`.gb`, `.gbk`, `.genbank` → GenBank;
  `.dna` → CommercialSaaS), case-insensitively. Malformed `.dna` files
  produce a user-friendly error pointing to the likely cause.

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
