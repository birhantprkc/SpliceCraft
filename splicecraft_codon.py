"""splicecraft_codon — the codon optimizer (Phase D, layer L2).

The mission-critical codon-optimization core, extracted from the hub: the
genetic-code table + the reverse-translation optimizer (_codon_optimize /
_codon_allocate / _codon_build_aa_map), CAI / GC metrics, forbidden-site
scrubbing (_codon_fix_sites / _codon_forbidden_sites), and codon-table TSV
parsing / search. The table ACCESSORS (_codon_tables_load/save/get) +
_CODON_GENETIC_CODE live in dataaccess; the enzyme catalog is reached via
_state._all_enzymes_hook (the restriction scanner's hook). The Kazusa /
genome-datasets network + the Rich usage-chart renderer stay hub-side
(separate concerns) for a later step. _MUT_* (mutagenesis codon usage) is
primer code, not here. Re-exported by the hub so sc.<name> + every call site
resolves unchanged.

ZERO-TOLERANCE subsystem (project_codon_optimizer_mission_critical) — guarded
by test_codon's property tests: round-trip, ACGT-only, in-frame, no internal
stop, no codon->AA mismatch.
"""
from __future__ import annotations

import re
from datetime import date as _date

import splicecraft_state as _state
from splicecraft_logging import _log
from splicecraft_biology import _forbidden_hit_set, _iupac_pattern, _mut_revcomp
from splicecraft_util import _natural_sort_key
from splicecraft_dataaccess import (
    _CODON_GENETIC_CODE, _codon_tables_load, _codon_tables_save, _get_setting,
)


# Standard genetic code for CDS translation (no biopython dependency)
_CODON_TABLE: dict[str, str] = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


# NCBI genetic-code tables other than the standard (table 1) are resolved
# lazily from Biopython and cached. Table 1 returns the hand-rolled
# `_CODON_TABLE` above (fast path + identical behaviour for the
# overwhelmingly-common case).
_CODON_TABLE_BY_ID: "dict[int, dict[str, str]]" = {1: _CODON_TABLE}


def _codon_table_for(table_id: "int | None") -> "dict[str, str]":
    """Return the codon→AA map for an NCBI genetic-code id (the GenBank
    ``/transl_table`` qualifier).

    Table 1 (or None / falsy) is the standard code. Other ids — 2
    (vertebrate mito), 4 (Mycoplasma / Spiroplasma + mold/protozoan
    mito), 5 (invertebrate mito), 11 (bacterial/plastid), … — are built
    once from Biopython's ``CodonTable`` and cached. Reassigned codons
    (e.g. ``TGA`` = Trp in tables 2/4/5, ``AGR`` = stop in table 2) then
    translate CORRECTLY instead of rendering a wrong residue + a false
    premature-stop ⚠ on the map. An unknown / invalid id falls back to
    the standard code with a warning rather than crashing — a hand-edited
    ``/transl_table=99`` must not break the map. Stops map to ``"*"`` to
    match ``_CODON_TABLE`` exactly."""
    try:
        tid = int(table_id) if table_id else 1
    except (TypeError, ValueError):
        tid = 1
    cached = _CODON_TABLE_BY_ID.get(tid)
    if cached is not None:
        return cached
    try:
        from Bio.Data import CodonTable as _BioCodonTable
        ct = _BioCodonTable.unambiguous_dna_by_id[tid]
        m = dict(ct.forward_table)          # sense codons (ACGT only)
        for stop in ct.stop_codons:
            m[stop] = "*"
    except Exception:  # noqa: BLE001  (KeyError for bad id, ImportError, …)
        _log.warning(
            "Unknown genetic-code table %r; falling back to the standard "
            "code (table 1).", table_id,
        )
        m = _CODON_TABLE
    _CODON_TABLE_BY_ID[tid] = m
    return m


_STOP_CODONS = frozenset(("TAA", "TAG", "TGA"))


_CODON_FIX_POS_RE = re.compile(r"codon (\d+)")


def _codon_fix_mutation_positions(mutations: list[str]) -> list[int]:
    """Given the string list returned by :func:`_codon_fix_sites`, return
    each mutation's 0-based codon-start nucleotide position in the insert.

    The mutation format is fixed by ``_codon_fix_sites`` — ``(codon N …)``
    where ``N`` is 1-based. A missing / malformed entry gets ``-1`` so
    callers can filter without raising.
    """
    out: list[int] = []
    for m in mutations:
        match = _CODON_FIX_POS_RE.search(m) if isinstance(m, str) else None
        if match:
            out.append((int(match.group(1)) - 1) * 3)
        else:
            out.append(-1)
    return out


# Forbidden sites for the optimizer's restriction-site fixer. Keys are the
# forward site only; the fixer adds the reverse complement automatically if
# the site is non-palindromic.
_CODON_DEFAULT_FORBIDDEN: dict[str, str] = {
    "BsaI":    "GGTCTC",
    "BsmBI":   "CGTCTC",
    "BbsI":    "GAAGAC",
    "EcoRI":   "GAATTC",
    "NdeI":    "CATATG",
    "XhoI":    "CTCGAG",
    "BamHI":   "GGATCC",
    "HindIII": "AAGCTT",
    "NcoI":    "CCATGG",
    "SalI":    "GTCGAC",
    "KpnI":    "GGTACC",
    "SacI":    "GAGCTC",
}


def _codon_tables_add(name: str, taxid: str, raw: dict,
                      source: str = "user") -> dict:
    """Insert or replace a table in the registry. Dedup key is taxid when
    non-empty, else name. Returns the stored entry."""
    entries = _codon_tables_load()
    taxid = str(taxid or "").strip()
    name  = (name or "?").strip() or "?"
    entry = {
        "name":   name,
        "taxid":  taxid,
        "source": source,
        "added":  _date.today().isoformat(),
        "raw":    dict(raw),
    }
    def _same(e):
        if taxid and e.get("taxid") == taxid:
            return True
        if not taxid and e.get("name") == name:
            return True
        return False
    kept = [e for e in entries if not _same(e)]
    kept.append(entry)
    _codon_tables_save(kept)
    return entry


_CODON_TSV_MAX_CHARS = 1_000_000   # 64 codons; even a verbose export is KB


# Three-letter (and stop-alias) → one-letter for TSV amino-acid columns.
_CODON_TSV_AA3 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "STOP": "*", "TER": "*", "END": "*",
}


def _parse_codon_tsv(text: str) -> dict:
    """Parse a tab/whitespace/comma-delimited codon-usage table into the
    in-memory ``{codon: (aa, count)}`` registry shape. Pure — no I/O.

    Each data row needs a 3-base codon and a usage count. An optional
    amino-acid column (1-letter, 3-letter, or ``*`` / ``Stop``) is
    validated against the standard code and otherwise derived from
    ``_CODON_GENETIC_CODE``. Rows whose first token isn't an ACGT/U codon
    (headers), blank lines, and ``#`` comments are skipped. Counts may be
    ints or floats (rounded); a fraction-only row (0..1) is scaled by
    1000 so relative preference survives. ``U`` is accepted and folded to
    ``T``.

    Raises ``ValueError`` with a readable message on a bad codon, a
    non-numeric / negative count, an AA/codon mismatch, a duplicate
    codon, or a file with zero usable rows — callers surface it in a
    status line rather than crashing.
    """
    if not isinstance(text, str):
        raise ValueError("codon table must be text")
    if len(text) > _CODON_TSV_MAX_CHARS:
        raise ValueError("codon table is too large (1 MB cap)")
    raw: "dict[str, tuple[str, int]]" = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        toks = [t for t in re.split(r"[\s,]+", s) if t]
        if not toks:
            continue
        codon = toks[0].upper().replace("U", "T")
        if len(codon) != 3 or any(b not in "ACGT" for b in codon):
            continue   # header / non-codon row — skip silently
        if codon not in _CODON_GENETIC_CODE:
            raise ValueError(f"line {lineno}: {codon!r} is not a valid codon")
        if codon in raw:
            raise ValueError(f"line {lineno}: duplicate codon {codon!r}")
        expected_aa = _CODON_GENETIC_CODE[codon]
        aa_given: "str | None" = None
        numeric: list[str] = []
        for t in toks[1:]:
            tu = t.upper()
            if len(tu) == 1 and (tu in "ACDEFGHIKLMNPQRSTVWY" or tu in "*.X"):
                aa_given = "*" if tu in "*.X" else tu
            elif tu in _CODON_TSV_AA3:
                aa_given = _CODON_TSV_AA3[tu]
            else:
                try:
                    float(t)
                    numeric.append(t)
                except ValueError:
                    pass   # stray non-numeric, non-AA token — ignore
        if aa_given is not None and aa_given != expected_aa:
            raise ValueError(
                f"line {lineno}: codon {codon!r} encodes {expected_aa!r} "
                f"but the file says {aa_given!r}"
            )
        if not numeric:
            raise ValueError(
                f"line {lineno}: no count/frequency column for {codon!r}"
            )
        ints = [t for t in numeric if float(t) == int(float(t))]
        count = (int(round(float(ints[-1]))) if ints
                 else int(round(float(numeric[-1]) * 1000)))
        if count < 0:
            raise ValueError(f"line {lineno}: negative count for {codon!r}")
        raw[codon] = (expected_aa, count)
    if not raw:
        raise ValueError(
            "no codon rows found — expected lines like 'GCT A 120' or "
            "'GCT 120' (codon then count)"
        )
    return raw


def _codon_name_parts(name: str) -> tuple[str, str]:
    """Return (genus, species) as lowercased tokens from an entry name.

    Genus = first whitespace-delimited token; species = second token (or "").
    Names like "E. coli K12" yield ("e.", "coli"); "Escherichia coli" yields
    ("escherichia", "coli"). No normalization between abbreviated and
    unabbreviated genera — users search what they see.
    """
    parts = (name or "").strip().split()
    genus   = parts[0].lower() if parts else ""
    species = parts[1].lower() if len(parts) > 1 else ""
    return genus, species


def _codon_search(query: str, entries: "list | None" = None) -> list[dict]:
    """Ranked search over taxid, genus, species, and full name.

    Rank 0: taxid exact match
    Rank 1: taxid prefix match
    Rank 2: genus prefix (first whitespace token of name)
    Rank 3: species prefix (second whitespace token)
    Rank 4: substring anywhere in the full name

    Results are sorted by (rank, name) so same-genus entries cluster and
    the strongest match wins. An empty/whitespace query returns the
    registry naturally sorted by display name (`Escherichia coli K12`
    before `Escherichia coli K-12 MG1655`) so the unfiltered table
    reads in human-friendly order rather than disk-insertion order.
    Same-rank ties under a non-empty query also use the natural-sort
    secondary key (e.g. `E. coli K12` before `K-12 MG1655`).
    """
    if entries is None:
        entries = _codon_tables_load()
    q = (query or "").strip().lower()
    if not q:
        # Empty query → natural-sort the registry by display name so
        # `Escherichia coli K12` lands near the top in stable order
        # regardless of the JSON-file insertion order.
        return sorted(
            entries,
            key=lambda e: _natural_sort_key(str(e.get("name") or "")),
        )
    ranked: list[tuple[int, tuple, dict]] = []
    for e in entries:
        name_lc  = str(e.get("name", "")).lower()
        taxid_lc = str(e.get("taxid", "")).lower()
        genus, species = _codon_name_parts(e.get("name", ""))
        if taxid_lc and taxid_lc == q:
            rank = 0
        elif taxid_lc and taxid_lc.startswith(q):
            rank = 1
        elif genus and genus.startswith(q):
            rank = 2
        elif species and species.startswith(q):
            rank = 3
        elif q in name_lc:
            rank = 4
        else:
            continue
        # Secondary key uses natural sort so same-rank matches come
        # back in human-friendly order (`E. coli K12` before `K-12 MG1655`).
        ranked.append((rank, _natural_sort_key(name_lc), e))
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in ranked]


def _codon_build_aa_map(raw: dict) -> tuple[dict, dict]:
    """Given {codon: (aa, count)}, return (aa_codons, codon_frac) where
    aa_codons[aa] = [(codon, frac), ...] sorted by fraction descending, and
    codon_frac[codon] = fractional usage for its amino acid.

    Codon-integrity defense-in-depth: the amino-acid label is taken from
    `_CODON_GENETIC_CODE`, NOT the `aa` stored in `raw`. Every table loader
    already forces the canonical label, so for any real table this is a no-op
    (byte-identical output). But a hand-built in-memory table (a test, a
    `codon_table=` override) could mislabel a codon — and a mislabel here
    would silently emit a wrong-residue codon that `_mut_translate` reads back
    as a different protein, with no error raised, in a subsystem where a
    silent wrong-protein round-trip is catastrophic. Deriving the AA from the
    standard code closes that hole; codons outside the 64 ACGT keys (N / gaps)
    are skipped."""
    from collections import defaultdict
    aa_total: dict = defaultdict(int)
    codon_aa: dict = {}
    for codon, (_aa, count) in raw.items():
        canon = _CODON_GENETIC_CODE.get(codon)
        if canon is None:               # non-ACGT codon (N / gap) — not optimizable
            continue
        codon_aa[codon] = canon
        aa_total[canon] += int(count)
    codon_frac: dict = {}
    for codon, (_aa, count) in raw.items():
        canon = codon_aa.get(codon)
        if canon is None:
            continue
        total = aa_total.get(canon, 0) or 1
        codon_frac[codon] = count / total
    aa_codons: dict = defaultdict(list)
    for codon, canon in codon_aa.items():
        if canon == "*":
            continue
        aa_codons[canon].append((codon, codon_frac[codon]))
    for aa in aa_codons:
        aa_codons[aa].sort(key=lambda x: -x[1])
    return dict(aa_codons), codon_frac


def _codon_allocate(codons: list, n: int) -> list:
    """Pick ``n`` codons from ``[(codon, frac), ...]`` so the chosen multiset
    matches the frequency distribution as closely as possible (largest-
    remainder apportionment), then interleave them so no single codon
    clusters at the front. Deterministic: equal remainders break by input
    order, so the same ``(codons, n)`` always yields the same list.

    Shared by amino-acid positions AND frequency-matched stop codons. The
    return is GUARANTEED to be exactly length ``n`` (empty when ``n <= 0``):
    for a normalized table the apportionment already sums to ``n``, but a
    hand-rolled / truncated table whose fractions don't sum to 1 is padded
    with (or truncated to) the most-frequent codon so the caller can never
    receive a short list that would leave a position unfilled — i.e. NO
    rogue/empty codon ever reaches the output. Every codon in ``codons`` is
    synonymous (same residue, or all stops), so padding never changes the
    encoded amino acid."""
    if n <= 0:
        return []
    if len(codons) == 1:
        return [codons[0][0]] * n
    targets: list = []
    remainders: list = []
    allocated = 0
    for codon, frac in codons:
        exact = n * frac
        floored = int(exact)
        targets.append(floored)
        remainders.append((exact - floored, len(targets) - 1))
        allocated += floored
    shortage = n - allocated
    remainders.sort(key=lambda x: -x[0])
    for k in range(max(0, shortage)):
        # `% len` guards a malformed table whose rounding leaves a shortage
        # larger than the codon count; for a normalized table
        # shortage <= len(remainders), so this is a plain index.
        targets[remainders[k % len(remainders)][1]] += 1
    queues = [[codon] * cnt
              for (codon, _frac), cnt in zip(codons, targets) if cnt > 0]
    interleaved: list = []
    i = 0
    while any(queues):
        q = queues[i % len(queues)]
        if q:
            interleaved.append(q.pop(0))
        i += 1
    # Length guarantee (defensive — a no-op for any table whose per-AA
    # fractions sum to 1): pad short with the most-frequent synonym,
    # truncate long.
    if len(interleaved) < n:
        interleaved += [codons[0][0]] * (n - len(interleaved))
    return interleaved[:n]


def _codon_optimize(protein: str, raw: dict, *, stops: int = 1) -> str:
    """Frequency-matching codon optimization: distribute synonymous codons
    across the protein so each amino acid's codon distribution matches
    the target organism's overall usage frequencies. Raises ValueError on
    unknown amino acids.

    Stop codons (2026-05-30): a run of trailing ``*`` in ``protein`` is
    honored verbatim — ``"MGK*"`` → one stop, ``"MGK**"`` → two, ``"MGK***"``
    → three — and ``stops`` is then ignored. A ``*`` anywhere but that
    trailing run raises ValueError (a premature stop is never silently
    encoded). When the protein carries no trailing ``*``, ``stops`` (default
    1, negatives clamped to 0) stop codons are appended. A SINGLE stop is
    always ``TAA`` (the strongest terminator / lowest readthrough in E. coli,
    and the historical default that downstream site-scrubbing assumes); 2+
    stops are frequency-matched to the table's OWN stop-codon usage so the
    emitted run resists readthrough with organism-appropriate diversity,
    falling back to ``TAA`` only when the table declares no stop codons.

    The amino-acid body is apportioned by `_codon_allocate`, which excludes
    stop codons from every residue's synonym pool — so a body position can
    NEVER be a stop (no premature internal stop) and is always a real codon
    (no rogue/empty base). Output length is exactly ``3*len(body) +
    3*n_stops``.

    Distinct from Angov-style codon HARMONIZATION (Angov 2011), which
    requires a SOURCE organism's codon usage to preserve relative
    rare-codon positions in the target (those positions encode
    translation pauses important for cotranslational folding). We only
    consume the target table, so this is pure optimization, not
    harmonization. Renamed 2026-05-01 to stop misleading users."""
    aa_codons, codon_frac = _codon_build_aa_map(raw)
    # Peel a trailing run of stop requests; a '*' anywhere else is an error.
    body = protein
    n_trailing = 0
    while body and body[-1] == "*":
        body = body[:-1]
        n_trailing += 1
    if "*" in body:
        raise ValueError(
            "stop codon '*' is only allowed at the end of the protein")
    n_stops = n_trailing if n_trailing else max(0, int(stops))

    aa_positions: dict = {}
    for i, aa in enumerate(body):
        aa_positions.setdefault(aa.upper(), []).append(i)
    codon_at = [""] * len(body)
    for aa, positions in aa_positions.items():
        codons_for_aa = aa_codons.get(aa, [])
        if not codons_for_aa:
            raise ValueError(f"No codons for amino acid '{aa}' in this table")
        for pos, codon in zip(positions,
                              _codon_allocate(codons_for_aa, len(positions))):
            codon_at[pos] = codon

    if n_stops <= 1:
        tail = "TAA" * n_stops
    else:
        stop_codons = sorted(
            ((c, codon_frac[c]) for c, (a, _ct) in raw.items() if a == "*"),
            key=lambda x: -x[1])
        tail = "".join(_codon_allocate(stop_codons or [("TAA", 1.0)], n_stops))
    return "".join(codon_at) + tail


def _codon_fix_sites(dna: str, protein: str, raw: dict,
                     sites: "dict | None" = None,
                     *, has_appended_stop: bool = True) -> tuple:
    """Substitute synonymous codons to remove internal restriction sites.

    ``sites`` is a forward-strand ``{name: site}`` dict; reverse complements
    are added automatically for non-palindromic sites. Returns
    ``(new_dna, fixes)``.

    ``has_appended_stop`` (2026-05-27 audit-5 H2): set to True when ``dna``
    ends in a SYNTHETIC stop codon that the caller appended (e.g.
    ``_codon_optimize`` always appends TAA). The boundary check then
    skips the last codon to avoid silently substituting an appended
    stop. Set to False when the caller passes a raw CDS region without
    an appended stop — without this kwarg pre-2026-05-27 the last 1-2
    codons were silently skipped for any forbidden site overlapping
    them, and the caller's "remaining sites" check then aborted with
    "no synonymous alternative" when the code had actually refused to
    try the swap.

    Hardening (2026-04-21) — a candidate swap is accepted only if it:
      1. Actually removes the target site at the current position, AND
      2. Introduces **no new** forbidden site (forward or RC) anywhere
         in the full sequence — counted against the full input site set,
         not just the enzyme currently being iterated. This guards
         against the classic failure mode of fixing BsaI by accidentally
         spawning an Esp3I (or the RC of either) a few bases away.

    Multiple occurrences of the same site are processed left-to-right;
    each swap only needs to remove its own position, so repeated sites
    of the same enzyme are handled correctly (pre-2026-04-21 the check
    was ``site not in test`` which failed when two copies were present).
    """
    if sites is None:
        sites = _CODON_DEFAULT_FORBIDDEN
    expanded: dict = {}
    for name, site in sites.items():
        site = str(site or "").upper()
        if not site:
            continue
        # Validate as an IUPAC recognition site; skip (rather than crash the
        # whole optimize) an enzyme whose site carries a stray non-IUPAC char.
        try:
            _iupac_pattern(site)
        except ValueError:
            _log.warning("Codon site-scrub: skipping %r (%s) — not a valid "
                          "IUPAC recognition site", site, name)
            continue
        expanded[name] = site
        rc = _mut_revcomp(site)
        if rc != site:
            expanded[f"{name}_rc"] = rc
    # Flat tuple of every forbidden pattern (forward + RC). Used by the
    # per-swap cross-check to veto swaps that would introduce a NEW
    # pattern anywhere (different enzyme, different strand, different
    # position — the check is global).
    all_forbidden = tuple(expanded.values())
    # Precompiled (cached) IUPAC matchers for the per-enzyme outer scan, so
    # a degenerate site is actually located (not literal-substring searched).
    site_pats = {nm: _iupac_pattern(st) for nm, st in expanded.items()}
    aa_codons, _ = _codon_build_aa_map(raw)
    dna_list = list(dna)
    fixes: list[str] = []
    for enzyme, site in expanded.items():
        pat = site_pats[enzyme]
        pos = 0
        while True:
            seq = "".join(dna_list)
            m = pat.search(seq, pos)
            if m is None:
                break
            idx = m.start()
            fixed = False
            lo_codon = max(0, (idx // 3) - 1)
            hi_codon = (idx + len(site)) // 3 + 2
            before_hits = _forbidden_hit_set(seq, all_forbidden)
            for codon_idx in range(lo_codon, hi_codon):
                codon_start = codon_idx * 3
                # 2026-05-27 (audit-5 H2): the `- 3` skip is only
                # valid when the caller appended a synthetic stop
                # codon to `dna_list`. Raw CDS regions (passed by
                # `_design_gb_primers`) don't end in an appended stop,
                # and skipping their last codon silently leaves a
                # forbidden site overlapping the C-terminus unfixed.
                last_safe = (
                    len(dna_list) - 3 if has_appended_stop
                    else len(dna_list)
                )
                if codon_start + 3 > last_safe:
                    break
                if codon_idx >= len(protein):
                    break
                aa = protein[codon_idx].upper()
                current = "".join(dna_list[codon_start:codon_start + 3])
                for alt, frac in aa_codons.get(aa, []):
                    if alt == current:
                        continue
                    test = dna_list[:]
                    test[codon_start:codon_start + 3] = list(alt)
                    test_seq = "".join(test)
                    after_hits = _forbidden_hit_set(test_seq, all_forbidden)
                    # (1) Target site at idx must be gone.
                    if (site, idx) in after_hits:
                        continue
                    # (2) No new forbidden hit appears anywhere.
                    #     (Existing hits elsewhere are fine — later
                    #     iterations of this loop will process them.)
                    if after_hits - before_hits:
                        continue
                    dna_list = test
                    strand = " (rc)" if enzyme.endswith("_rc") else ""
                    fixes.append(
                        f"{enzyme.replace('_rc', '')}{strand} at nt {idx+1}: "
                        f"{current}→{alt} (codon {codon_idx+1} {aa}, "
                        f"freq={frac:.3f})"
                    )
                    fixed = True
                    break
                if fixed:
                    break
            if not fixed:
                pos = idx + 1
    return "".join(dna_list), fixes


def _codon_forbidden_sites() -> "dict[str, str]":
    """Resolve the persisted ``codon_forbidden_enzymes`` name list into the
    ``{name: recognition_site}`` map that `_codon_fix_sites` consumes, drawing
    sites from the merged built-in + custom enzyme set. A name that's unknown
    (e.g. a custom enzyme since deleted) or whose site isn't valid IUPAC is
    skipped. An empty / missing list means 'scrub nothing'. Default ['BsaI'].
    """
    names = _get_setting("codon_forbidden_enzymes", ["BsaI"])
    if not isinstance(names, list):
        names = ["BsaI"]
    enz = _state._all_enzymes_hook()
    out: dict[str, str] = {}
    for n in names:
        info = enz.get(str(n))
        if not info:
            continue
        site = str(info[0] or "").upper()
        if not site:
            continue
        try:
            _iupac_pattern(site)
        except ValueError:
            continue
        out[str(n)] = site
    return out


def _codon_cai(dna: str, raw: dict) -> float:
    """Codon Adaptation Index (geometric mean of per-codon freq ÷ peak freq
    of its amino-acid synonymy group). Skips stops and unknown codons."""
    import math
    aa_codons, codon_frac = _codon_build_aa_map(raw)
    w: list[float] = []
    for i in range(0, len(dna) - 2, 3):
        codon = dna[i:i + 3].upper()
        entry = raw.get(codon)
        if not entry or entry[0] == "*":
            continue
        peak = aa_codons[entry[0]][0][1] if entry[0] in aa_codons else 0.0
        if peak > 0:
            w.append(codon_frac.get(codon, 0.0) / peak)
    if not w:
        return 0.0
    return math.exp(sum(math.log(max(v, 1e-10)) for v in w) / len(w))


def _codon_gc(dna: str) -> float:
    """GC%. Empty string → 0."""
    if not dna:
        return 0.0
    gc = sum(1 for c in dna.upper() if c in "GC")
    return gc / len(dna) * 100.0
