# Architecture

How SpliceCraft is organized, why, and how to navigate it.

## Hub + layered siblings

SpliceCraft began as a single-file app and is now a **hub + layered-siblings**
layout on Textual + Biopython. `splicecraft.py` is the ~99k-line **hub**
(`PlasmidApp`, the data-safety POLICY, the `.dna` blob store, CLI, agent-API,
screens, and the app-coupled modals/widgets — the deeply-coupled application
core, deliberately kept together; extracting `PlasmidApp` + its three main panels
would cascade into the data-safety core). The cleanly-separable layers live in
flat `splicecraft_*.py` siblings the hub imports and re-exports:
`splicecraft_biology` (pure biology + the restriction-site scanner / enzyme
digest — sacred invariants #1/#2/#6 — reading its caches + catalog through
`_state` getters), `splicecraft_state`, `splicecraft_logging` (`_log` /
`_log_event` + the `_action_log` / `_timed` decorators),
`splicecraft_persistence` (the domain-agnostic save/load engine + data-safety
chokepoint), `splicecraft_dataaccess` (every domain `_load_X`/`_save_X` accessor
+ grammar/enzyme data; the migrations/mirrors stay hub-side via `_state` hooks),
`splicecraft_record` (GenBank↔SeqRecord serialization), `splicecraft_util` (pure
cross-cutting helpers), `splicecraft_net` (SSRF-hardened network primitives),
`splicecraft_codon` (the mission-critical codon optimizer), `splicecraft_primer`
(primer / site-directed-mutagenesis design), `splicecraft_search` (online
BLAST / HMMER + the HMM-DB downloader), `splicecraft_cloning` (construction
simulation + the PCR caps), `splicecraft_seqanalysis` (ORF finder + part
classifier),
`splicecraft_gels` (agarose-gel sim/render — `[SUB-gels]`),
`splicecraft_experiments` (lab-notebook entry processing — `[SUB-experiments]`),
`splicecraft_fileio` (single-file sequence-format I/O — FASTA/GenBank/GFF/AB1/FASTQ),
`splicecraft_backup` (the user-data backup / restore / migrate engine + Master
Delete enumeration — the data-safety core), `splicecraft_render`,
`splicecraft_history`, `splicecraft_widgets`, `splicecraft_modals` (60
dependency-clean dialog classes), `splicecraft_agent` (all 107 data-only
agent-API endpoints; the deep engines they call are reached via `_state` hooks),
`splicecraft_errors` (plus the stdlib-only `splicecraft_cli` sidecar and
`splicecraft_demo_plasmids` seed). **The flat-sibling modularization is now
essentially complete** — the hub went from ~131k to ~99k lines (−25%) across 24
siblings; only the God-class application core (PlasmidApp + the big-3 panels +
their anchored modals/handlers + the blob writers + the pyhmmer BLAST/HMM engine)
stays hub-side, which would need in-App decomposition rather than a lift.

The hub stays greppable as one totally-ordered file; the siblings are bounded,
independently-loadable units (the goal: a context-limited model can hold one
whole). Siblings are layered L0→L7 with **no upward imports** and re-exported so
`import splicecraft as sc; sc.<name>` resolves unchanged.
Mutable-state siblings
are accessed by attribute (`_state.X`) so hub, siblings, and tests share one
copy — a by-value `from splicecraft_state import X` binds a stale copy and is
forbidden.

Guards: `tests/test_import_layers.py` (no cycles / upward imports; every sibling
packaged in `pyproject.toml`), `tests/public_surface_baseline.json` (surface
byte-for-byte), `tests/test_state_module.py` + the per-sibling behaviour tests.
A new sibling MUST be added to `pyproject.toml`'s wheel + sdist lists or the
released wheel breaks at the re-import step.

`grep -rn "^class \|^def " splicecraft*.py` gives an authoritative live map.

Test files are 1:1 named after the subsystem they cover.

## File layout (top-to-bottom)

`splicecraft.py` is laid out by concern, top-down:

1. Imports + persistence helpers + path resolution
2. Logging primitives (`_log_event`, `_action_log`, `_timed`)
3. Enzyme catalog + IUPAC + scanner + 2D feature packer + seq-panel
   renderer
4. GenBank I/O
5. `_Canvas` / `_BrailleCanvas` / `PlasmidMap` / `FeatureSidebar`
6. `LibraryPanel`
7. `SequencePanel`
8. Core modals
9. Grammars + settings
10. Codon registry + Kazusa + mutagenesis
11. Feature-library workbench
12. Parts bin
13. Domesticator + Constructor
14. Mutagenize modal
15. Primer design
16. Small modals
17. `PlasmidApp` (controller, keybindings, undo stashes, autosave,
    `@work` threads)
18. `main()`

## The agent handover document

`CLAUDE.md` at the repo root is the contributor-and-agent handover
document. It contains:

- **41 numbered sacred invariants** — biology correctness, persistence
  contracts, concurrency rules, UI conventions, lock-file
  hardening, the `.dna` writer's expected packet inventory. Touching
  invariant code without updating its regression test will trip the
  test in under two seconds.
- **Known pitfalls** — wrap features, `id()` cache keys, Textual
  reactive auto-invalidation, the `_source_path` survival rule across
  in-place edits, Primer3's linear-only constraint, etc.
- **Persistent user preferences** — the conventions for adding a new
  `settings.json` toggle (4 mechanical steps).
- **Pairwise alignment + Plasmidsaurus ingestion** — the two-stage
  pipeline (size caps + alignment).
- **Architecture pointers** + grep recipes.

**Read it before touching the rendering layer, record pipeline,
primer design, or any persisted-data save path.**

## Why one file

The constraint started as a personal preference and has held up under
scrutiny:

- **Greppability.** Every callsite, every state mutation, every error
  path is reachable with one grep. No "find usages" gymnastics
  across packages.
- **No import puzzles.** New contributors don't need to learn the
  module layout. New subsystems can land without any package-graph
  thinking.
- **Editor responsiveness.** Modern editors handle 60k LoC files
  fine; the trade-off is that the LSP / type-checker re-checks the
  whole file on edit, which is acceptable when the suite runs in
  ~5 minutes anyway.
- **Refactor cost is real.** The cost is internalised, not externalised.
  See [V1_GATE.md](
  https://github.com/Binomica-Labs/SpliceCraft/blob/master/V1_GATE.md)
  soft gate S3 / S6 for the long-term plan.

When the constraint will be reconsidered: when the file passes
~100k LoC, or when a subsystem with no `PlasmidApp` coupling
appears that benefits clearly from extraction. See `CONTRIBUTING.md`
for the three-test rule on extractions.

## Test pyramid

| Suite                                  | Use                                              | Runtime |
|----------------------------------------|--------------------------------------------------|---------|
| `tests/test_dna_sanity.py`             | Inner loop while iterating on biology code       | < 2 s   |
| `tests/test_commercialsaas_io.py`      | When touching `.dna` reader / writer             | ~30 s   |
| `tests/test_agent_api.py`              | When touching `_h_*` endpoints                   | ~45 s   |
| `tests/test_smoke.py`                  | End-to-end + update / restore flows              | ~90 s   |
| `tests/test_perf_regression.py`        | Best-of-N regression gates (perf-baseline.json)  | ~3 s    |
| `tests/test_cli_client.py`             | splicecraft-cli sidecar                          | ~2 s    |
| Full suite (`pytest -n auto -q`)       | Before commit, before release                    | ~5 min  |

All tests run offline against synthetic `SeqRecord`s and monkeypatched
data paths; an autouse fixture in `tests/conftest.py` guarantees no
test can write to real user files.

## Concurrency model

- **`@work(thread=True)` workers** for everything heavier than the
  16 ms frame budget: restriction scan, BLAST, HMMscan, Primer3,
  pairwise align, Gibson sim, GB cycle, classifier digest.
- **Stale-record guard**: workers capture
  `_record_load_counter` at entry; the post-work callback bails out
  if the canvas has moved on.
- **Worker exclusivity**: `@work(exclusive=True, group=...)` for
  user-driven modal flows so a click-spam can't pile up superseded
  work.
- **RLock-protected saves**: `_cache_lock` serialises every `_save_*`
  + cache reassignment so two concurrent saves can't land
  `os.replace A→B` while caches land `B→A`. RLock because save
  chains nest.

## Observability

Every save / load / migration / network / lock / shutdown emits a
structured event via `_log_event(event, **fields)`:

```
app.<area>.<verb>      # user actions
op.<area>.<verb>       # operations
<noun>.<verb>          # state (save.ok, record.loaded, migration.step)
```

Sequence content is **never** logged — `_repr_for_log` truncates and
tags anything DNA-shaped.

Design target: **user pastes log → AI parses → patch shipped same
loop**. The agent-friendly event taxonomy is documented in `CLAUDE.md`
invariant #42.
