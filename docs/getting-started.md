# Getting started

The minimum you need to know to be productive on day one.

## Launch

```bash
splicecraft                       # empty canvas
splicecraft L09137                # fetch pUC19 from NCBI on launch
splicecraft myplasmid.gb          # local GenBank
splicecraft mything.dna           # popular commercial plasmid editor format
```

Press `?` once running for the full keyboard-shortcut reference,
rendered as a Markdown modal so you can drag-select a combo to copy.

## First five seconds with pUC19

```bash
splicecraft L09137
```

What you should see:

- **Circular braille map** in the centre — pUC19 ring with feature
  arcs in distinct colours.
- **Feature sidebar** on the right listing CDS, primer_bind, and
  rep_origin features.
- **Library panel** on the left (empty until you add this plasmid
  via `Alt+K`).
- **Sequence panel** at the bottom, two-strand, with the cursor at
  bp 1.

Try these in order:

1. `r` — toggle restriction-site overlay. Watch BamHI, HindIII,
   EcoRI, and others light up on the map and sequence panel.
2. Click on the EcoRI site — upstream bases tint blue, downstream
   red, with the staggered overhang flipped between strands.
3. `v` — toggle linear view. `+`/`-` zoom; `[`/`]` pan; `0` reset.
4. `Enter` on the sequence panel — highlight the feature enclosing
   the cursor.
5. `Ctrl+A`, `Ctrl+C` — select all + copy the top strand.
6. `Alt+C` — copy the reverse complement instead.
7. `Alt+K` — add ("keep") pUC19 in the library.
8. `Ctrl+Q` — quit. Re-launch with `splicecraft`; pUC19 is in the
   panel.

## Three workflows that motivate the design

### "I'm planning a cloning."

`Constructor → Traditional` or `Constructor → Gibson` or `Constructor
→ Golden Braid`. Pick your insert source (current plasmid / library
entry / free-form PCR product / Parts Bin), pick your vector, simulate
the digest + ligation. Save the product back to the library with full
construction-history XML preserved for downstream auditability.

See [Features → Cloning](features.md#cloning).

### "I need diagnostic primers for this region."

`Ctrl+P` opens the Primer Design workbench. Choose detection /
cloning / Golden Braid / generic, pick the target region, click
Design. Primer3 returns ranked candidates; promote them to
`primer_bind` features on the map or save to the persistent primer
library (Designed → Ordered → Validated lifecycle).

See [Features → Primer design](features.md#primer-design).

### "I have a `.dna` archive from the previous PI."

`File → Bulk import folder…`, point at the archive, give the
collection a name. Every `.dna` / `.gb` / `.gbk` file inside is
loaded independently; failures per file are isolated and surfaced in
a notify summary. The popular commercial plasmid editor's
construction-history XML is preserved on import and re-emitted on
save — so the lineage of a multi-step build survives the round-trip.

See [Features → Library](features.md#library).

## Where to go next

- [**Features overview**](features.md) — every workflow the
  workbench supports.
- [**Keybindings + menus**](keybindings.md) — the full reference.
- [**Agent API**](agent-api.md) — drive the running session from an
  external AI agent or script.
- [**Data safety**](data-safety.md) — what gets backed up, where,
  and how to restore.
