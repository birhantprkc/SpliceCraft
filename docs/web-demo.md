# Hosting the SpliceCraft web demo (splicecraft.bio)

The web demo is **the real `splicecraft.py`**, run server-side and streamed to a
browser terminal via Textual Web. There is no separate web codebase, so the
browser demo is **1-to-1 with the terminal tool by construction** — same braille
maps, same black theme, same behaviour. What makes it safe + public-ready is
**demo mode** (`SPLICECRAFT_DEMO`, see `[INV-131]`) plus per-session container
isolation.

## TL;DR

```bash
pip install splicecraft textual-web        # pin the EXACT released version (see staleguard)
SPLICECRAFT_DEMO=web textual serve "python3 -m splicecraft"   # or: textual serve "splicecraft"
```

Point a CNAME from `splicecraft.bio` at the host. For a quick public URL with
no ops, `textual-web` (Textualize's hosted relay) works too; for control over
isolation + limits, self-host as below.

## The two tiers

| Env | Use | Behaviour |
|---|---|---|
| `SPLICECRAFT_DEMO=local` | previewing the demo build on your own machine | **full features**, sandboxed data dir + seeded examples |
| `SPLICECRAFT_DEMO=web` | the public deployment | **restrained** — see lockdown below |

Both force a **fresh ephemeral data dir per process** (`/tmp/splicecraft-demo-…`)
and ignore `$SPLICECRAFT_DATA_DIR`/`$XDG`, so a session can never read or write a
real library. Each launch seeds three worked examples (general plasmid,
Golden-Braid-scrubbable plasmid, two-gene operon) and shows a version banner.

## What the `web` tier locks (defence-in-depth, in-app)

These are enforced in-process *on top of* the container boundary:

- **No network egress** — every fetch helper (`fetch_genbank`, PyPI update
  check, NCBI taxid, Kazusa, genome datasets, HMM-DB / arbitrary-URL opener)
  raises, so no SSRF / outbound abuse regardless of UI path.
- **No host-FS reads** — open-file, load-part-from-file, entry-vector-from-file,
  image-attach, etc. (the main open is gated; the rest are container-contained).
- **No agent HTTP API** — `_start_agent_api` refuses even if `--agent` is passed.
- **No destructive / bulk-data ops** — data export/import (migrate), master
  delete, and the per-format exports (GenBank / FASTA / GFF / CommercialSaaS)
  all show a polite "install SpliceCraft for the full tool" message.
- **Sequence-size cap** — `_apply_record` refuses sequences > 100 kb so a
  megabase paste can't bog down the host.

The **science tools stay fully usable** on the seed (Scrub, operon
domestication, primer design, the Simulator, BLAST, codon optimisation) — they
are pure compute, not gated.

## Per-session isolation (self-host)

`textual serve` runs one Python process per browser session. For a public host:

- Run each session in a **minimal container** (no real data, no host mounts) so
  even the container-contained surfaces (in-container file reads/writes) expose
  nothing. The container is the primary boundary; demo mode is defence-in-depth.
- Apply **resource limits** (CPU/mem via cgroups, `ulimit`, a wall-clock idle
  timeout) and a **concurrency cap** (N sessions = N processes) with a queue.
- TLS + a reverse proxy (nginx/Caddy) in front; rate-limit new connections.

## Version staleguard — keeping the demo 1-to-1 / never stale

The demo serves whatever version is installed, so "never behind the terminal
build" is guaranteed by **pinning + redeploy on release**:

1. The deploy installs an **exact** version: `pip install splicecraft==X.Y.Z`
   (never a floating range), so the served build is reproducible.
2. **Redeploy on each release.** After `release.py` ships X.Y.Z to PyPI, the
   demo host re-pulls that exact version (a webhook on the PyPI publish, or a
   `release.py` post-publish hook that pokes the host, or a nightly that
   compares the installed version to PyPI's latest and redeploys on drift).
3. The **banner shows `v{__version__}`** so a session is self-identifying and
   drift is visible at a glance.

> Because demo mode blocks egress, the *in-app* "is there a newer version"
> check is intentionally off in the web tier — the staleguard lives at the
> **deploy** layer (steps 1–2), not in the sandboxed process.

## Still to wire when you stand up hosting

- The redeploy automation (step 2) — depends on where you host.
- A manual click-through of every modal in both tiers as a final human QA
  (the automated `test_demo_mode.py::TestDemoModeQA` covers the lockdown
  contract + that the core tools run on the seed).
- Optionally richer seed content (parts / primers / a Sanger read) so the
  primer + alignment tools also have a worked example out of the box.
