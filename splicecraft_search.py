"""splicecraft_search — online NCBI search operations (Phase D, layer L1).

The network search operations that resolve user queries against NCBI, lifted out
of the hub. Seeded with the taxonomy name->taxid search (_ncbi_taxid_search +
_ncbi_prep_term + _ncbi_taxid_search_terms). Builds on the shared SSRF-hardened
primitives in splicecraft_net + the XML-security parse in splicecraft_util, and
gates egress through the fail-closed _state._demo_block_network_hook. (Online
BLAST + the HMM-DB fetch can join here later, once the shared _online_* infra is
extracted.) Re-exported by the hub so sc.<name> + every call site resolves
unchanged.
"""
from __future__ import annotations

import splicecraft_state as _state
from splicecraft_logging import _log
from splicecraft_util import _safe_xml_parse
from splicecraft_net import _NCBI_MAX_RESPONSE_BYTES


def _ncbi_prep_term(query: str) -> str:
    """Turn a user query into an NCBI Entrez term.

    * Single token (typed-as-genus): combine an exact-taxon subtree search
      restricted to species rank with a wildcard prefix search via OR, so
      typing 'Escherichia' returns every Escherichia species (subtree hit)
      AND typing a partial like 'Escher' still matches via the wildcard.
    * Multi-word query: append '*' to the trailing token so 'Homo sapien'
      matches 'Homo sapiens' etc.
    * User-supplied wildcards or field tags pass through untouched.
    """
    q = (query or "").strip()
    if not q or "*" in q or "[" in q:
        return q
    tokens = q.split()
    if len(tokens) == 1:
        t = tokens[0]
        return f"({t}[Subtree] AND species[Rank]) OR {t}*"
    tokens[-1] = tokens[-1] + "*"
    return " ".join(tokens)


def _ncbi_taxid_search_terms(query: str) -> list[str]:
    """Cascading NCBI-taxonomy search terms, strictest first — mirrors the
    sister project ScriptoScope's `genbank_search` strategy (see
    `[SISTER]` in docs/invariants.md). The caller tries each term in
    order and stops at the first that returns hits, so an imprecise or
    partial species name still surfaces *related* taxa instead of a
    dead-end "no results".

    * Strategy 1 is `_ncbi_prep_term` — the precise-ish query (genus
      subtree + prefix wildcard for a single token; trailing wildcard
      for a multi-word name). The overwhelming majority of real queries
      resolve here, so this is identical to the pre-cascade behaviour.
    * For a MULTI-word query that comes back empty, two broader rounds
      kick in: every token prefix-wildcarded and AND-joined (tolerates a
      partial genus AS WELL AS a partial species — "Sacchar cerev"),
      then OR-joined (ANY token matches, so a stray or mistyped word
      still finds relatives — e.g. a genus typo "Homon sapiens" is
      rescued by the species epithet).

    A single-token query gets only strategy 1: its broader forms are
    already subsumed by the `OR {t}*` clause `_ncbi_prep_term` emits, so
    re-querying would just repeat the same search. A query the user has
    already steered with a `*` wildcard or a `[Field]` tag passes through
    verbatim — never second-guess an explicit Entrez query.
    """
    q = (query or "").strip()
    if not q:
        return []
    first = _ncbi_prep_term(q)
    if "*" in q or "[" in q:
        return [first]
    tokens = q.split()
    if len(tokens) < 2:
        return [first]
    terms = [first]
    for broadened in (
        " AND ".join(f"{tok}*" for tok in tokens),
        " OR ".join(f"{tok}*" for tok in tokens),
    ):
        if broadened not in terms:
            terms.append(broadened)
    return terms


def _ncbi_taxid_search(query: str, retmax: int = 200,
                      timeout: float = 15.0) -> tuple:
    """Search NCBI taxonomy for candidates matching `query`. Returns
    (hits, total_count, status_message) where each hit is
    {'taxid': str, 'name': str}. Names come from a batched esummary call
    (one round-trip for up to `retmax` ids).

    Imprecise / partial queries are handled by a cascading search
    (`_ncbi_taxid_search_terms`, mirroring ScriptoScope's `genbank_search`):
    a strict query is tried first, and if it comes back EMPTY the search
    is automatically broadened — every token prefix-wildcarded, then
    OR-joined — so a related-but-not-exact species name still surfaces
    candidates. A network / parse / oversize failure aborts the cascade
    immediately (an empty result, by contrast, just falls through to the
    next, broader strategy). Pure network — run from a worker."""
    _state._demo_block_network_hook("NCBI taxonomy search")
    import urllib.parse
    import urllib.request
    import urllib.error as _urllib_error   # sweep #25 — narrow excepts
    import xml.etree.ElementTree as ET
    q = (query or "").strip()
    if not q:
        return [], 0, "Empty query"
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ids: list[str] = []
    total = 0
    broadened = False
    for idx, term in enumerate(_ncbi_taxid_search_terms(q)):
        params = urllib.parse.urlencode({
            "db": "taxonomy", "term": term,
            "retmax": str(retmax), "retmode": "xml",
        })
        try:
            req = urllib.request.Request(
                f"{base}/esearch.fcgi?{params}",
                headers={"User-Agent": "SpliceCraft/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(_NCBI_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _NCBI_MAX_RESPONSE_BYTES:
                _log.warning("NCBI esearch response exceeded %d bytes; truncating",
                              _NCBI_MAX_RESPONSE_BYTES)
                return [], 0, "NCBI returned an oversized response"
            xml_data = raw.decode("utf-8", errors="replace")
        except (OSError, _urllib_error.URLError) as exc:
            # Sweep #25 (2026-05-23): narrowed from bare `Exception` —
            # network failures land as `URLError` (timeout, DNS, refused)
            # or `OSError` (socket-level). Other exceptions (KeyError,
            # AttributeError) here would be real bugs and should
            # propagate to surface in the log + dialog. A network error
            # aborts the cascade (matches ScriptoScope's `?` propagation)
            # rather than masking an outage behind broader retries.
            _log.exception("NCBI esearch failed for %r", q)
            return [], 0, f"Network error: {exc}"
        try:
            # allow_dtd=True: NCBI eutils responses open with an EXTERNAL
            # DTD reference — `<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD
            # esearch 20060628//EN" ".../esearch.dtd">` — which has no
            # internal subset, so it stays XXE / billion-laughs safe (expat
            # never fetches the external DTD since Py 3.7.1, and there are no
            # entities to expand). Without this flag the strict parse rejects
            # EVERY real NCBI response with "XML contains DTD/ENTITY —
            # refusing to parse", which surfaced as the taxon-search "XML
            # parser error". Mirrors the BLAST-XML caller. See [PIT-19].
            root = _safe_xml_parse(xml_data, allow_dtd=True)
        except ET.ParseError as exc:
            return [], 0, f"Could not parse NCBI response: {exc}"
        cur_ids = [e.text for e in root.findall(".//Id") if e.text]
        if cur_ids:
            ids = cur_ids
            count_elem = root.find(".//Count")
            try:
                total = (int(count_elem.text)
                         if count_elem is not None and count_elem.text
                         else len(ids))
            except ValueError:
                total = len(ids)
            broadened = idx > 0
            break
    if not ids:
        return [], 0, f"No NCBI taxonomy entry for '{q}'"
    # Batched esummary: one round-trip for all retrieved ids
    names_by_id: dict[str, str] = {}
    try:
        sparams = urllib.parse.urlencode({
            "db": "taxonomy", "id": ",".join(ids), "retmode": "xml",
        })
        req = urllib.request.Request(f"{base}/esummary.fcgi?{sparams}",
                                     headers={"User-Agent": "SpliceCraft/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            sraw = r.read(_NCBI_MAX_RESPONSE_BYTES + 1)
        if len(sraw) > _NCBI_MAX_RESPONSE_BYTES:
            _log.warning("NCBI esummary response exceeded %d bytes; ignoring",
                          _NCBI_MAX_RESPONSE_BYTES)
            sxml = ""
        else:
            sxml = sraw.decode("utf-8", errors="replace")
        # allow_dtd=True — esummary carries the same external-DTD DOCTYPE
        # as esearch (`<!DOCTYPE eSummaryResult PUBLIC ... esummary-v1.dtd>`);
        # see the esearch parse above for why this is safe.
        sroot = _safe_xml_parse(sxml, allow_dtd=True) if sxml else None
        for doc in (sroot.findall(".//DocSum") if sroot is not None else []):
            did_el = doc.find("Id")
            if did_el is None or not did_el.text:
                continue
            did = did_el.text
            for item in doc.findall("Item"):
                if item.get("Name") == "ScientificName" and item.text:
                    names_by_id[did] = item.text
                    break
    except (OSError, _urllib_error.URLError, ET.ParseError):
        # Sweep #25 (2026-05-23): narrowed from bare `Exception`.
        _log.exception("NCBI esummary failed for ids %s", ids[:3])
    hits = [{"taxid": tid,
             "name":  names_by_id.get(tid, f"(taxid {tid})")}
            for tid in ids]
    msg = f"{total} hit(s) for '{q}'"
    if total > len(hits):
        msg = f"Showing {len(hits)} of {total} hits for '{q}' (refine to narrow)"
    if broadened:
        # No exact match — surfaced related taxa via the broadened
        # cascade. Tell the user so a loose hit isn't mistaken for a
        # precise one.
        msg += " · broadened to related names"
    return hits, total, msg
