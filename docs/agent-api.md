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
splicecraft --headless               # agent API only, NO terminal UI (CI / no-pty)
```

`--headless` (alias `--agent-headless`, or env `SPLICECRAFT_HEADLESS=1`)
runs the JSON API under Textual's headless driver — no pty required, so
it backgrounds cleanly in CI / agent contexts (no `script -qfc` wrapper).
It implies `--agent`, prints a `SpliceCraft agent API ready on …` line to
stdout once the socket is accepting connections, and serves the same
endpoints as the UI launch. For readiness, poll the unauthenticated
`GET /healthz` (returns `{ok, status:"ready", version, headless}`)
instead of racing the token-file write + first real call.

A long-running daemon keeps serving the code it launched with, so after a
`splicecraft update` in another terminal it silently runs stale. `status`
surfaces this: it reports `installed_version` (what's on disk now) next to
the running `version`, plus a `stale` boolean (`installed_version` is `null`
when it can't be read, e.g. running from a source checkout). Every response
also carries a `_stale` warning when the running version is behind, so you
notice without polling `status`. A **headless** daemon can `POST /restart`
itself — it re-execs and the API returns on the same port (poll `/healthz`);
a GUI `--agent` session refuses (it would lose its live view — restart it
manually).

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

~179 endpoints across:

- **Records** — `new-plasmid` (create from a raw sequence, the Ctrl+N
  flow), get / set sequence, add / update / delete features (with the full
  arrow type — forward ▶ / reverse ◀ / arrowless / double-stranded ◀▶ —
  plus colour and arbitrary GenBank qualifiers, matching the Insert/Edit
  Feature dialog; `add-features` inserts MANY at once under one lock + one
  dirty-check), list features, find ORFs (length cutoff in AMINO ACIDS —
  `min_aa`; `min_length`/`min_bp` are rejected so a bp-vs-aa mix-up can't
  silently return a default-length result), `undo` / `redo` the last edit,
  `discard-changes` (revert the canvas to its library-stored copy / clear a
  stuck-dirty flag so the next load / new-plasmid proceeds without `force`),
  transfer annotations, apply GFF3 features to the loaded record
  (`apply-gff3`).
- **Files** — load (chromosome-scale safe via the path-based loader;
  supports `.gb` / `.gbk` / `.genbank` / `.dna` / `.embl` /
  FASTA / `.ab1` / single-record `.fastq` / `.gff3`),
  export GenBank / GFF3 / FASTA / EMBL / CommercialSaaS `.dna`
  (symlink-guarded), bulk import a folder, bulk export a
  collection (`bulk-export-collection`).
- **Library + collections** — list, search across collections,
  load an entry by name or id (`load-entry` resolves cross-collection:
  active collection first, then the others; pass `collection` to
  disambiguate), rename a plasmid's display name (`rename-plasmid` —
  fixes a name that got slugged to underscores), delete entries,
  copy-plasmid (copy an entry into another collection, name + features
  intact, in one call), move-plasmid (RELOCATE an entry between
  collections atomically — no copy-then-delete data-loss round-trip;
  handles the active collection on either side, re-staging the live
  library mirror), copy-plasmids / move-plasmids (the BULK form — a
  whole `{names:[…]}` list into one collection in a single locked save,
  the machine-friendly path that sidesteps the rate limiter; per-item
  results report copied/moved, not_found, ambiguous, and conflict, never
  silently renaming or dropping), create / rename / delete collections,
  get / set the active collection, list / set plasmid statuses.
- **Parts** — list-parts, get-part, create-part / update-part (full
  parity with the Part editor — name / grammar / type / level / position /
  overhangs / sequence plus `backbone`, selection `marker`, and
  `fwd_primer` / `rev_primer` domestication primers, Tms derived),
  delete-part, move-part (reassign a part to another bin in one atomic
  call), classify-part (overhang-pair lookup against every grammar).
  `create-part` / `list-parts` / `delete-part` accept a `{bin}` to file
  into / read / prune just that named bin's own parts (a bin is a real
  partition, not only for writes). Parts-bin containers:
  list-parts-bins, create-parts-bin (the parts-side parallel to
  create-collection), set-active-parts-bin (switch the active named bin;
  mirrors the bin into the live parts file), rename-parts-bin,
  delete-parts-bin (the deleted bin's parts go with it; the last bin
  can't be removed; the active bin auto-promotes + re-mirrors).
- **Design** — gibson-assemble, simulate-gibson, traditional-clone /
  simulate-traditional-cloning (restriction digest + ligation: excise the
  insert, digest the vector, try every vector-fragment × insert-fragment ×
  orientation, and save the product — refuses to guess when more than one
  ligation is possible, pick with `vector_frag_idx` / `insert_frag_idx`. Pass
  `insert_circular:true` to cut a cassette OUT of a *plasmid* insert — e.g. an
  Ω multigene into a binary vector — so its two digest fragments are both
  offered instead of treating the insert as a linear PCR product),
  golden-gate-assemble / simulate-golden-gate (Type IIS — BsaI /
  BsmBI / BbsI / SapI / Esp3I — overhang-directed N-part assembly: parts in
  any order, chained by their 4-nt overhangs into a circle, with a
  unique-overhang + no-residual-site fidelity check), design-mutagenesis,
  scrub-plasmid (clone-free restriction-site removal: silent / synonymous
  cures inside CDSes + minimal swaps elsewhere; scrubs the loaded record or
  an explicit `seq`+`features`, optional `codon_taxid` biases coding cures to
  a host's frequent codons, never mutates the canvas. `method` picks the
  route: `"quikchange"` (default — cured sequence + an improved-QuikChange
  primer pair per locus) or `"golden_braid"` (split into BsaI-tailed
  fragments that Golden-Gate back together; force-cures every BsaI site,
  returns per-fragment primers + native junction overhangs + a digest+ligate
  `verified` flag)), design-gb-part (Golden Braid / MoClo), domesticate-part
  (the real Synthesis-tab L0 clone: digest the grammar's CONFIGURED entry
  vector + the part's primed amplicon at the Type IIS enzyme, ligate the
  insert into the backbone, and save the circular L0 plasmid with its
  insert + entry-vector lineage — pass `{sequence, oh5, oh3, name,
  grammar?, type?, fwd_primer?, rev_primer?, collection?}` (saves to the
  active collection, or the non-active `collection` if given). **Fails
  loud** (422) when no
  compatible entry vector is configured rather than silently emitting a
  pUPD2-stub backbone, so an agent never files a wrong construct;
  `domesticate-parts` runs the same engine over a `{parts:[…]}` batch in
  ONE call — dirty-guard checked once, per-item results so one part that
  can't clone doesn't abort the rest),
  assemble-into-entry-vector (the multi-source level-up clone: chain L0
  parts into an α/L1 TU, or TUs into an Ω/L2 module, by their fusion
  overhangs and ligate into the configured `role` acceptor at the level-up
  Type IIS enzyme — pass `{sources, grammar?, source_level?, role, name,
  collection?}` (active collection by default, or the named one).
  Also **fails loud** (422) on a missing acceptor or a chain that can't
  close; the L2→binary hop is a known engine gap), design-primers
  (Primer3 primer-pair design over a region: `detection` picks a
  diagnostic amplicon, `cloning` appends RE-site tails, `generic`
  returns binding-only primers — no tails / overhangs), check-primer
  (a SINGLE oligo vs a template: Tm, GC%, and every 3'-anchored binding
  site — both strands, wrap-aware on a circular template, the tail
  scored as mismatches — to confirm a designed primer binds exactly once
  before saving), optimize-protein
  (codon-optimise an AA sequence to a chosen table; optional `stops`
  0–3 appends that many stop codons, and a trailing `*` run in the
  protein is honored as-is and overrides it).
- **Simulate** — simulate-pcr (exact-match in-silico amplification,
  wrap-aware on circular templates) and simulate-gel (per-lane band
  positions + optional rendered ASCII gel image; ladder / plasmid /
  digest / PCR-amplicon sources).
- **Digest** — digest (cut a RAW sequence with named enzymes and report
  the cuts + resulting fragments with their **overhangs** — overhang-aware
  QC for a Golden-Braid / restriction junction without loading the
  sequence onto the canvas; `circular` defaults true, a singular `enzyme`
  is accepted, and names the catalog doesn't know are reported under
  `unknown_enzymes` rather than silently dropped).
- **Alignment** — diff-plasmid (one target, circular rotation
  auto-detected), multi-align (batch: the loaded plasmid or a given
  sequence vs many targets at once — the Alt+A overlay; rotation-aware
  per target, circular auto-detected from each target's topology or
  forced with a `circular` flag, with `picked_rotation` reported per
  row), list-plasmidsaurus-members, align-plasmidsaurus-zip.
- **History** — get-history returns the parsed `<HistoryTree>`
  lineage as nested JSON. Agent assemblies (`gibson-assemble`,
  `traditional-clone`, `golden-gate-assemble`) attach real parent
  lineage — each input fragment / part / vector becomes a parent node, so
  the product reads as a genuine `insertFragment` assembly instead of a
  flat `createDocument` leaf. Name your inputs (a `{name}` on a gibson
  fragment / golden-gate part, or `vector_name` / `insert_name` on a
  traditional clone) and a parent that matches a saved library entry
  nests that entry's own sub-lineage.
- **Codon tables** — list, add (Kazusa fetch or raw dict), delete.
- **Search** — blast (in-process BLASTN / BLASTP against your
  collections; pass `collections` to scope the DB to a subset and stay
  under the build cap on a large library), hmmscan. **Online** (off by
  default): blast-online (remote NCBI BLAST vs `nt`/`nr`/…) and
  hmmer-web (remote hmmscan vs Pfam at EBI) SHIP the query sequence to
  an external server, so they are refused `403` unless the SpliceCraft
  user has armed Settings → "Allow agent online BLAST/HMMER". That
  setting is the HUMAN half of a two-key gate — it is deliberately NOT
  in the agent settings allowlist, so an autonomous agent can never flip
  it on itself; the in-process blast / hmmscan stay local and never
  consult it. Both block for minutes on the remote job (502 on a
  network / server error) and are concurrency-capped.
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
- **Primer collections** — list-primer-collections, create-primer-collection
  (the primer-side parallel to create-collection), rename-primer-collection,
  delete-primer-collection (the last collection can't be removed; the active
  one auto-promotes + re-mirrors), set-active-primer-collection,
  move-primer (reassign a primer to another collection in one atomic call),
  plus per-primer CRUD. `list-primers` / `get-primer` / `delete-primer` accept
  a `{collection}` to read or prune just that collection's own primers (a
  collection is a real partition, not only for writes). `delete-primers`
  prunes a whole `{names:[…]}` batch in ONE locked save (the machine-friendly
  path for a large cleanup that would otherwise trip the rate limiter),
  reporting `removed` / `not_found` / `ambiguous`.
- **Data safety** — list-backups, restore-backup,
  list-pre-update-snapshots, restore-pre-update-snapshot.
- **Settings** — get-settings, set-setting (allowlisted toggles only;
  a handful of settings — the Plasmidsaurus secret, the online-search
  egress gate `allow_online_search` — are deliberately NOT in the
  allowlist, so an agent can neither read nor flip them; those are
  human-armed via the GUI Settings dialog);
  get-feature-colors / set-feature-color (the per-feature-type render
  colour overrides).
- **Experiments lab notebook** — list / get / create / update /
  delete experiment entries; move-experiment (relocate an entry to
  another project in one atomic call); attach-experiment-image (attach a
  server-side image file + embed it in the entry body); list / create /
  rename / delete projects; set active project (full notebook-layer CRUD).
  `create-experiment` / `list-experiments` / `delete-experiment` accept a
  `{project}` to file into / read / prune just that named project's own
  entries (a project is a real partition, not only for writes; deleting
  from the active project by name is refused — switch away first so the
  live mirror stays consistent).
- **Gels** — list / get / create / update / delete saved gel
  snapshots (in addition to simulate-gel for one-shot runs).
- **Protein motifs** — list (built-ins + user overrides),
  set (copy-on-write override), delete user overrides.
- **Entry vectors** — list (carries each vector's `role`; a grammar can
  hold several — Alpha1 / Alpha2 / Omega1 / Omega2 + the L0 vector), get,
  set, plus auto-detect across the full library and clear-for-grammar.
- **Active pointers** — every `set-active-*` (collection, codon-table,
  primer-collection, parts-bin, experiment-project, enzyme-collection,
  hmm-database) has a matching `get-active-*` so a client can read the
  current selection before changing it.
- **Utility** — check-primer-duplicates, capture-snapshot.

Call `/tools` for the live discovery endpoint. Each entry is
`{name, method, write, doc, doc_full}` — `doc_full` is the endpoint's
COMPLETE docstring, which documents the request body (required / optional
keys, aliases, enums, size caps), so a client forms a correct call in one
round-trip instead of N trial-and-error 400s. `/tools` is authoritative:
it lists every registered endpoint, including the ~42 app-coupled
handlers that live in `splicecraft.py` rather than `splicecraft_agent.py`
— don't rely on grepping a single file for `@_agent_endpoint`.

Every success response also carries a predictable **`data`** field, so you
don't have to know each endpoint's ad-hoc key (`seq` / `library` / `sites`
/ `matches` / …): `data` is the result with the envelope/metadata stripped,
unwrapped to the bare value when there's a single content key (a scalar or
list lands directly under `data`). The original keys stay too, so it's a
superset — read whichever you prefer.

## Security posture

- **Bearer-token auth** on every write endpoint; reads are
  unauthenticated to keep scripted introspection ergonomic.
- **Localhost only** (`127.0.0.1`) — single-tenant by design. Do not
  expose on a LAN.
- **Inputs are length-, range-, and shape-validated at the boundary.**
- **Symlink refusal**: write paths go through
  `_check_agent_write_path` which walks the full ancestor chain via
  `resolve()` divergence + per-segment `is_symlink()`. Pre-fix this
  only checked the immediate parent — see the symlink ancestor-chain
  regression in [`docs/invariants.md`](invariants.md).
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

`load-entry` resolves **across collections**: it checks the active
collection first, then the others. A unique cross-collection hit loads
(and the response names its `collection`); an ambiguous name returns
`409` listing the holders; pass `collection` to pin the search.
`search-library` is cross-collection too, and the `id` it returns is a
valid `load-entry` key.

The mutation endpoints `rename-plasmid`, `set-plasmid-status`,
`delete-from-library`, and the lookup in `diff-plasmid` operate on the
**active collection only** (via `_load_library()`). To target a plasmid
elsewhere, `set-active-collection` to its home first — `search-library`
shows which collection holds it.

`transfer-annotations` resolves its `source_id` **across collections**
(like `load-entry`): the active collection first, then the others, by
display **name or id**. Pass `source_collection` to pin the search, and an
ambiguous key across collections returns `409`. So a backbone in a
non-active collection transfers onto the loaded record without a temp copy.
To apply (not preview) pass `apply: true` — or the legacy `dry_run: false`;
the default is a dry run.

`list-library` lists the active-collection library by default; pass
`{collection}` to scope it to any one collection's plasmids without
switching the active collection (`404` if there's no such collection).

### Name integrity

`load-entry` stamps the entry's stored display name onto the loaded
record, so a later `save` / `add-current-to-library` round-trips the real
name (spaces preserved) instead of the underscored GenBank LOCUS. A
record pulled in with `fetch` (inspect-only, `saved:false`) is **not**
auto-filed on `save` — creating a new library entry requires an explicit
`{create:true}` so an inspection can't silently pollute the active
collection. Unknown body keys are echoed back under `ignored` rather than
silently dropped.

`load-file` and `add-current-to-library` accept optional `{id, name}`
overrides to stamp the record's identity directly. This is the
deterministic fix for a **batch build loop**: a from-scratch `.dna`
carries no name packet, so without an override every such file loads as
id `Cloned` and a loop of `add-current` calls silently REPLACES each
prior entry (the library dedups by id) down to a single survivor. Pass a
unique `id` (scrubbed to `[A-Za-z0-9_-]`, 32-char cap) per build — and a
spaced `name` for the display name — and all N persist. The override is
applied to a copy, so the live canvas record is untouched.

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
splicecraft-cli call <endpoint> --json '{...}'   # call ANY endpoint
```

`call` is a generic passthrough that reuses the same token / host / port
plumbing as the named subcommands (method defaults to POST when `--json`
is given, else GET), so a client never has to hand-roll HTTP against the
private `_request`. An HTTP error surfaces as structured JSON (with
`http_code`) plus a non-zero exit, instead of a hard exit that would kill
a batch mid-run.

See the [CLI sidecar](cli.md) for the full convenience wrapper.
