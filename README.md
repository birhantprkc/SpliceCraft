# SpliceCraft

![SpliceCraft Logo](https://raw.githubusercontent.com/Binomica-Labs/SpliceCraft/master/splicecraftLogo.png)

[![PyPI](https://img.shields.io/pypi/v/splicecraft.svg)](https://pypi.org/project/splicecraft/)
[![Python](https://img.shields.io/pypi/pyversions/splicecraft.svg)](https://pypi.org/project/splicecraft/)
[![100% Python](https://img.shields.io/badge/100%25-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![TUI: Textual](https://img.shields.io/badge/TUI-Textual-5A45FF?logo=python&logoColor=white)](https://textual.textualize.io/)
[![Tests](https://github.com/Binomica-Labs/SpliceCraft/actions/workflows/test.yml/badge.svg)](https://github.com/Binomica-Labs/SpliceCraft/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: stable](https://img.shields.io/badge/status-stable-4EBF71.svg)](https://github.com/Binomica-Labs/SpliceCraft)

## Your whole cloning workflow, in the terminal.

SpliceCraft is a plasmid workbench that runs where you already work. Open a
map, edit the sequence, design primers, plan a Golden Braid or MoClo
assembly, BLAST a hit, check your Sanger reads, and keep a lab notebook —
all from the keyboard, all in one place, no browser tab and no cloud
account. Circular and linear maps render as crisp Unicode braille graphics
in any modern terminal, and nothing leaves your machine unless you ask it to.

It's built by a practicing bioengineer for daily bench work: the bug
reports come from real cloning, and so do the fixes. If you live in a
terminal and clone for a living, it's meant to feel like home.

![SpliceCraft screenshot](https://raw.githubusercontent.com/Binomica-Labs/SpliceCraft/master/splicecraftScreenshot.png)

**Why give it a try:**

- **Fast and local.** No Electron, no web app, no login. `pipx install splicecraft` and you're designing in seconds.
- **It does the whole job.** View → edit → design → clone → simulate → verify → document — one tool that actually understands how those steps connect.
- **It guards your data like it's irreplaceable** (because it is — see below).
- **It's scriptable.** A 100+ endpoint local API and a stdlib CLI let an agent or a shell script drive every workflow.

## Quick start

```bash
pipx install splicecraft
splicecraft                      # empty canvas
splicecraft L09137               # fetch pUC19 from NCBI on launch
splicecraft myplasmid.gb         # local GenBank or .dna
```

x86-64 Linux, Intel macOS, and Windows install entirely from prebuilt
wheels — nothing to compile. On **ARM64 Linux** (Raspberry Pi / ARM
cloud) and **Apple Silicon with Python ≥3.10**, one dependency
(`primer3-py`, the primer-design engine) has no prebuilt ARM wheel and
compiles at install, so install a C toolchain first:
`sudo apt install build-essential python3-dev` (Linux) or
`xcode-select --install` (macOS). One-time, then `pipx install splicecraft`.

Press `?` once running for the full keyboard-shortcut reference. See
[`docs/install.md`](docs/install.md) for pip / uv / conda / source installs.

## A workhorse that just works

Your plasmid library is the product of months — sometimes years — of work.
SpliceCraft is engineered so it can be your daily driver without ever making
you nervous about it. Three promises, and the receipts behind them:

**Your data is sacred.** Every save runs through a four-layer safety net: an
atomic write (a crash can never leave a half-written file), a `.bak` plus
rotating timestamped backups, a daily snapshot, and a "suspicious shrink"
guard that flat-out refuses to replace a 156 MB library with an empty file.
Open a file that collides with something you already have and SpliceCraft
asks — skip, keep a copy, or overwrite — it never clobbers your work behind
your back. Before any self-update it snapshots everything to a sibling
directory first, so even a hypothetical bug in a new version can't take your
recovery copy with it.

**The biology is correct, and we prove it.** Restriction scanning handles
palindromes, Type IIS enzymes, and origin-spanning cut sites the way a bench
scientist expects; features that wrap the origin survive every edit;
translation (including non-standard genetic codes via `/transl_table`),
reverse-complement, and IUPAC matching are pinned down to the base.
Selection markers are read from the actual entry vector you're using —
no hardcoded antibiotics quietly mislabeling a construct. There are
**4,000+ automated tests** behind all of it, plus property-based fuzzing on
the biology primitives, crash-injection tests on the save path, and
concurrency fuzzing on the data layer. Every release ships only when the
whole suite is green.

**We go looking for trouble.** The codebase is governed by a long list of
"sacred invariants" (documented in [`CLAUDE.md`](CLAUDE.md)), and each
release is preceded by deep, multi-pass audits hunting for edge cases,
data-loss windows, race conditions, and security gaps — with every finding
verified against the real code before a line is changed. The short version:
it's a workhorse, and it goes to real lengths to keep "it just works" true.

Full data-safety writeup: [`docs/data-safety.md`](docs/data-safety.md) ·
Security policy: [`SECURITY.md`](SECURITY.md).

## A guided tour

Everything lives behind a menu bar across the top of the screen. Here's the
tour — starting with **BLAST** and working rightward across the bar, with the
housekeeping menus (**File**, **Settings**) saved for last.

### BLAST

Search without leaving the app (`Ctrl+B`). The **Local** tab runs BLASTN /
BLASTP / HMMscan against your own library with an in-process engine — powered
by `pyhmmer`, so there's no external `blast+` to install — and a one-click
downloader pulls Pfam-A or NCBIfam (or any HMMER3 `.hmm.gz` URL) with
automatic update-detection, no `wget` + `hmmpress` ritual. On native Windows,
BLASTN/BLASTP run in-process but HMMscan needs WSL2 (`pyhmmer`/HMMER is
POSIX-only). The **Online** tab
sends DNA / RNA / protein — or a whole plasmid or a single feature straight
from your library — to NCBI (blastn / blastp / blastx / tblastn / tblastx) or
to Pfam at EMBL-EBI, and drops the hits into a scrollable table just like a
web BLAST. A live spinner and poll counter prove the search is really running,
and Cancel actually stops it.

### Enzymes

Drive the restriction overlay: show all sites, unique cutters only, 6+ or 4+
bp recognition, or just the Golden Braid connectors. A multi-cutter wears a
**superscript cut-count** on its name (EcoRI², BsaI³) on the map and sequence
panel — live, so editing out a site ticks it down and you can confirm you
killed a cut. Build **enzyme collections** — named subsets of the master catalog (200+ NEB enzymes plus
your own customs) — and the active collection scopes every scan. Add a custom
enzyme (name, site, cut positions, type, supplier) and it's live in every
map from then on.

### Features

A workbench for your reusable annotations — promoters, RBSs, tags, CDSs.
Capture a region off any plasmid into the feature library, then drop it back
onto another construct to either *annotate* a selection or *splice* the
sequence in. It's the same store the Synthesis editor and Domesticator pull
from, so your parts stay consistent everywhere.

Marking things up by hand? **Ctrl+F** finds a DNA subsequence — fuzzy, so
you can allow a few mismatches, and on both strands — then `n` / `N` step
through every hit. Each match lands pre-selected, so **Alt+Shift+F** tags it
as a feature on the spot: ideal for walking a plasmid and annotating repeat
regions one by one. (Looking for an existing annotation instead? `Ctrl+/`
searches your features by name.)

### Primers

A full-screen Primer3 designer covering detection, cloning, Golden Braid, and
generic primers — each with a persistent **Designed → Ordered → Validated**
lifecycle, so you can see at a glance what's been ordered and what came back
working, right alongside the plasmid it belongs to.

The library beside the designer organises primers into **collections** (new /
rename / delete, like the plasmid side, with destructive prompts defaulting to
**No**), with a **search bar** above the list that filters by name (fuzzy, like
the plasmid library). Press **Space** to cycle a primer's mark — ★ select → $
cart → M move → none — then **MOVE** the M-marked primers to another collection,
or bulk-delete / re-status the ★-marked set (the warning names exactly how many
go); marks are tracked by the primer itself, so searching/filtering never moves
a mark onto the wrong one, and **CLEAR** wipes every mark at once. When it's time
to order, **export** a collection — or your order **Cart** (the $-marked primers)
— to a paste-ready **CSV** (name, sequence, length, Tm) for the synthesis
company; a successful cart export **clears the $ marks** so the next order starts
empty. **Import** a CSV back for a full round-trip. Exports refuse a malformed
oligo and imports skip + report invalid rows, so a wrong sequence never reaches
your order or your library.

### Mutato

Site-directed mutagenesis, with a hint of whimsy in the name. Point at any
CDS, name the change (say, `L54A`), and SpliceCraft designs the SOE-PCR
primers for you — with a smart fallback to a 2-primer modified-outer strategy
when a near-the-end mutation can be folded into a single primer. It checks
its own work, too: the shortcut is only offered when the primer genuinely
carries the change, so you never amplify wild-type by accident. It also turns a
pasted protein into a ready-to-order CDS — frequency-matched codon optimization
against your chosen table, with a **stops** selector (1–3) that also honors a
trailing `*` run when you want a double or triple stop codon, and an **Avoid
sites** picker that scrubs your chosen restriction-enzyme cut sites out of the
optimized CDS.

Its second tab, **Scrub**, cures a whole plasmid of restriction sites without
any cloning. Pick the enzymes to remove (Golden Gate / MoClo Type IIS sites by
default) and SpliceCraft finds the smallest set of point changes that destroy
each site — **silent** ones inside any coding sequence, checked against *every*
overlapping reading frame so a protein never changes (and biased toward your
chosen codon table's frequent synonyms when there's a choice), and minimal
swaps elsewhere — without ever spawning a new forbidden site. Press **Apply
cure** and SpliceCraft names + saves the cured circular plasmid to a collection
you pick — with the cure primers bound where they anneal — draws those same
primers on the **original** plasmid (each cure showing as a mismatch) so you can
see exactly what every primer changes, and loads the result onto the canvas. The
primers are designed off the cured sequence so they bind exactly where they're
drawn; save them to your primer library or add them straight to the map as well.
Choose how it re-circularizes:
**QuikChange** — an improved-QuikChange pair per locus, PCR → DpnI → transform
(no ligase, no assembly); or **Golden Braid** — split the plasmid at each cure
into BsaI-tailed fragments that a one-pot Golden Gate reaction ligates back
together, the native 4 nt junction overhangs making reassembly seamless so the
only net change is the cured sites. (BsaI being the assembly enzyme, every BsaI
site is force-cured, and the design proves itself by a digest-and-ligate
simulation before you order.) Sites it can't remove silently are reported,
never forced. On a Golden Braid cure, **Apply cure** doesn't shortcut to the
finished plasmid — it saves each fragment's PCR amplicon to your library as
`PCR-<primer-pair name>` and genuinely BsaI-digests + ligates them back together,
so the cured plasmid's **History** reads as a real assembly with those amplicons
as its parents.

### Synthesis

A gene-synthesis composer with three tabs. The **DNA tab** is a
horizontally-scrolling linear editor with anti-parallel strand markers, live
feature stripes, restriction overlay, and AA translation, plus a feature
library side-pane (Insert to splice, Annotate to overlay), and a feature-aware
paste — copy a stretch of a plasmid (`Ctrl+C`) and paste it here, and its
features ride along, annotated at the right positions. The **Protein
tab** lets you type or paste amino acids and watch the codons appear underneath
using your chosen codon table — pick a different one or hit **Manage** to open
the **tabbed codon-table manager**, where each way of getting a table has its
own tab: **Build from genome** (give an NCBI assembly accession or a taxid, and
choose highly-expressed genes — ribosomal proteins, the recommended bias for
strong expression — or the whole genome), **Fetch (Kazusa)**, **Import TSV**,
plus a **Library** tab to pick or delete and a **Chart** tab that draws any
table as the classic genetic-code grid — each codon annotated with its usage
within its amino-acid family (relative synonymous usage), and each family's
single most-used codon highlighted in bold green (family-wide, so a codon
split across cells — Leu, Ser, Arg, the stops — lights up once) for easy
visual identification. The same manager opens from
**Settings ▸ Codon Tables** and the Mutato / Constructor codon pickers. Codons
are reflected live — and a built-in motif library (His6, FLAG, HA, TEV,
P2A, NLS, GS linkers, and ~30 more) inserts pre-colored tags. Hit **Optimize →
DNA** to codon-optimize the protein — the **Stops** selector (0–3) auto-tracks
the protein's trailing `*` run (override it and your choice wins), alongside the
same **Avoid sites** scrubbing as Mutato — and hand the CDS straight to the DNA
tab as an editable fragment. Or hit **Open** to
load a sequence straight from a single-entry FASTA (or other amino-acid file) — a
file browser highlights the loadable formats in pink. Compose a part,
hit **Clone Fragment**, and pick how to clone it: any modular grammar
(Golden Braid L0, MoClo, your own custom grammars) hands the fragment straight
to the **Domesticator** as an L0 building block, while **Gibson** and
**Traditional (restriction / ligation)** open the **Constructor** on the matching
tab with the fragment already pasted in. Nothing is saved and nothing on your
canvas is touched at this point — you name things later, at the save step. If the
modular grammar you pick has no **entry vector** bound yet (a fresh install, say),
SpliceCraft asks you to pick an acceptor plasmid from your library and checks it
really carries that grammar's dropout cassette before binding it, so the clone
can't quietly degrade to a bare insert. When you save the domesticated part, one
dialog names — and independently files — three things: the **cloned plasmid** (the
part actually cloned into the entry vector — a full circular plasmid — into any
collection), the **linear fragment** you'd send for a DNA-synthesis order (its own
name, defaulting to a `FRAG-…` form, into any collection), and which **parts bin**
the L0 part files into. The fragment is the *primed amplicon* — the insert
flanked by the designed domestication primers' enzyme sites + overhangs, exactly
as it would run on the bench — and both the fragment **and** the clone carry the
**domestication primers**, drawn with their bound (annealing + overhang) and
unbound (enzyme-tail) regions, so you can see exactly how each was built (and
regenerate the amplicon later for a synthesis order). The fragment also keeps
whatever **features** you'd annotated on it (the optimized CDS bar, say),
redrawn at the right bases on the amplicon. Both the DNA and Protein
tabs also carry a **Clear** button — a mouse twin for the keyboard reset — that
empties the editor, prompting first if you have unsaved edits.
The **Operon Design tab** has two sub-tabs. **Synthetic Operon Construction**
turns the codon optimizer and SpliceCraft's
built-in RBS engine into an expression-tuning workbench. Keep a library of
**protein collections** on the right (add a protein by pasting a sequence,
grabbing a CDS from any plasmid in your library with **From feature**, or
**Fetch**ing one from NCBI by accession), drop proteins into the **assembly
lane**, and give each gene a **target relative RBS strength**.
**Assemble** codon-optimizes every CDS and reverse-designs every ribosome
binding site *in its real assembled context* — so the achieved strength tracks
your target, and a gene the surrounding sequence can't drive strongly is
flagged rather than silently missed. The finished operon (promoter + RBS + CDS
per gene + terminator) drops into the DNA tab fully annotated, ready to add cut
sites or **Clone Fragment**. It's all pure-Python — the RNA folding,
cofolding, and translation-initiation model ship inside SpliceCraft with no
external dependencies.

**Native Operon Domestication** is the reverse workflow: lift a *natural* operon
— from the canvas selection, a library plasmid, or an NCBI accession — cure the
Type IIS sites your chosen grammar forbids (plus any extra enzymes you list in
**Also cure**, e.g. `EcoRI, KpnI`, to scrub downstream cloning sites too), and
clone it into that grammar with its features intact. Because you amplify native DNA off a microbe (nothing is
synthesized), every cure is carried by a primer: synonymous substitutions inside
each CDS (in the correct reading frame — reverse-strand and multi-CDS operons
included, and a stop codon never becomes sense), plus a guided single-base fix
you pick for any site that falls outside a CDS. SpliceCraft designs the
**SOE-PCR primer set** (a flanking cassette pair plus one mutagenic pair per cure
junction) and refuses to proceed unless *every* cure is primer-encoded.

**Clone into grammar** saves the whole job in one step, with you naming and filing
each piece. The cured operon becomes a CDS-equivalent **OPERON** L0 part in your
parts bin — a Golden Braid position whose AATG overhang carries the first gene's
start codon and whose GCTT meets a terminator, so it drops into an assembly between
a promoter and a terminator exactly like a CDS. The part is then **cloned into its
entry vector** and saved to a plasmid collection you choose (named how you like),
and the **cured PCR amplicon** is saved to your library under a name you pick
(prefilled `PCR-…`) in the collection you choose; the SOE primers land in your
primer library. You also name the **primer family** in the save dialog — that one
name labels the whole SOE set as `{family}-DOM-#-F/R` (the `-DOM-` domesticator tag,
the positional pair number, and forward/reverse) everywhere they appear. The primers
are drawn **bound** to the PCR fragment, the cloned plasmid, **and the original
source plasmid you lifted from** — re-derived against each sequence so they sit where
they anneal, with each synonymous cure showing as a mismatch on the source, so you
can see exactly what every primer changes and why. Both the fragment and the clone
record how they were made in their **History** tab, and the individual genes carry
all the way through to the clone — no whole-operon band stacked on top of them.
Cancelling either name prompt cancels the whole save; when it completes, the finished
clone is loaded onto the canvas and highlighted in the library.

**Save** (and **Save As**, which forks a copy and only lights up once the
fragment has been saved once) let you pick which collection the fragment lands
in, and keep editing it there.

### Parts

Your **Parts Bin** — the Level-0 building blocks for grammar-based assembly,
organized into per-grammar bins. Multiple bins live side by side as Parts Bin
collections, so a yeast toolkit and a plant toolkit never get mixed up.

### Constructor

Where it all comes together. A multi-tab assembly bench — Traditional
cloning, Gibson, Golden Braid, MoClo, or your own custom grammar — driven by
a 4-source part picker. Every assembly, at every level, lands as one complete
library entry (payload + overhangs + backbone) that carries every parent
feature forward, so you can trace a finished L3 construct all the way back to
its L0 parts right from the Library panel.

### Simulator

In-silico PCR and agarose gels. Pick a template from your library, run the
PCR, then save the amplicon back to the library or send it to a gel lane.
Gels render at 0.5–4% with a real Helling–Goodman–Boyer mobility curve; stack
several amplicons side by side, save the whole gel to reload later, or cite
it as `&<gel>` in your lab notebook.

### Sequencing

Verify your constructs against real reads. Drop in a Plasmidsaurus `.zip` and
walk three numbered tabs — pick the run, pick the sample, pick the target
plasmid — then **Align**. The read lands as a colored bar on the plasmid's
linear map (blue match / red mismatch / gray gap) with its name painted
right onto the bar so a multi-read pile-up stays readable. Zoomed all the
way out, each cell is shaded by how much of its span actually binds —
solid blue where it matches, a red shade that deepens with the mismatch
density, gray for gaps — so a partially-binding read reads as a blue/red/
gray patchwork and even a single-base mismatch still shows red in its
region. **Click anywhere on a read's bar to jump the sequence panel to
that exact spot** — centered and highlighted — so you can land on a
misaligned stretch (or the precise base to re-edit) without scrolling;
the full per-base alignment view is still a keystroke away in the
Alignment Manager. **Bulk auto-align**
matches a whole results folder against your library in one pass — and its
confirm window shows each read's real **identity, mismatched-base, and gap
counts** (computed by actually aligning, not just the name/k-mer match
score), ready when the window opens so you can see how clean every read is
before you commit. The
**Verification Report** grades every construct (✓ verified / ⚠ near-match /
~ partial / ✗ divergent) in one sortable table — click a row to jump to the
first variant. The **Alignment Manager** lists every stored alignment with
its identity, mismatched-base count, and gap count, so one glance tells you
how clean each read is — and an identity that isn't a true 100% never rounds
up to "100%" (a single off-by-one base reads as e.g. `99.99%`, not a false
perfect score). The Library panel even shows a per-plasmid **Seq** badge so
you can see what's been verified at a glance, alongside a **Kind** badge
(`○` plasmid · `/` fragment · `≈` amplicon · `ρ` protein) telling you what
each entry is.

### Experiments

A genuine lab notebook, in markdown. A split-pane editor, entries grouped
into **projects** (the way plasmids group into collections), and live colored
cross-references — type `@plasmid`, `!action`, or `&gel` and double-click (or
`Ctrl+G`) to jump straight to the source. Attach images, and spellcheck with
`F7` against a dictionary you can grow.

### History

Every plasmid you build through SpliceCraft remembers how it was made —
whether you cloned it via Golden Braid, traditional digest/ligation, Gibson,
or PCR, or just edited and saved it. **History** opens with a **Protocol**
summary — a numbered recipe that reads left → right like the bench (*"assemble
pProm + pCDS_GFP + pTerm into pENTR_L1 → TU_GFP ✂ Esp3I"*, with a symbol
legend) — above a **lineage tree** that opens collapsed to the finished plasmid
and its direct inputs and lets you drill in as deep as you like. Selecting a
step shows its detail, including the **primers** used for a PCR. Every step is
**dated** — its date and time (e.g. *JUN 9 2026 14:30*, written slash-free so it
can't be misread as MM/DD vs DD/MM) sits right beside the action. A backbone or
part reused across branches is shown once and then referenced, so even a
multi-part Golden Braid / MoClo build reads at a glance. The same history rides
along when you import or re-export a CommercialSaaS `.dna` file — and that file's
own creation date lands on its top step. "How did I make this again?" is always
one keystroke away.

### File & Settings

The housekeeping. **File** opens local files, fetches from NCBI, saves,
exports (GenBank / FASTA / GFF3), bulk-imports a folder, and restores from
backup. Every GenBank SpliceCraft writes (library entries, exports, autosaves)
records a `Created by SpliceCraft v… on …` line in its COMMENT, so a file's
origin is always traceable. It also runs record-level jobs — including the **selection→cloning hub**
(**Alt+Shift+P**): highlight any stretch of DNA in the sequence panel and pick a
pipeline — **Traditional** restriction cloning, **Golden Braid** / **MoClo** (or
any custom grammar) domestication, or **Gibson** assembly. Each opens
pre-populated with the selection *and its features*. The **Traditional** branch
names the amplicon, then the cut-site dialog **steers you to a working enzyme
pair** — it marks any enzyme
whose site falls inside your selection (`✗`) or that's Type IIS, lists the
usable ones first, and pre-picks a viable directional pair; pick a **destination
vector** there too and it flags enzymes that vector can't be opened with (`⚠`),
refines the suggestion to a pair that cuts both, and pre-loads that vector into
the Constructor as the backbone. SpliceCraft then designs the cut-site-tailed
PCR primers, builds the amplicon — carrying every annotation your highlight
spans, even when the highlight crosses the plasmid origin — **saves the named
amplicon to your library** as a reusable linear PCR product (its features and
both run primers travel with it, the primers drawn as primer-binding sites), and
opens the Constructor's Traditional cloning tab (pre-loaded when you chose a
vector — just Simulate). Simulating **digests the amplicon and purifies away the
off-cuts** — exactly like cutting and gel-purifying at the bench — so the
recognition site reforms at each junction and *none* of the primer pad / outside-
the-cut bases carry into the construct (no stray extra bases in your clone). The
cloned product keeps those insert features, shows where the cloning primers bind
(the unbound 5′ enzyme-site tail drawn as a flap), and flags any vector feature
your cut sites land inside as *(disrupted)*, so dropping an insert into a lacZα
MCS reads as the knock-out it is. It's also where you **Migrate
Data** — package your *entire* setup
(library, collections, parts, primers, features, grammars, codon tables,
settings, lab notebook, and full construction history) into one portable,
compressed `.zip` and import it into another install, so a fresh machine picks
up exactly where you left off (the import snapshots your current data first and
verifies every file's checksum before replacing it, and refuses anything that
isn't a genuine SpliceCraft archive). And it's home to **Master Delete**, a
triple-gated full wipe for
when you genuinely want a clean slate (typed `YES`, a default-No confirm, and
a cool-down on the button; no shortcut, no API). **Settings** collapses every
toggle (restriction overlay, primer-binding length, and more) into one dialog,
with launchers for the grammar, entry-vector, enzyme-collection, and
codon-table editors. Every button has a concise hover tooltip explaining what
it does; a **Show button tooltips** switch in Settings turns them all off if
you'd rather not see them.

Want to drive all of this from a script or an agent? There's a 100+ endpoint
localhost JSON API (`splicecraft --agent`) and a stdlib-only CLI sidecar
(`splicecraft-cli`) — see [`docs/agent-api.md`](docs/agent-api.md) and
[`docs/cli.md`](docs/cli.md).

Full feature reference: [`docs/features.md`](docs/features.md).

## Documentation

| Topic                         | Where                                                                |
|-------------------------------|----------------------------------------------------------------------|
| Install methods               | [`docs/install.md`](docs/install.md)                                |
| First five seconds with pUC19 | [`docs/getting-started.md`](docs/getting-started.md)                |
| Full feature list             | [`docs/features.md`](docs/features.md)                              |
| Keybindings + menus           | [`docs/keybindings.md`](docs/keybindings.md)                        |
| Data safety + backups         | [`docs/data-safety.md`](docs/data-safety.md)                        |
| Agent API (HTTP)              | [`docs/agent-api.md`](docs/agent-api.md)                            |
| CLI sidecar                   | [`docs/cli.md`](docs/cli.md)                                        |
| Architecture                  | [`docs/architecture.md`](docs/architecture.md)                      |
| Sacred invariants             | [`CLAUDE.md`](CLAUDE.md)                                            |
| Contributing                  | [`CONTRIBUTING.md`](CONTRIBUTING.md)                                |
| Security policy               | [`SECURITY.md`](SECURITY.md)                                        |
| v1.0.0 acceptance gate        | [`V1_GATE.md`](V1_GATE.md)                                          |
| Changelog                     | [`CHANGELOG.md`](CHANGELOG.md)                                      |
| Release checklist             | [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md)                      |

## Tests

```bash
python3 -m pytest -n auto -q                  # full suite (~5–6 min on 8 cores)
python3 -m pytest tests/test_dna_sanity.py    # biology correctness only (< 2 s)
python3 -m pytest tests/test_perf_regression.py  # perf gates (~3 s)
```

All tests run offline against synthetic `SeqRecord`s and monkeypatched data
paths; the autouse `_protect_user_data` fixture in `tests/conftest.py`
guarantees no test can write to real user files.

## Maintenance

SpliceCraft is actively maintained. The maintainer is a practicing
bioengineer running real cloning workflows in it daily; releases typically go
out the same week a problem surfaces at the bench. Issues and PRs welcome at
[github.com/Binomica-Labs/SpliceCraft/issues](https://github.com/Binomica-Labs/SpliceCraft/issues).

See [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a non-trivial PR — it
walks through the sacred invariants, the test cadence, and the
security-sensitive code surfaces.

## License

MIT
