# Agent API

`splicecraft --agent` (alias `--agent-api`) exposes a localhost JSON
HTTP API with bearer-token auth, covering every GUI action external
AI agents need. The server binds `127.0.0.1` only and rejects any
request whose `Host` header isn't loopback (`127.0.0.1` / `localhost`
/ `::1`) — a DNS-rebinding defense, so a browser page can't reach the
API even to enumerate endpoints.

## Why it exists

Local AI coding agents (Claude Code, Cursor, aider, hand-rolled
scripts) work best when they can *do* things in the user's existing
environment, not just generate text. SpliceCraft's agent API lets an
agent drive the running GUI session through the side-door without
leaving its terminal.

## Quick start

```bash
splicecraft --agent                  # default port 6701
splicecraft --agent --agent-port 6800  # alternative port
```

The server writes a token file at
`<DATA_DIR>/agent_token` containing the port + bearer token on the
first two lines. Hand the token to any client that needs to call the
API; the [CLI sidecar](cli.md) reads this file automatically.

```bash
# manual cURL
TOKEN=$(tail -1 ~/.local/share/splicecraft/agent_token)
PORT=$(head -1 ~/.local/share/splicecraft/agent_token)
curl -s -H "Authorization: Bearer $TOKEN" \
     -X POST -H "Content-Type: application/json" \
     -d '{"accession":"L09137"}' \
     http://127.0.0.1:$PORT/fetch
```

## Endpoint inventory

~130 endpoints across:

- **Records** — get / set sequence, add / update / delete features,
  list features, find ORFs, transfer annotations, apply GFF3 features
  to the loaded record (`apply-gff3`).
- **Files** — load (chromosome-scale safe via the path-based loader;
  supports `.gb` / `.gbk` / `.genbank` / `.dna` / `.embl` /
  FASTA / `.ab1` / single-record `.fastq` / `.gff3`),
  export GenBank / GFF3 / FASTA / EMBL / CommercialSaaS `.dna`
  (symlink-guarded), bulk import a folder, bulk export a
  collection (`bulk-export-collection`).
- **Library + collections** — list, search across collections,
  delete entries, create / rename / delete collections, set the
  active collection, list / set plasmid statuses.
- **Parts** — list-parts, get-part, delete-part, classify-part
  (overhang-pair lookup against every grammar); list-parts-bins,
  set-active-parts-bin (switch the active named bin; mirrors the bin
  into the live parts file).
- **Design** — gibson-assemble, simulate-gibson, design-mutagenesis,
  scrub-plasmid (clone-free restriction-site removal: silent / synonymous
  cures inside CDSes + minimal swaps elsewhere; scrubs the loaded record or
  an explicit `seq`+`features`, optional `codon_taxid` biases coding cures to
  a host's frequent codons, never mutates the canvas. `method` picks the
  route: `"quikchange"` (default — cured sequence + an improved-QuikChange
  primer pair per locus) or `"golden_braid"` (split into BsaI-tailed
  fragments that Golden-Gate back together; force-cures every BsaI site,
  returns per-fragment primers + native junction overhangs + a digest+ligate
  `verified` flag)), design-gb-part (Golden Braid / MoClo), design-primers
  (generic Primer3 detection or restriction cloning), optimize-protein
  (codon-optimise an AA sequence to a chosen table; optional `stops`
  0–3 appends that many stop codons, and a trailing `*` run in the
  protein is honored as-is and overrides it).
- **Simulate** — simulate-pcr (exact-match in-silico amplification,
  wrap-aware on circular templates) and simulate-gel (per-lane band
  positions + optional rendered ASCII gel image; ladder / plasmid /
  digest / PCR-amplicon sources).
- **Alignment** — diff-plasmid (circular rotation auto-detected),
  list-plasmidsaurus-members, align-plasmidsaurus-zip.
- **History** — get-history returns the parsed `<HistoryTree>`
  lineage as nested JSON.
- **Codon tables** — list, add (Kazusa fetch or raw dict), delete.
- **Search** — blast, hmmscan.
- **RNA structure + RBS** — fold-rna (minimum-free-energy secondary
  structure: dot-bracket fold + ΔG in kcal/mol, pure-Python Turner-2004,
  no external dependency); cofold-rna (bound-state heterodimer ΔG of two
  strands, e.g. a 16S anti-SD : mRNA hybrid); rbs-strength (relative
  E. coli translation-initiation strength of a ribosome binding site —
  weighs SD:anti-SD match, 5'UTR occlusion, SD-to-start spacing, and the
  start codon; returns a RELATIVE ranking score, not an absolute rate);
  design-rbs (reverse-design a 5'UTR / Shine-Dalgarno + spacer to a
  target relative strength, with the achievable range + an on-target
  flag); assemble-operon (assemble a contiguous operon from a list of
  CDSs each with a target strength — context-aware RBS design + an
  element layout + per-gene achieved-vs-target report). DNA `T` read as
  `U`; ambiguous / over-length / bad input → 400.
- **HMM databases** — list / get / set-active / delete / add /
  download-hmm-database (the registry that backs hmmscan). `add`
  registers a custom `.hmm.gz` URL (mirrors the GUI "Add" form);
  `download` streams → decompresses → hmmpresses a catalog entry
  (builtin like `pfam-a` / `ncbifam`, or a custom one) into
  `<DATA_DIR>/hmm_databases/<id>/` exactly as a GUI download would, so
  `set-active` + `hmmscan` can then use it. `download` runs
  synchronously, so a large builtin (Pfam-A ~300 MB, NCBIfam ~600 MB)
  blocks the request for minutes; a 409 means a download for that id is
  already in flight. `delete` un-downloads the files but keeps the
  catalog entry so `download` can re-fetch it.
- **Plasmidsaurus** — plasmidsaurus-items (list your sequencing
  orders, most-recent first) / download-plasmidsaurus (fetch a run's
  results zip by its 6-character item code over Plasmidsaurus's
  official OAuth2 REST API and import the run's `.gbk` assemblies into
  the library as new entries, tagged
  `source: plasmidsaurus:<code>:<sample>`, never overwriting existing
  entries). Credentials resolve env-first
  (`PLASMIDSAURUS_CLIENT_ID` / `_SECRET`) then the Settings values, and
  are deliberately NOT exposed through get-settings / set-setting.
  `download` runs synchronously and imports only `kind=results` (the
  archive that carries assemblies); no credentials → 400, a download /
  parse failure → 502, an archive with no samples → 422.
- **Custom enzymes + enzyme collections** — list / get / create /
  update / delete-custom-enzyme; list / get / create / update /
  delete-enzyme-collection; get / set-active-enzyme-collection.
- **Feature library** — list / get / create / update /
  delete-feature-library (reusable annotation snippets).
- **Primer collections** — list-primer-collections,
  set-active-primer-collection (plus per-primer CRUD).
- **Data safety** — list-backups, restore-backup,
  list-pre-update-snapshots, restore-pre-update-snapshot.
- **Settings** — get-settings, set-setting (allowlisted toggles).
- **Experiments lab notebook** — list / get / create / update /
  delete experiment entries; list / create / rename / delete
  projects; set active project (full notebook-layer CRUD).
- **Gels** — list / get / create / update / delete saved gel
  snapshots (in addition to simulate-gel for one-shot runs).
- **Protein motifs** — list (built-ins + user overrides),
  set (copy-on-write override), delete user overrides.
- **Entry vectors** — list, get, set, plus auto-detect across the
  full library and clear-for-grammar.
- **Utility** — check-primer-duplicates, capture-snapshot.

Call `/tools` for the live discovery endpoint that emits the current
inventory with one-line docs per endpoint.

## Security posture

- **Bearer-token auth** on every write endpoint; reads are
  unauthenticated to keep scripted introspection ergonomic.
- **Localhost only** (`127.0.0.1`) — single-tenant by design. Do not
  expose on a LAN.
- **Inputs are length-, range-, and shape-validated at the boundary.**
- **Symlink refusal**: write paths go through
  `_check_agent_write_path` which walks the full ancestor chain via
  `resolve()` divergence + per-segment `is_symlink()`. Pre-fix this
  only checked the immediate parent — see `CLAUDE.md` invariant #41
  for the regression.
- **Read-dir traversal** uses `lstat` + `S_ISDIR` to refuse
  directory-symlink escapes.
- **Per-handler size caps** — `_h_load_file` 50 MB
  (`force=true` override), agent paths capped via
  `_safe_file_size_check`, manifest reads capped at
  `_PRE_UPDATE_MANIFEST_MAX_BYTES`.
- **Secrets stay out of the agent surface.** The Plasmidsaurus Client
  Secret is excluded from the settings allowlist (so get-settings can't
  read it back and set-setting can't change it) and is redacted to
  `<redacted>` in the change log + event stream. The Plasmidsaurus
  download endpoint streams over the same hardened, HTTPS-only,
  size-capped fetch path as the rest of the network layer and verifies
  a zip-archive magic header before writing to disk.

## Cross-collection lookups

The `transfer-annotations` and `diff-plasmid` endpoints look up
plasmids in the **active library only** (via `_load_library()`), not
across collections. To target a plasmid in another collection: call
`search-library` to locate it, then `set-active-collection`, then the
endpoint you actually want. Documented in each handler's docstring;
see [`CLAUDE.md`](
https://github.com/Binomica-Labs/SpliceCraft/blob/master/CLAUDE.md)
invariant #30.

## Concurrency

- Heavy ops (BLAST build, BLAST search, HMMscan, alignment) run in
  `@work(thread=True)` workers; the API returns immediately with a
  status the client can poll, OR blocks the request until the worker
  completes — endpoint-specific.
- The agent server uses `_agent_save_or_500(save_fn, label)` for
  every `_save_*` call so an OSError / RuntimeError becomes a 500 +
  in-app notify, not a silent in-memory / disk desync.

## Discovery + introspection

```bash
splicecraft-cli tools             # list every endpoint + one-line doc
splicecraft-cli status            # current record snapshot
splicecraft-cli features          # features on the loaded record
```

See the [CLI sidecar](cli.md) for the full convenience wrapper.
