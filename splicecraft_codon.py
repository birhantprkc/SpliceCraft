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
from pathlib import Path

import splicecraft_state as _state
from splicecraft_logging import _log
from splicecraft_biology import _forbidden_hit_set, _iupac_pattern, _mut_revcomp
from splicecraft_util import _natural_sort_key, _sanitize_path
from splicecraft_net import (
    _HMM_DB_RETRY_BACKOFF_S, _NCBI_MAX_RESPONSE_BYTES, _build_hardened_url_opener,
    _sanitize_accession,
)
from splicecraft_fileio import _is_safe_zip_member_name
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


# ── codon usage chart (Phase D, moved from hub) ─────────────────────────────
# Renders a per-amino-acid synonymous-codon usage chart as a Rich-markup
# STRING (no Rich object dependency). _AA_NAME_3 (AA 1->3 letter) is used only
# here.
# Single-letter → three-letter amino-acid names for the genetic-code chart
# (`_render_codon_chart`). Stop is spelled "Stop" to match the canonical
# textbook wall chart. The full-name catalog lives on `AminoAcidPickerModal`.
_AA_NAME_3: dict[str, str] = {
    "A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys",
    "Q": "Gln", "E": "Glu", "G": "Gly", "H": "His", "I": "Ile",
    "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe", "P": "Pro",
    "S": "Ser", "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val",
    "*": "Stop",
}


def _render_codon_chart(raw: dict, *, rna: bool = True) -> str:
    """Render a codon-usage table as the classic textbook genetic-code grid,
    returned as a Rich-markup string.

    The layout matches the canonical wall chart: the four 1st-base blocks
    (U/C/A/G) stack vertically, the 2nd base runs across the four columns,
    and the four lines inside every cell are the 3rd base (U/C/A/G). Each
    codon is annotated with its usage *within its amino-acid family* (the
    relative synonymous usage, as a percentage), and each amino-acid family's
    single most-used codon is highlighted bold green for easy visual
    identification. The choice is FAMILY-WIDE (matching the % shown), so a
    family split across two cells — Leu, Ser, Arg, and the three stops
    (UAA/UAG/UGA, treated as one family) — yields exactly one highlight, not
    one per cell. A codon with no usage in the table is never highlighted.
    Synonymous residues are bracketed and named (3-letter; stops as "Stop").
    Codons missing from the table render a dim "·" placeholder.

    Bases display as RNA (U) by default to match the iconic chart; lookups
    are always by the stored DNA (T) key. Pure — display only, no I/O."""
    _aa_codons, codon_frac = _codon_build_aa_map(raw)

    def _count(codon: str) -> int:
        v = raw.get(codon)
        return int(v[1]) if v else 0

    # The single most-used codon in each amino-acid family is highlighted green.
    # The choice is FAMILY-WIDE (matching the relative-synonymous-usage % shown),
    # so a family split across two cells — Leu (UU+CU), Ser (UC+AG), Arg (CG+AG),
    # and the stops UAA/UAG/UGA (UA+UG) — yields exactly ONE highlight, never one
    # per cell. Stops are one family. Deterministic tie-break by codon; a family
    # with no usage in the table gets no highlight.
    _fam: dict = {}
    for _codon, _aa in _CODON_GENETIC_CODE.items():
        _fam.setdefault(_aa, []).append(_codon)
    dominant: set = set()
    for _members in _fam.values():
        champ = sorted(_members, key=lambda c: (-_count(c), c))[0]
        if _count(champ) > 0:
            dominant.add(champ)

    order = "TCAG"                       # U, C, A, G in DNA (T) coordinates
    CW, AAW, LGUT, RGUT = 17, 4, 2, 2    # cell / AA-label / gutter widths
    label_row = {1: 0, 2: 0, 3: 1, 4: 1}  # which row of a run carries the name

    def _disp(b: str) -> str:
        return "U" if (rna and b == "T") else b

    def _cell(first: str, second: str) -> list:
        """Four markup lines (3rd base = U/C/A/G) for one grid cell, each
        exactly CW visible columns wide (markup tags are zero-width)."""
        codons = [first + second + third for third in order]
        aas = [_CODON_GENETIC_CODE[c] for c in codons]
        # Group the four residues into runs of equal AA (for the brackets). The
        # green highlight is the family-wide champion computed above (`dominant`).
        run_of: dict = {}
        i = 0
        while i < 4:
            j = i
            while j < 4 and aas[j] == aas[i]:
                j += 1
            for r in range(i, j):
                run_of[r] = (i, j - i)
            i = j
        out: list = []
        for r in range(4):
            cdn = codons[r]
            show = "".join(_disp(b) for b in cdn)
            frac = codon_frac.get(cdn)
            pct = ("[dim]   ·[/dim]" if frac is None
                   else f"{round(frac * 100):>3d}%")     # 4 visible cols
            field = f"{show} {pct}"                       # 8 visible cols
            if cdn in dominant:
                field = f"[b green]{field}[/]"            # stark + easy to spot
            start, length = run_of[r]
            pos = r - start
            if length == 1:
                g = "─"
            elif pos == 0:
                g = "╮"
            elif pos == length - 1:
                g = "╯"
            elif pos == label_row[length]:
                g = "┤"
            else:
                g = "│"
            if pos == label_row[length]:
                nm = _AA_NAME_3.get(aas[r], aas[r])
                lab = (f"[red]{nm:<{AAW}}[/red]" if aas[r] == "*"
                       else f"{nm:<{AAW}}")
            else:
                lab = " " * AAW
            out.append(f" {field} [dim]{g}[/dim] {lab} ")   # CW visible
        return out

    def _rule(left: str, mid: str, right: str) -> str:
        return (" " * LGUT) + left + mid.join(["─" * CW] * 4) + right + \
               (" " * RGUT)

    width = LGUT + 1 + CW * 4 + 3 + 1 + RGUT
    lines: list = [f"{'second base':^{width}}"]
    # Column header: 2nd-base letter centred over each column.
    lines.append((" " * (LGUT + 1))
                 + " ".join(f"[b]{_disp(s):^{CW}}[/b]" for s in order)
                 + " " + (" " * RGUT))
    lines.append(_rule("┌", "┬", "┐"))
    for bi, first in enumerate(order):
        cells = [_cell(first, second) for second in order]
        for r in range(4):
            row = "│".join(cells[c][r] for c in range(4))
            left = f" [b]{_disp(first)}[/b]" if r == 1 else "  "
            right = f" [b]{_disp(order[r])}[/b]"
            lines.append(f"{left}│{row}│{right}")
        lines.append(_rule("├", "┼", "┤") if bi < 3 else _rule("└", "┴", "┘"))
    return "\n".join(lines)



# ── codon-table NETWORK builders (Phase D, moved from hub) ──────────────────
# Build a codon-usage table from a live source: Kazusa (HTML scrape) or an
# NCBI genome (datasets CDS zip -> highly-expressed-gene table). Egress is
# gated via _state._demo_block_network_hook + the splicecraft_net hardened
# opener; lazy urllib/json/zipfile/io/socket/itertools inside the fns.
def _build_heg_table_from_cds(cds_fasta_text: str,
                              mode: str = "heg") -> "tuple[dict, dict]":
    """Build a codon-usage table from a genome's CDS FASTA (the
    ``cds_from_genomic.fna`` shape NCBI Datasets ships). Pure — no I/O, no
    network — so it unit-tests against an inline FASTA string.

    ``mode``:
      * ``"heg"``    — count only highly-expressed genes (ribosomal proteins),
        the right signal for heterologous-expression optimization. Amino acids
        absent from the r-protein set (ribosomal proteins can be Cys/Trp-poor)
        are BACKFILLED from the whole-genome counts so every protein still
        optimizes — backfill only supplies a synonym pool for an otherwise-
        missing residue; it never overrides the HEG bias where it exists.
      * ``"genome"`` — count every CDS (whole-genome average; what Kazusa
        approximates).

    Returns ``(raw, stats)`` where ``raw`` is the ``{codon: (aa, count)}``
    registry shape consumed by `_codon_optimize` / `_codon_tables_add`, and
    ``stats`` carries mode / n_cds_total / n_cds_heg / n_codons / aa_coverage /
    backfilled / gc3 for the status line. Each record is read in frame 0 (CDS
    FASTA is in-frame); codons containing non-ACGT (N, gaps) are skipped — a
    codon is counted iff it is one of the 64 keys of `_CODON_GENETIC_CODE`.

    Raises ``ValueError`` on an unknown ``mode``, when no usable CDS were found,
    or (heg mode) when the genome carries no ribosomal-protein CDS — callers
    surface it in a status line.
    """
    if mode not in ("heg", "genome"):
        raise ValueError(f"unknown mode {mode!r} (expected 'heg' or 'genome')")
    import io
    from collections import Counter

    def _is_rprotein(header: str) -> bool:
        h = header.lower()
        if "transferase" in h:           # rimI / prmA modification enzymes
            return False                 # carry "ribosomal protein" but aren't one
        if "ribosomal protein" in h:
            return True
        # NCBI cds_from_genomic deflines carry [gene=rplB] / [gene=rpsL] /
        # [gene=rpmA] for the 50S/30S/large-subunit r-proteins.
        return bool(re.search(r"\[gene=rp[lsm]", h))

    genome_counts: "Counter" = Counter()
    heg_counts: "Counter" = Counter()
    n_cds_total = 0
    n_cds_heg = 0

    def _consume(hdr: "str | None", parts: list) -> None:
        """Count one CDS record's in-frame ACGT codons into the two Counters."""
        nonlocal n_cds_total, n_cds_heg
        if hdr is None:
            return
        s = "".join(parts).upper().replace("U", "T")
        usable = (len(s) // 3) * 3
        if usable <= 0:
            return
        n_cds_total += 1
        is_heg = _is_rprotein(hdr)
        if is_heg:
            n_cds_heg += 1
        for i in range(0, usable, 3):
            codon = s[i:i + 3]
            if codon in _CODON_GENETIC_CODE:   # 64 ACGT keys → skips N/gaps
                genome_counts[codon] += 1
                if is_heg:
                    heg_counts[codon] += 1

    # Stream line-by-line (lazy — avoids materialising a whole-file line list
    # AND a records list; at the 256 MB download cap that's the difference
    # between ~1× and ~2-3× the input in peak memory). Each record is consumed
    # the instant its sequence is complete; only one record is held at a time.
    header: "str | None" = None
    seq_parts: list = []
    for raw_line in io.StringIO(cds_fasta_text):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            _consume(header, seq_parts)
            header = line[1:].strip()
            seq_parts = []
        else:
            seq_parts.append(line)
    _consume(header, seq_parts)

    if not genome_counts:
        raise ValueError("no usable CDS found — expected an in-frame CDS "
                         "FASTA (e.g. NCBI cds_from_genomic.fna)")
    base = heg_counts if mode == "heg" else genome_counts
    if not base:
        raise ValueError("no highly-expressed (ribosomal-protein) CDS found "
                         "in this genome — try whole-genome mode")

    raw: dict = {c: (_CODON_GENETIC_CODE[c], int(n)) for c, n in base.items()}
    backfilled: list = []
    if mode == "heg":
        have_aa = {_CODON_GENETIC_CODE[c] for c in raw}
        for c, n in genome_counts.items():
            aa = _CODON_GENETIC_CODE[c]
            if aa not in have_aa:
                raw[c] = (aa, int(n))
                if aa not in backfilled:
                    backfilled.append(aa)

    n_codons = sum(n for _aa, n in raw.values())
    gc3 = ((sum(n for c, (_aa, n) in raw.items() if c[2] in "GC")
            / n_codons * 100) if n_codons else 0.0)
    stats = {
        "mode":        mode,
        "n_cds_total": n_cds_total,
        "n_cds_heg":   n_cds_heg,
        "n_codons":    n_codons,
        "aa_coverage": len({_aa for _aa, _n in raw.values()} - {"*"}),
        "backfilled":  sorted(backfilled),
        "gc3":         round(gc3, 1),
    }
    return raw, stats


def _codon_parse_kazusa_html(html: str) -> "dict | None":
    """Parse Kazusa showcodon.cgi GCG-format HTML. Returns {codon: (aa, count)}
    or None on failure."""
    pre = re.search(r"<[Pp][Rr][Ee]>(.*?)</[Pp][Rr][Ee]>", html, re.DOTALL)
    text = pre.group(1) if pre else html
    pat = re.compile(r"\b([ACGTU]{3})\b\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
    raw: dict = {}
    for m in pat.finditer(text):
        rna = m.group(1).upper()
        dna = rna.replace("U", "T")
        if dna not in _CODON_GENETIC_CODE or dna in raw:
            continue
        try:
            count = round(float(m.group(2)))
        except ValueError:
            continue
        raw[dna] = (_CODON_GENETIC_CODE[dna], count)
    # 2026-05-27 (audit-5 domesticator M1): require all 64 codons.
    # Pre-fix the threshold was 60, silently accepting up to 4
    # missing codons; if a rare codon (e.g. ATA-Ile) was missing
    # for an organism, `_codon_optimize` raised `ValueError("No
    # codons for amino acid …")` only when the protein happened to
    # contain that AA — surface unpredictably at design time. Now
    # malformed tables are rejected upfront.
    if len([c for c in raw if raw[c][0] != "?"]) < 64:
        return None
    return raw


_KAZUSA_MAX_RESPONSE_BYTES = 1 * 1024 * 1024


# NCBI Datasets CDS-FASTA zip download (codon-table genome builder). A
# bacterial genome's CDS set zips to ~1 MB; 256 MB caps a hostile / oversized
# response. The builder targets prokaryotic hosts — an accidentally-eukaryotic
# CDS set trips this cap and surfaces a "use a smaller / prokaryotic genome"
# error rather than ballooning memory.
_NCBI_CDS_ZIP_MAX_BYTES    = 256 * 1024 * 1024
# Local CDS FASTA cap (the offline `_file_build_codon_table` source). Matches the
# NCBI zip cap — it's the same CDS data, just read from disk instead of fetched.
_CDS_FILE_MAX_BYTES        = 256 * 1024 * 1024


# A real Datasets CDS zip carries a handful of members; cap the walk so a
# hostile / MITM'd response can't make us iterate millions of entries.
_NCBI_CDS_ZIP_MAX_MEMBERS  = 10_000


_NCBI_DATASETS_TIMEOUT_S   = 60.0


def _codon_fetch_kazusa(taxid: str, timeout: float = 15.0) -> tuple:
    """Fetch codon usage from Kazusa for an NCBI taxid. Returns
    (raw_dict_or_None, status_message). Pure network call — callers should
    invoke from a worker thread."""
    _state._demo_block_network_hook("Kazusa codon fetch")
    import urllib.request
    import urllib.error as _urllib_error  # sweep #25 — narrow excepts
    taxid = str(taxid).strip()
    if not taxid.isdigit():
        return None, f"Invalid taxid '{taxid}' (must be numeric)"
    url = (f"https://www.kazusa.or.jp/codon/cgi-bin/showcodon.cgi"
           f"?species={taxid}&aa=1&style=GCG")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(_KAZUSA_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _KAZUSA_MAX_RESPONSE_BYTES:
            _log.warning("Kazusa response exceeded %d bytes; aborting",
                          _KAZUSA_MAX_RESPONSE_BYTES)
            return None, "Kazusa returned an oversized response"
        html = raw.decode("utf-8", errors="replace")
    except (OSError, _urllib_error.URLError) as exc:
        # Sweep #25 (2026-05-23): narrowed from bare `Exception`.
        _log.exception("Kazusa fetch failed for taxid %s", taxid)
        return None, f"Network error: {exc}"
    low = html.lower()
    if "not found" in low or "no data" in low:
        return None, f"Taxid {taxid} not found in Kazusa database"
    raw = _codon_parse_kazusa_html(html)
    if raw is None:
        return None, f"Could not parse Kazusa table for taxid {taxid}"
    return raw, f"Fetched from Kazusa: {len(raw)} codons (taxid {taxid})"


# ── Genome → codon table (NCBI Datasets HEG builder) ──────────────────────────
# Build a codon-usage table from a genome's CDS instead of Kazusa: resolve a
# taxid → RefSeq reference accession, download the CDS_FASTA zip from the NCBI
# Datasets v2 API, extract the in-frame `cds_from_genomic.fna`, hand it to
# `_build_heg_table_from_cds`. Network reads are size-capped + retried once
# (250 ms) through the shared hardened opener ([PIT-20] / [RECIPE] "New HTTP
# fetch"); the zip is opened with the existing zip-safety guards. Worker-thread
# helpers — never call from the UI thread.
_NCBI_DATASETS_BASE = "https://api.ncbi.nlm.nih.gov/datasets/v2"


def _genome_datasets_request(url: str, max_bytes: int, timeout: float) -> bytes:
    """GET `url` through the shared hardened opener, one 250 ms-backoff retry on
    transient errors, body bounded at `max_bytes` (ValueError if exceeded —
    [PIT-20]). HTTP/URL errors propagate for the caller to message."""
    _state._demo_block_network_hook("Genome download")
    import socket
    import time as _time
    import urllib.error
    import urllib.request
    opener = _build_hardened_url_opener()
    req = urllib.request.Request(
        url, headers={"User-Agent": f"SpliceCraft/{_state._sc_version}"})
    last_exc: "BaseException | None" = None
    for attempt in range(2):                       # 1 try + 1 retry
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError(
                    f"response exceeded {max_bytes:,}-byte cap "
                    f"(genome too large — try a prokaryote / smaller assembly)")
            return body
        except urllib.error.HTTPError:
            raise                                  # 4xx/5xx is permanent
        except (urllib.error.URLError, socket.timeout) as exc:
            last_exc = exc
            if attempt == 0:
                _time.sleep(_HMM_DB_RETRY_BACKOFF_S)
                continue
            raise
    assert last_exc is not None                    # unreachable
    raise last_exc


def _genome_resolve_reference_accession(
        taxid: str, timeout: float = _NCBI_DATASETS_TIMEOUT_S) -> tuple:
    """Resolve an NCBI taxid → its RefSeq reference-genome assembly accession.
    Returns (accession, organism_name) on success or (None, error_message)."""
    import json as _json
    taxid = str(taxid).strip()
    if not taxid.isdigit():
        return None, f"Invalid taxid '{taxid}' (must be numeric)"
    url = (f"{_NCBI_DATASETS_BASE}/genome/taxon/{taxid}/dataset_report"
           f"?filters.reference_only=true&filters.assembly_source=refseq"
           f"&page_size=1")
    try:
        body = _genome_datasets_request(url, _NCBI_MAX_RESPONSE_BYTES, timeout)
        report = _json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:                      # size cap / malformed JSON
        return None, f"Could not read assembly report for taxid {taxid}: {exc}"
    except Exception as exc:                        # network / HTTP
        _log.exception("Datasets taxon resolve failed for %s", taxid)
        return None, f"Could not resolve taxid {taxid}: {exc}"
    reports = report.get("reports") if isinstance(report, dict) else None
    if not reports:
        return None, (f"No RefSeq reference genome for taxid {taxid} — supply a "
                      f"specific assembly accession (GCF_…) instead")
    rec = reports[0] or {}
    acc = str(rec.get("accession", "") or "")
    org = rec.get("organism") if isinstance(rec.get("organism"), dict) else {}
    name = str((org or {}).get("organism_name", "") or "")
    if not acc:
        return None, f"Reference assembly for taxid {taxid} carried no accession"
    return acc, name


def _genome_extract_cds_fasta(zip_bytes: bytes) -> str:
    """Extract the `cds_from_genomic.fna` member from an in-memory NCBI Datasets
    CDS zip, reusing the zip-safety guards (`_is_safe_zip_member_name`, member-
    size cap, bounded read — [SUB-plasmidsaurus]). ValueError when the archive
    is not a zip, carries no CDS FASTA (unannotated assembly), or trips a cap."""
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            # Cap the member walk and require an EXACT basename so a decoy
            # like `evilcds_from_genomic.fna` can't be selected by `endswith`.
            import itertools
            member = next(
                (info for info in itertools.islice(
                     zf.infolist(), _NCBI_CDS_ZIP_MAX_MEMBERS)
                 if Path(info.filename).name == "cds_from_genomic.fna"
                 and _is_safe_zip_member_name(info.filename)), None)
            if member is None:
                raise ValueError(
                    "assembly has no annotated CDS (no cds_from_genomic.fna) — "
                    "try a RefSeq (GCF_) accession that includes annotation")
            if member.file_size > _NCBI_CDS_ZIP_MAX_BYTES:
                raise ValueError(
                    f"CDS member too large ({member.file_size:,} bytes; cap "
                    f"{_NCBI_CDS_ZIP_MAX_BYTES:,})")
            with zf.open(member, "r") as fh:
                raw = fh.read(_NCBI_CDS_ZIP_MAX_BYTES + 1)
    except zipfile.BadZipFile:
        raise ValueError("downloaded file is not a valid zip — the assembly "
                         "accession may not exist or has no CDS download")
    if len(raw) > _NCBI_CDS_ZIP_MAX_BYTES:
        raise ValueError("CDS member exceeded cap during decompression "
                         "— possible zip-bomb")
    return raw.decode("utf-8", errors="replace")


def _genome_build_codon_table(
        query: str, mode: str = "heg",
        timeout: float = _NCBI_DATASETS_TIMEOUT_S) -> tuple:
    """Build a codon table from an NCBI genome. `query` is an assembly accession
    (`GCF_…`/`GCA_…`) or an NCBI taxid (digits → resolve to the RefSeq reference
    assembly). `mode` is 'heg' or 'genome'. Returns (raw_or_None, message,
    meta_or_None); meta carries accession / taxid / organism / stats for the
    caller's display name + saved entry. Worker-thread only (network + parse)."""
    query = str(query or "").strip()
    if not query:
        return None, "Enter a genome assembly accession (GCF_…) or NCBI taxid", None
    if mode not in ("heg", "genome"):
        return None, f"Invalid mode '{mode}' (expected 'heg' or 'genome')", None
    taxid = ""
    organism = ""
    accession = query
    if query.isdigit():
        taxid = query
        resolved, info = _genome_resolve_reference_accession(query, timeout)
        if resolved is None:
            return None, info, None                # info = error message
        accession, organism = resolved, info       # info = organism name
    # Sanitize before the accession reaches the request URL — blocks path /
    # query injection (e.g. "GCF_1/../x?y=z") from the un-sanitized modal
    # input; the taxid path is already digit-only. `_sanitize_accession`
    # permits the GCF_…/GCA_… charset and rejects URL/shell metacharacters.
    accession = _sanitize_accession(accession) or ""
    if not accession.upper().startswith(("GCF_", "GCA_")):
        return None, (f"'{query}' is not a valid assembly accession "
                      f"(expected GCF_… / GCA_…) or a numeric taxid"), None
    url = (f"{_NCBI_DATASETS_BASE}/genome/accession/{accession}/download"
           f"?include_annotation_type=CDS_FASTA")
    try:
        zip_bytes = _genome_datasets_request(url, _NCBI_CDS_ZIP_MAX_BYTES, timeout)
        fasta = _genome_extract_cds_fasta(zip_bytes)
        raw, stats = _build_heg_table_from_cds(fasta, mode)
    except ValueError as exc:                       # cap / no-CDS / build error
        return None, str(exc), None
    except Exception as exc:                         # network / HTTP
        _log.exception("Genome codon-table build failed for %s", accession)
        return None, f"Download failed for {accession}: {exc}", None
    label = "ribosomal-protein (HEG)" if mode == "heg" else "whole-genome"
    n_cds = stats["n_cds_heg"] if mode == "heg" else stats["n_cds_total"]
    bf = (f", backfilled {', '.join(stats['backfilled'])}"
          if stats.get("backfilled") else "")
    msg = (f"Built {label} table from {accession}"
           + (f" ({organism})" if organism else "")
           + f": {stats['n_codons']:,} codons from {n_cds} CDS, "
           + f"{stats['aa_coverage']}/20 AAs, GC3 {stats['gc3']}%{bf}")
    meta = {"accession": accession, "taxid": taxid,
            "organism": organism, "stats": stats}
    return raw, msg, meta


def _file_build_codon_table(path: "str | Path", mode: str = "heg") -> tuple:
    """Build a codon table from a LOCAL CDS FASTA file — the in-frame
    `cds_from_genomic.fna` shape (nucleotide CDS records), optionally gzipped.
    No network; the offline analogue of `_genome_build_codon_table`. `mode` is
    'heg' (ribosomal-protein bias, for heterologous expression) or 'genome'
    (whole-file average). Returns (raw_or_None, message, meta_or_None); meta
    carries organism (the filename) + stats for the caller's display name + saved
    entry.

    A PROTEOME (amino-acid) FASTA can't be used — codon usage needs the nucleotide
    CDS — so a non-ACGT file surfaces the shared builder's 'no usable CDS' error."""
    if mode not in ("heg", "genome"):
        return None, f"Invalid mode '{mode}' (expected 'heg' or 'genome')", None
    import gzip
    p = _sanitize_path(str(path) if path is not None else None)
    if p is None:
        return None, ("Enter a path to a CDS FASTA file "
                      "(.fna / .fasta, optionally .gz)"), None
    try:
        if not p.is_file():
            return None, f"No such file: {p}", None
        size = p.stat().st_size
        if size > _CDS_FILE_MAX_BYTES:
            return None, (f"File too large ({size:,} bytes > "
                          f"{_CDS_FILE_MAX_BYTES:,})"), None
        data = p.read_bytes()
    except OSError as exc:
        return None, f"Could not read {p.name}: {exc}", None
    if data[:2] == b"\x1f\x8b":                       # gzip magic — NCBI ships CDS .gz
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            return None, f"Could not gunzip {p.name}: {exc}", None
        if len(data) > _CDS_FILE_MAX_BYTES:
            return None, (f"Decompressed file too large "
                          f"(> {_CDS_FILE_MAX_BYTES:,} bytes)"), None
    try:
        raw, stats = _build_heg_table_from_cds(
            data.decode("utf-8", "replace"), mode)
    except ValueError as exc:                         # no-CDS / no-rprotein / build
        return None, str(exc), None
    except Exception as exc:                          # pragma: no cover — defensive
        _log.exception("Local CDS codon-table build failed for %s", p.name)
        return None, f"Build failed: {exc}", None
    label = "ribosomal-protein (HEG)" if mode == "heg" else "whole-genome"
    n_cds = stats["n_cds_heg"] if mode == "heg" else stats["n_cds_total"]
    bf = (f", backfilled {', '.join(stats['backfilled'])}"
          if stats.get("backfilled") else "")
    msg = (f"Built {label} table from {p.name}: "
           f"{stats['n_codons']:,} codons from {n_cds} CDS, "
           f"{stats['aa_coverage']}/20 AAs, GC3 {stats['gc3']}%{bf}")
    meta = {"accession": "", "taxid": "", "organism": p.stem,
            "source_file": p.name, "stats": stats}
    return raw, msg, meta
