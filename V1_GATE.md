# SpliceCraft v1.0.0 acceptance gate

The `0.x` line is a sustained beta with weekly-to-daily releases. The
`1.0.0` tag carries different promises — API stability, semantic
versioning, longer support window — and so the gate to cut it is
deliberately strict.

> **Maintainer-approval gate.** Meeting every criterion below does
> NOT auto-clear the gate. The `1.0.0` tag is cut only with explicit
> maintainer approval, even if everything on this list is green.
> This file is the *checklist*, not the *trigger*.

## Target date

**2026-05-24** is the working deadline. It is not a commit. If
criteria slip, the date slips.

## Hard gates (must all be green)

| # | Criterion | How we check it |
|---|---|---|
| 1 | Zero open `bug`-labelled issues marked `severity:high` or `severity:critical` | `gh issue list --label "bug,severity:high" --state open` returns empty |
| 2 | Full test suite green on every Python version in CI matrix (3.10–3.13) | `gh run list --workflow=test.yml --branch=master --limit=1` shows success |
| 3 | All 41 sacred invariants (`CLAUDE.md`) have at least one regression test | manual audit + invariant-to-test crossreference grep |
| 4 | Pyright strict passes on `splicecraft.py` and `splicecraft_cli.py` | `pyright` exits 0 |
| 5 | `ruff check` passes | `ruff check .` exits 0 |
| 6 | Coverage ≥ 80% on biology primitives (`_scan_restriction_sites`, `_rc`, `_iupac_pattern`, `_translate_cds`, `_feat_len`, `_bp_in`, `_rebuild_record_with_edit`) | `coverage report --include="splicecraft.py" --fail-under=80` after the targeted run |
| 7 | All 8 terminals in `RELEASE_CHECKLIST.md` smoke matrix pass | physical sign-off in the checklist for the release candidate |
| 8 | Pre-update snapshot + restore round-trip verified on a real user data dir | manual run of `splicecraft update --restore-pre-update latest` |
| 9 | Bundled `.dna` round-trip verified against the commercial editor's free viewer | manual run on `AB303066.dna` |
| 10 | README screenshot is current within the release candidate | visual diff against the current `splicecraftScreenshot.png` |
| 11 | CHANGELOG entry for `1.0.0` reads as a *coherent* release narrative — not a commit dump | maintainer review |
| 12 | Public docs site published (mkdocs on GitHub Pages, or equivalent) | `https://binomica-labs.github.io/SpliceCraft/` resolves |
| 13 | `SECURITY.md` disclosure channel tested end-to-end | maintainer self-test |
| 14 | No `TODO` / `FIXME` / `HACK` markers in `splicecraft.py` or `splicecraft_cli.py` | `grep -nE "TODO|FIXME|XXX|HACK" splicecraft.py splicecraft_cli.py` returns empty |

## Soft gates (strongly preferred, not blocking)

| # | Criterion | Why |
|---|---|---|
| S1 | One non-maintainer code reviewer has signed off | Bus-factor mitigation |
| S2 | At least one merged community PR in the `0.x` history | Sanity check on the contribution path |
| S3 | `_run_update_subcommand` (currently 532 lines) and the other 200+-line functions are below 200 lines each | Reviewability |
| S4 | Conda-forge / bioconda recipe has been accepted upstream | Reach beyond PyPI |
| S5 | Performance regression suite (`tests/test_performance.py`) has CI-asserted baselines, not just smoke-runs | Catch silent perf cliffs |
| S6 | `PlasmidApp` (currently 156 methods) factor-OR-document decision **resolved 2026-05-17**: mixin extraction declined as cosmetic — controllers cut through call chains, not around them. See `PlasmidApp.__doc__` for the full rationale. v2.x viewmodel layer is the proper-decoupling project. | Long-term maintainability |
| S7 | Issue templates have been used at least once by a real reporter | Validates the channel |
| S8 | Conda recipe has an open PR or merged PR in bioconda-recipes | Documents the canonical-copy promise |

## What v1.0.0 *commits us to*

These are the post-1.0.0 promises a maintainer should be prepared
to keep, and the reason the gate is strict:

- **Semantic versioning.** Breaking changes require a major bump
  (`2.0.0`). Behaviour changes inside a minor bump must be
  backward-compatible at the file-format, agent-API, and CLI levels.
- **Two-version support window.** Bugs filed against `1.x.y` get a
  fix in the latest `1.x` if `x` is `current-1` or newer. Older
  `1.x` lines are upgrade-only.
- **Data format stability.** Schema versions for `plasmid_library.json`,
  `collections.json`, `parts_bin.json`, `primers.json`,
  `feature_library.json`, etc. require migrators for every bump
  (the `_ENTRY_MIGRATIONS` registry is the lever; invariant #36).
  A user dropping a `0.x` data dir into a `1.x` install must boot
  cleanly and never lose entries.
- **Agent-API surface stability.** No endpoint removal without a
  `1.x` minor-bump deprecation window. New endpoints are additive.
- **CLI flag stability.** No flag rename or removal without the same
  deprecation window. New flags are additive.

## What v1.0.0 *does NOT* commit us to

- **Unicode rendering stability.** Terminal emulators ship glyph
  changes constantly; the braille-dot map will shift visually with
  the user's fonts and that is by design.
- **Performance.** We document budgets (`< 5 s first render on 5 MB`,
  `< 33 ms/frame`) but a `1.x` minor release may slip these if the
  trade-off is clearly worth it. Regressions WILL be flagged in
  release notes.
- **Optional-dependency behaviour.** `primer3-py` and `pyhmmer` are
  required today; should either project become unmaintained, we
  reserve the right to ship a `1.x` that vendors a fallback or
  reduces functionality, documented in release notes.

## Tracking

Current state (snapshot at the time of last edit; re-run before
cutting the release candidate):

| Gate | State |
|---|---|
| Hard #1  | _verify before RC_ |
| Hard #2  | _verify before RC_ |
| Hard #3  | _verify before RC_ |
| Hard #4  | _verify before RC_ |
| Hard #5  | _verify before RC_ (new — added 2026-05-17) |
| Hard #6  | _verify before RC_ |
| Hard #7  | _verify before RC_ |
| Hard #8  | _verify before RC_ |
| Hard #9  | _verify before RC_ |
| Hard #10 | _verify before RC_ |
| Hard #11 | _verify before RC_ |
| Hard #12 | _verify before RC_ (new — added 2026-05-17) |
| Hard #13 | _verify before RC_ |
| Hard #14 | green (0 markers as of 2026-05-17) |

Each "_verify before RC_" should be re-checked and flipped before
proposing the `1.0.0` tag. The maintainer-approval gate sits above
all of these.
