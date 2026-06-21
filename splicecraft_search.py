"""splicecraft_search — online NCBI search operations (Phase D, layer L1).

The online network-search operations that resolve user queries against external
services, lifted out of the hub: the NCBI taxonomy name->taxid search
(_ncbi_taxid_search + _ncbi_prep_term + _ncbi_taxid_search_terms) AND the **online
search engines** — NCBI BLAST (URL API: _ncbi_blast_online / _ncbi_blast_parse_xml
/ _ncbi_blast_delete_rid) + EBI HMMER web (_hmmer_web_hmmscan / _hmmer_web_parse_json)
with their shared submit->poll->fetch->parse infra (_online_http, _online_clean_query,
_program_query_kind, _online_max_query_len, _ncbi_blast_db_for, _online_safe_*, the
_OnlineSearchCancelled exception + per-engine threading.Event cancel flags, and the
_NCBI_BLAST_*/_HMMER_*/_ONLINE_* consts). Builds on the shared SSRF-hardened primitives
in splicecraft_net (_build_hardened_url_opener / _NCBI_TIMEOUT_S) + the XML-security
parse in splicecraft_util, gates egress through the fail-closed
_state._demo_block_network_hook, stamps the User-Agent from _state._sc_version (= hub
__version__), and never logs sequence content ([INV-38]). The BlastModal / HmmerModal
UI screens (which run these in @work(thread=True) workers) STAY hub-side. The HMM-DB
fetch can still join here later. Re-exported by the hub so sc.<name> + every call site
resolves unchanged.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any as _Any, Callable as _Callable

import splicecraft_state as _state
from splicecraft_logging import _log
from splicecraft_util import _CONTROL_CHARS_RE, _safe_xml_parse, _strip_fasta_headers
from splicecraft_net import (
    _NCBI_MAX_RESPONSE_BYTES, _NCBI_TIMEOUT_S, _build_hardened_url_opener,
)


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


# ═══ Online search engines (NCBI BLAST URL API + EBI HMMER web) — from hub ═══
# submit -> poll -> fetch -> parse; run in BlastModal/HmmerModal workers (UI stays
# hub-side). Cancel via the per-engine threading.Event. __version__ -> _state._sc_version.

# ── Online search engines (NCBI BLAST URL API + EBI HMMER web) ───────────────
#
# Borrowed + generalised from the sister project ScriptoScope
# (`/home/seb/proteoscope/scriptoscope.py::ncbi_blastp`) per the
# "[RECIPE] borrow before respinning" playbook. Both engines follow the
# same submit → poll → fetch → parse shape and run inside BlastModal's
# `@work(thread=True)` workers so the UI keeps redrawing during the
# (potentially multi-minute) round trip. A `threading.Event` per engine
# lets the user cancel mid-flight; the NCBI side also releases the
# server-side RID so an abandoned job doesn't keep running unattended.
#
# Hardening (per the new-feature sweep convention):
#   * resp.read(MAX + 1) + bail-if-exceeded — never raw .read() ([PIT-20]).
#   * NCBI XML routed through _safe_xml_parse ([PIT-19], defangs DOCTYPE /
#     billion-laughs / unbounded nesting).
#   * HMMER JSON parsed defensively (.get() chains, several shapes) since
#     the P7Hit object is `additionalProperties: true` in the EBI schema.
#   * Friendly RuntimeErrors for timeout / unreachable host — never a raw
#     traceback to the user.
#   * Never logs sequence content ([INV-38]) — lengths / program only.

_NCBI_BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"
_HMMER_WEB_SUBMIT_URL = "https://www.ebi.ac.uk/Tools/hmmer/api/v1/search/hmmscan"
_HMMER_WEB_RESULT_URL = "https://www.ebi.ac.uk/Tools/hmmer/api/v1/result/"

# Response caps ([PIT-20]). BLAST XML for a 50-hit list runs into the
# megabytes; Pfam hmmscan JSON is smaller. Both refuse a pathological
# multi-hundred-MB body (compromised / misconfigured server / MITM).
_NCBI_BLAST_MAX_RESPONSE_BYTES = 48 * 1024 * 1024
_HMMER_WEB_MAX_RESPONSE_BYTES = 24 * 1024 * 1024

# Poll cadence + overall ceiling shared by both engines. 10 s honours
# NCBI's URL-API politeness floor (don't poll a single RID more than
# ~once / 10 s); ScriptoScope used 5 s but we err polite. 300 s overall
# matches the crib.
_ONLINE_POLL_INTERVAL_S = 10
_ONLINE_MAX_WAIT_S = 300

# Programs offered by the Online tab's dropdown. The trailing "hmmscan"
# routes to the EBI HMMER engine; every other value hits NCBI BLAST.
_ONLINE_BLAST_PROGRAMS: "tuple[tuple[str, str], ...]" = (
    ("blastn   (DNA/RNA → nucleotide nt)",        "blastn"),
    ("blastp   (protein → protein nr)",           "blastp"),
    ("blastx   (translated DNA → protein nr)",    "blastx"),
    ("tblastn  (protein → translated nt)",        "tblastn"),
    ("tblastx  (translated DNA → translated nt)", "tblastx"),
    ("hmmscan  (protein → Pfam, online)",         "hmmscan"),
)

# Programs that take a nucleotide query (so a pasted RNA can be U→T
# normalised before submit) vs a protein query.
_ONLINE_NUCLEOTIDE_QUERY = frozenset({"blastn", "blastx", "tblastx"})
_ONLINE_PROTEIN_QUERY = frozenset({"blastp", "tblastn", "hmmscan"})


def _program_query_kind(program: str) -> str:
    """"nt" if the program takes a nucleotide query, else "protein".
    Spans both tabs (local blastn/blastp/hmmscan + online's five BLAST
    programs). Used to decide whether switching program invalidates the
    current query (nt↔protein) and should clear it."""
    return "nt" if program in _ONLINE_NUCLEOTIDE_QUERY else "protein"

# NCBI's hard query-length limits (chars). Submitting past these makes
# the server reject the job ("query too long"), so we refuse the search
# client-side with a clear message rather than truncate (a silently
# trimmed BLAST query returns misleading hits). Per NCBI's published
# limits: 1,000,000 for nucleotide queries; 100,000 for protein. The
# EBI HMMER hmmscan service takes a single protein — 100,000 aa is far
# beyond any real Pfam query but still caps a pathological paste.
_NCBI_BLAST_MAX_QUERY = {
    "blastn": 1_000_000, "blastx": 1_000_000, "tblastx": 1_000_000,
    "blastp": 100_000, "tblastn": 100_000,
    "hmmscan": 100_000,
}


def _online_max_query_len(program: str) -> int:
    return _NCBI_BLAST_MAX_QUERY.get(program, 100_000)

# EBI HMMER job-status buckets (case-folded). Anything non-empty that
# isn't a terminal-success / terminal-error state counts as "still
# running" and keeps the poll loop going.
_HMMER_DONE_STATUS = frozenset(
    {"SUCCESS", "DONE", "COMPLETE", "COMPLETED", "FINISHED", "OK"})
_HMMER_ERROR_STATUS = frozenset(
    {"ERROR", "ERR", "FAILURE", "FAILED", "FAIL"})


class _OnlineSearchCancelled(Exception):
    """Raised inside an online-search worker when the user cancels."""


# One cancel flag per engine. Set by the UI thread (Cancel button),
# polled by the worker via `Event.wait(timeout=…)` so cancellation is
# near-instant even mid-sleep between polls.
_ncbi_blast_cancel = threading.Event()
_hmmer_web_cancel = threading.Event()


def _ncbi_blast_db_for(program: str) -> str:
    """Default NCBI database for a BLAST program: a protein DB (`nr`) when
    the *subject* is protein (blastp / blastx), a nucleotide DB (`nt`)
    otherwise (blastn / tblastn / tblastx)."""
    return "nr" if program in ("blastp", "blastx") else "nt"


def _online_safe_float(val: "_Any") -> "float | None":
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _online_safe_int(val: "_Any") -> "int | None":
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def _online_clean_query(raw: str, program: str) -> str:
    """Strip FASTA headers + whitespace, upper-case, and (for nucleotide-
    query programs) normalise RNA U→T. Does NOT truncate — the caller
    enforces `_online_max_query_len(program)` and refuses oversize input
    so a trimmed query can't return misleading hits."""
    seq = _strip_fasta_headers(raw or "")
    seq = re.sub(r"\s+", "", seq).upper()
    if program in _ONLINE_NUCLEOTIDE_QUERY:
        seq = seq.replace("U", "T")
    return seq


def _online_http(url: str, *, data: "bytes | None" = None,
                 headers: "dict | None" = None,
                 timeout: int = _NCBI_TIMEOUT_S,
                 max_bytes: int = _NCBI_BLAST_MAX_RESPONSE_BYTES) -> str:
    """POST (``data`` given) / GET (``data`` None) ``url`` and return the
    decoded body. Translates network failures into friendly RuntimeErrors
    and enforces the response-size cap ([PIT-20]). HTTP error statuses
    surface as ``RuntimeError`` carrying the code so the poll loop can tell
    a transient 404-not-ready from a fatal 400."""
    import socket
    import urllib.error
    import urllib.request
    hdrs = {"User-Agent": f"SpliceCraft/{_state._sc_version}"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    # Sweep #30 (2026-05-28): route through the hardened opener so the
    # online BLAST/HMMER path gets the same network discipline as the
    # HMM-DB downloader + PyPI check — explicit verifying SSL context,
    # bounded redirects, and refusal of an https->http downgrade on
    # redirect. Pre-fix this used the default global opener, which would
    # follow a MITM/compromised-CDN redirect to plaintext and read the
    # RID/XML over a tamperable channel (feeding the result table). [INV-85]
    opener = _build_hardened_url_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout):
            raise RuntimeError(
                "Connection timed out — check your internet connection."
            ) from exc
        raise RuntimeError(f"Cannot reach server: {reason}") from exc
    except socket.timeout as exc:
        raise RuntimeError(
            "Connection timed out — check your internet connection."
        ) from exc
    if len(raw) > max_bytes:
        raise RuntimeError(
            f"Response exceeded the {max_bytes // (1024 * 1024)} MB cap — "
            f"refusing to load it. Narrow the query or hit list."
        )
    return raw.decode("utf-8", "replace")


def _ncbi_blast_delete_rid(rid: str) -> None:
    """Best-effort release of a server-side BLAST job so an abandoned /
    cancelled search doesn't keep running. Failures are swallowed — the
    job times out on NCBI's side anyway."""
    from urllib.parse import urlencode
    try:
        _online_http(
            _NCBI_BLAST_URL,
            data=urlencode({"CMD": "Delete", "RID": rid}).encode(),
            timeout=10,
        )
    except Exception:
        pass


def _ncbi_blast_online(query: str, program: str, database: str,
                       max_hits: int,
                       progress_cb: "_Callable[[str], None] | None" = None,
                       cancel_event: "threading.Event | None" = None
                       ) -> "list[dict]":
    """Run a remote NCBI BLAST via the public URL API (CMD=Put → RID →
    poll SearchInfo → CMD=Get XML → parse). Returns hit dicts shaped for
    ``BlastModal._online_render_blast``. Raises ``_OnlineSearchCancelled``
    if ``cancel_event`` fires, ``RuntimeError`` on network / server error.
    Borrowed from ScriptoScope's ``ncbi_blastp`` ([RECIPE])."""
    from urllib.parse import urlencode
    cancel = cancel_event or threading.Event()
    if progress_cb:
        progress_cb(f"Submitting {program} to NCBI…")
    put = urlencode({
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": database,
        "QUERY": query,
        "HITLIST_SIZE": str(max_hits),
        "FORMAT_TYPE": "XML",
    }).encode()
    put_text = _online_http(
        _NCBI_BLAST_URL, data=put,
        max_bytes=_NCBI_BLAST_MAX_RESPONSE_BYTES)
    rid = ""
    for line in put_text.splitlines():
        s = line.strip()
        if s.startswith("RID = "):
            rid = s.split("=", 1)[1].strip()
            break
    if not rid:
        raise RuntimeError(
            "NCBI did not return a job id (RID) — the query may be invalid "
            "for this program, or NCBI is rejecting traffic right now.")
    try:
        elapsed = 0
        checks = 0
        consecutive_fail = 0
        while elapsed < _ONLINE_MAX_WAIT_S:
            if cancel.wait(timeout=_ONLINE_POLL_INTERVAL_S):
                raise _OnlineSearchCancelled()
            elapsed += _ONLINE_POLL_INTERVAL_S
            checks += 1
            if progress_cb:
                progress_cb(
                    f"Waiting for NCBI {program} results "
                    f"(checked {checks}×)…")
            try:
                status_text = _online_http(
                    _NCBI_BLAST_URL,
                    data=urlencode({
                        "CMD": "Get",
                        "FORMAT_OBJECT": "SearchInfo",
                        "RID": rid,
                    }).encode(),
                    max_bytes=_NCBI_BLAST_MAX_RESPONSE_BYTES)
                consecutive_fail = 0
            except (RuntimeError, OSError) as exc:
                # Transient NCBI 5xx / network blip while the job is still
                # queued server-side: tolerate a few in a row before giving
                # up (mirrors the EBI HMMER poll loop). Pre-fix a single 502
                # aborted the search AND leaked the RID — the delete only ran
                # on the cancel path.
                consecutive_fail += 1
                if consecutive_fail >= 5:
                    raise RuntimeError(
                        f"NCBI BLAST polling failed {consecutive_fail}× in a "
                        f"row: {exc}") from exc
                _log.debug("NCBI poll transient failure %d/5: %s",
                            consecutive_fail, exc)
                continue
            if "Status=WAITING" in status_text:
                continue
            if "Status=FAILED" in status_text:
                raise RuntimeError(
                    "NCBI BLAST job failed — the server errored on the "
                    "query (check the sequence matches the program).")
            if "Status=UNKNOWN" in status_text:
                raise RuntimeError(
                    "NCBI BLAST job expired or is unknown — please retry.")
            if "Status=READY" in status_text:
                break
        else:
            raise RuntimeError(
                f"NCBI BLAST timed out after {_ONLINE_MAX_WAIT_S}s — the "
                f"query may be too large; try a shorter region.")
    except BaseException:
        # Any abort after the RID was issued (cancel, timeout, FAILED /
        # UNKNOWN, repeated poll failures) must delete the server-side job —
        # pre-fix only cancel did, leaking RIDs on every other error path.
        # Success (READY) breaks WITHOUT raising, so the RID survives for the
        # result fetch below.
        _ncbi_blast_delete_rid(rid)
        raise
    if progress_cb:
        progress_cb("Downloading NCBI results…")
    xml_text = _online_http(
        _NCBI_BLAST_URL,
        data=urlencode({
            "CMD": "Get", "FORMAT_TYPE": "XML", "RID": rid,
        }).encode(),
        timeout=60, max_bytes=_NCBI_BLAST_MAX_RESPONSE_BYTES)
    return _ncbi_blast_parse_xml(xml_text, max_hits)


def _ncbi_blast_parse_xml(xml_text: str, max_hits: int) -> "list[dict]":
    """Parse NCBI BLAST XML into hit dicts. Routes through _safe_xml_parse
    ([PIT-19]); one row per hit (first/best HSP, mirroring the crib)."""
    import xml.etree.ElementTree as ET
    try:
        # NCBI BLAST XML opens with an external <!DOCTYPE … BlastOutput.dtd>
        # — allowed here (expat never fetches it); internal subsets remain
        # refused so this stays XXE / billion-laughs safe.
        root = _safe_xml_parse(xml_text, allow_dtd=True)
    except ET.ParseError as exc:
        raise RuntimeError(
            f"NCBI returned XML SpliceCraft couldn't parse: {exc}") from exc
    hits: "list[dict]" = []
    for hit in root.iter("Hit"):
        # Strip terminal control bytes (\x1b / OSC etc.) from server-supplied
        # text: the detail pane escapes Rich markup but NOT raw ESC, so a
        # hostile Hit_def could otherwise inject terminal escape sequences.
        hit_def = _CONTROL_CHARS_RE.sub(
            "", hit.findtext("Hit_def") or "").strip()
        acc = (_CONTROL_CHARS_RE.sub(
                   "", hit.findtext("Hit_accession") or "").strip()
               or _CONTROL_CHARS_RE.sub(
                   "", hit.findtext("Hit_id") or "").strip() or "?")
        hsp = hit.find(".//Hsp")
        if hsp is None:
            continue
        identity = _online_safe_int(hsp.findtext("Hsp_identity"))
        align_len = _online_safe_int(hsp.findtext("Hsp_align-len"))
        pct = (round(identity / align_len * 100, 1)
               if identity is not None and align_len else None)
        hits.append({
            "accession": acc,
            "description": hit_def,
            "identity_pct": pct,
            "aln_len": align_len,
            "evalue": _online_safe_float(hsp.findtext("Hsp_evalue")),
            "bit_score": _online_safe_float(hsp.findtext("Hsp_bit-score")),
            "q_start": _online_safe_int(hsp.findtext("Hsp_query-from")),
            "q_end": _online_safe_int(hsp.findtext("Hsp_query-to")),
            "s_start": _online_safe_int(hsp.findtext("Hsp_hit-from")),
            "s_end": _online_safe_int(hsp.findtext("Hsp_hit-to")),
        })
        if len(hits) >= max_hits:
            break
    return hits


def _hmmer_web_parse_json(obj: "_Any", max_hits: int) -> "list[dict]":
    """Pull Pfam hits out of an EBI HMMER result body. Defensive across
    `result.hits`, `results.hits`, and top-level `hits`.

    The human-readable family name + description live in the hit's
    ``metadata`` sub-object (``identifier`` / ``description``), NOT in the
    top-level ``name`` (an internal numeric id) or ``desc`` (always null in
    the live v1 API). We read metadata first and fall back to the
    top-level / legacy keys so canned fixtures without metadata still
    parse. ``clan`` / ``type`` / ``external_link`` enrich the detail pane;
    ``included`` flags whether the hit cleared Pfam's inclusion (gathering)
    threshold vs. merely the reporting threshold."""
    if not isinstance(obj, dict):
        return []
    hits = None
    for container_key in ("result", "results"):
        container = obj.get(container_key)
        if isinstance(container, dict) and isinstance(
                container.get("hits"), list):
            hits = container["hits"]
            break
    if hits is None and isinstance(obj.get("hits"), list):
        hits = obj["hits"]
    if not isinstance(hits, list):
        return []
    out: "list[dict]" = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        md = h.get("metadata")
        if not isinstance(md, dict):
            md = {}
        # Strip terminal control bytes from server-supplied strings (the
        # detail pane escapes Rich markup but NOT raw ESC / OSC sequences).
        acc = _CONTROL_CHARS_RE.sub("", str(
            h.get("acc") or md.get("accession")
            or h.get("accession") or "?"))
        # `metadata.identifier` is the Pfam family name (e.g. "Pkinase");
        # the top-level `name` is an internal numeric id — avoid it unless
        # nothing better exists.
        name = _CONTROL_CHARS_RE.sub("", str(
            md.get("identifier") or md.get("id")
            or h.get("name") or acc))
        desc = _CONTROL_CHARS_RE.sub("", str(
            md.get("description") or h.get("desc")
            or h.get("description") or ""))
        ndom = _online_safe_int(
            h.get("ndom") if h.get("ndom") is not None
            else h.get("nincluded") if h.get("nincluded") is not None
            else h.get("nreported"))
        if ndom is None:
            doms = h.get("domains")
            ndom = len(doms) if isinstance(doms, list) else 0
        out.append({
            "acc": acc,
            "name": name,
            "description": desc,
            "evalue": _online_safe_float(
                h.get("evalue") if h.get("evalue") is not None
                else h.get("eval")),
            "bit_score": _online_safe_float(
                h.get("score") if h.get("score") is not None
                else h.get("bitscore")),
            "n_dom": ndom,
            # Sweep #30 (2026-05-28): strip control bytes here too — these
            # three EBI-supplied fields also reach the markup=True detail
            # pane (clan/type/link) and were the gap the acc/name/desc
            # strips above left open. [INV-85]
            "clan": _CONTROL_CHARS_RE.sub("", str(md.get("clan") or "")),
            "type": _CONTROL_CHARS_RE.sub("", str(md.get("type") or "")),
            "link": _CONTROL_CHARS_RE.sub(
                "", str(md.get("external_link") or "")),
            "included": bool(h.get("is_included", True)),
        })
        if len(out) >= max_hits:
            break
    return out


def _hmmer_web_hmmscan(protein: str, max_hits: int,
                       progress_cb: "_Callable[[str], None] | None" = None,
                       cancel_event: "threading.Event | None" = None
                       ) -> "list[dict]":
    """Run a remote hmmscan vs Pfam via the EBI HMMER web API (POST search
    → poll result/{id} until SUCCESS → parse). Returns Pfam hit dicts.
    Raises ``_OnlineSearchCancelled`` on cancel, ``RuntimeError`` on
    network / server error."""
    cancel = cancel_event or threading.Event()
    if progress_cb:
        progress_cb("Submitting hmmscan to EBI HMMER…")
    body = json.dumps({"input": protein, "database": "pfam"}).encode()
    submit_text = _online_http(
        _HMMER_WEB_SUBMIT_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json"},
        max_bytes=_HMMER_WEB_MAX_RESPONSE_BYTES)
    try:
        submit = json.loads(submit_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "EBI HMMER returned an unparseable submit response.") from exc
    # Some deployments return hits inline on submit — short-circuit.
    inline = _hmmer_web_parse_json(submit, max_hits)
    if inline:
        return inline
    job_id = ""
    if isinstance(submit, dict):
        job_id = str(submit.get("id") or submit.get("uuid") or "").strip()
    if not job_id:
        raise RuntimeError("EBI HMMER did not return a job id.")
    result_url = _HMMER_WEB_RESULT_URL + job_id
    elapsed = 0
    checks = 0
    consecutive_fail = 0
    while elapsed < _ONLINE_MAX_WAIT_S:
        if cancel.wait(timeout=_ONLINE_POLL_INTERVAL_S):
            raise _OnlineSearchCancelled()
        elapsed += _ONLINE_POLL_INTERVAL_S
        checks += 1
        if progress_cb:
            progress_cb(
                f"Waiting for Pfam hmmscan results (checked {checks}×)…")
        try:
            rtext = _online_http(
                result_url,
                headers={"Accept": "application/json"},
                max_bytes=_HMMER_WEB_MAX_RESPONSE_BYTES)
            obj = json.loads(rtext)
            consecutive_fail = 0
        except (RuntimeError, json.JSONDecodeError):
            # 404-not-ready / transient flake — keep polling, but don't
            # spin forever against a persistent failure.
            consecutive_fail += 1
            if consecutive_fail >= 5:
                raise RuntimeError(
                    "EBI HMMER kept returning errors — try again later.")
            continue
        status = str(
            (obj.get("status") if isinstance(obj, dict) else "") or ""
        ).upper()
        if status in _HMMER_ERROR_STATUS:
            raise RuntimeError("EBI HMMER reported the job failed.")
        if status and status not in _HMMER_DONE_STATUS:
            continue  # PENDING / RUNNING / QUEUED — still cooking
        return _hmmer_web_parse_json(obj, max_hits)
    raise RuntimeError(
        f"EBI HMMER timed out after {_ONLINE_MAX_WAIT_S}s — try again "
        f"or use the Local tab with a downloaded Pfam database.")
