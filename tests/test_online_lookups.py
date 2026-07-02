"""test_online_lookups — Babs / agent-API online reference-database lookups.

The `*-search` endpoints (fpbase / uniprot / literature / genbank / wikipedia /
web / patent) send only a QUERY STRING to public databases, gated behind the
human-armed `allow_online_lookups` setting (an autonomous agent can't self-arm).

No real network here: engine tests monkeypatch `splicecraft_search._online_http`
(the single fetch chokepoint) with canned bodies matching the REAL API shapes
captured live; handler tests patch `_get_setting` + the engine funcs in the
`splicecraft_agent` namespace ([CONV] patch-the-sibling rule). The autouse
`_protect_user_data` fixture sandboxes the data dir.
"""
import json

import pytest

import splicecraft as sc
import splicecraft_agent as sa
import splicecraft_dataaccess as da
import splicecraft_search as ss

H = sc._state._AGENT_HANDLERS
LOOKUP_EPS = ["fpbase-search", "uniprot-search", "literature-search",
              "genbank-search", "wikipedia-search", "web-search",
              "patent-search"]
_GATE_KEYS = ("allow_online_lookups", "brave_search_api_key",
              "patentsview_api_key")


# ── registration / config guards ────────────────────────────────────────────
def test_all_endpoints_registered_readonly():
    for e in LOOKUP_EPS:
        assert e in H, f"{e} not registered in _AGENT_HANDLERS"
        _fn, write = H[e]
        assert write is False, f"{e} must be read-only (write flag False)"


def test_endpoints_discoverable_by_babs():
    names = {e["name"] for e in sc._babs_list_endpoints({"filter": "search"})["endpoints"]}
    assert {"fpbase-search", "patent-search", "web-search"} <= names


def test_gate_and_keys_out_of_agent_allowlist():
    # The human-only egress switch + the secret keys must NOT be agent-settable.
    for k in _GATE_KEYS:
        assert k not in sc._AGENT_SETTINGS_ALLOWLIST


def test_keys_are_sensitive_for_log_redaction():
    assert {"brave_search_api_key", "patentsview_api_key"} <= da._SENSITIVE_SETTING_KEYS


def test_keys_in_settings_schema():
    for k in _GATE_KEYS:
        assert k in sc._SETTINGS_SCHEMA


def test_get_settings_does_not_leak_gate_or_keys():
    body = H["get-settings"][0](None, {})
    settings = body["settings"]
    for k in _GATE_KEYS:
        assert k not in settings


# ── egress gate ──────────────────────────────────────────────────────────────
def test_disarmed_refuses_403_with_arming_hint(monkeypatch):
    monkeypatch.setattr(sa, "_get_setting", lambda k, d=None: False)
    for e in LOOKUP_EPS:
        body, status = H[e][0](None, {"query": "x"})
        assert status == 403, e
        assert "disarmed" in body["error"] and "allow_online_lookups" in body["error"]


def _arm(monkeypatch, **keys):
    """Arm the gate; optionally set provider keys. Everything else → default."""
    def fake(k, d=None):
        if k == "allow_online_lookups":
            return True
        if k in keys:
            return keys[k]
        return d
    monkeypatch.setattr(sa, "_get_setting", fake)


# ── input validation ──────────────────────────────────────────────────────────
def test_missing_and_blank_query_400(monkeypatch):
    _arm(monkeypatch)
    assert H["fpbase-search"][0](None, {})[1] == 400
    assert H["fpbase-search"][0](None, {"query": "   "})[1] == 400
    assert H["fpbase-search"][0](None, {"query": 5})[1] == 400


def test_overlong_query_400(monkeypatch):
    _arm(monkeypatch)
    big = "z" * (ss._ONLINE_LOOKUP_QUERY_MAX + 1)
    assert H["fpbase-search"][0](None, {"query": big})[1] == 400


def test_bad_max_hits_400(monkeypatch):
    _arm(monkeypatch)
    assert H["fpbase-search"][0](None, {"query": "x", "max_hits": "lots"})[1] == 400


def test_genbank_bad_db_400(monkeypatch):
    _arm(monkeypatch)
    assert H["genbank-search"][0](None, {"query": "x", "db": "bogus"})[1] == 400


def test_genbank_nuccore_alias_ok(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(sa, "_ncbi_db_search", lambda q, db, n: ([], 0))
    body = H["genbank-search"][0](None, {"query": "x", "db": "nuccore"})
    assert body["ok"] and body["db"] == "nucleotide"


# ── handler success shape / provider labels / error mapping ──────────────────
def test_success_shape(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(sa, "_fpbase_search", lambda q, n: [{"name": "mCherry"}])
    body = H["fpbase-search"][0](None, {"query": "mCherry"})
    assert body["ok"] and body["source"] == "FPbase"
    assert body["query"] == "mCherry" and body["count"] == 1
    assert body["results"][0]["name"] == "mCherry"


def test_web_provider_label(monkeypatch):
    monkeypatch.setattr(sa, "_web_search",
                        lambda q, n, key: [{"title": "t", "url": "u", "snippet": "s"}])
    _arm(monkeypatch)
    assert H["web-search"][0](None, {"query": "x"})["provider"] == "DuckDuckGo (best-effort)"
    _arm(monkeypatch, brave_search_api_key="KEY")
    assert H["web-search"][0](None, {"query": "x"})["provider"] == "Brave"


def test_patent_provider_label_and_total(monkeypatch):
    monkeypatch.setattr(sa, "_patent_search",
                        lambda q, n, key: ([{"number": "US1"}], 42))
    _arm(monkeypatch)
    b = H["patent-search"][0](None, {"query": "x"})
    assert b["provider"].startswith("Google Patents") and b["total_matches"] == 42
    _arm(monkeypatch, patentsview_api_key="KEY")
    assert H["patent-search"][0](None, {"query": "x"})["provider"] == "PatentsView"


def test_runtimeerror_maps_502(monkeypatch):
    _arm(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("Connection timed out")
    monkeypatch.setattr(sa, "_fpbase_search", boom)
    body, status = H["fpbase-search"][0](None, {"query": "x"})
    assert status == 502 and "timed out" in body["error"]


def test_unexpected_error_maps_500(monkeypatch):
    _arm(monkeypatch)

    def boom(*a, **k):
        raise ValueError("weird")
    monkeypatch.setattr(sa, "_uniprot_search", boom)
    body, status = H["uniprot-search"][0](None, {"query": "x"})
    assert status == 500 and body["type"] == "ValueError"


# ── engine parse tests (canned bodies matching the real API shapes) ──────────
def _route(mapping):
    """Fake `_online_http` dispatching by URL substring. Dict/list → JSON."""
    def fake(url, **kw):
        for frag, body in mapping.items():
            if frag in url:
                return body if isinstance(body, str) else json.dumps(body)
        raise AssertionError(f"unexpected url: {url}")
    return fake


def test_fpbase_parse_and_code_mapping(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"fpbase.org": [
        {"name": "mCherry", "slug": "mcherry", "seq": "MAS", "genbank": "XX000000",
         "uniprot": None, "agg": "t", "switch_type": "b", "default_state": "d1",
         "states": [{"slug": "d1", "ex_max": 597, "em_max": 610, "qy": 0.2,
                     "ext_coeff": 50000, "brightness": 10}]}]}))
    r = ss._fpbase_search("mcherry", 5)
    assert r[0]["name"] == "mCherry" and r[0]["ex_max"] == 597 and r[0]["em_max"] == 610
    assert r[0]["oligomerization"] == "tetramer" and r[0]["switch_type"] == "basic"
    assert r[0]["genbank"] == "XX000000" and r[0]["url"].endswith("/protein/mcherry/")


def test_fpbase_paginated_and_skips_nameless(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"fpbase.org": {"results": [
        {"slug": "x"},                                   # no name -> dropped
        {"name": "GFP", "slug": "gfp", "states": []}]}}))
    r = ss._fpbase_search("x", 5)
    assert len(r) == 1 and r[0]["name"] == "GFP" and r[0]["ex_max"] is None


def test_uniprot_parse(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"uniprot.org": {"results": [
        {"primaryAccession": "P0", "entryType": "UniProtKB reviewed (Swiss-Prot)",
         "organism": {"scientificName": "Escherichia coli"},
         "proteinDescription": {"recommendedName": {"fullName": {"value": "Beta-lactamase"}}},
         "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Hydrolyzes."}]}],
         "keywords": [{"name": "Antibiotic resistance"}]}]}}))
    r = ss._uniprot_search("bla", 5)
    assert r[0]["accession"] == "P0" and r[0]["name"] == "Beta-lactamase"
    assert r[0]["organism"] == "Escherichia coli" and r[0]["reviewed"] is True
    assert r[0]["function"] == "Hydrolyzes." and "Antibiotic resistance" in r[0]["keywords"]
    assert r[0]["url"].endswith("/uniprotkb/P0")


def test_europepmc_parse_and_url_priority(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"europepmc": {"resultList": {"result": [
        {"title": "A <i>study</i>", "authorString": "Doe J", "journalTitle": "J Sci",
         "pubYear": "2020", "doi": "10.1/x", "pmid": "123", "isOpenAccess": "Y",
         "abstractText": "An abstract."}]}}}))
    r = ss._europepmc_search("x", 5)
    assert r[0]["title"] == "A study" and r[0]["doi"] == "10.1/x"
    assert r[0]["url"] == "https://doi.org/10.1/x" and r[0]["is_open_access"] is True
    assert r[0]["abstract"] == "An abstract."


def test_genbank_two_call(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({
        "esearch.fcgi": {"esearchresult": {"idlist": ["111"], "count": "9"}},
        "esummary.fcgi": {"result": {"uids": ["111"], "111": {
            "accessionversion": "NC_1.1", "title": "Foo genome",
            "organism": "Escherichia coli", "slen": 1000}}}}))
    recs, total = ss._ncbi_db_search("foo", "nucleotide", 5)
    assert total == 9 and recs[0]["accession"] == "NC_1.1" and recs[0]["length"] == 1000
    assert "nuccore" in recs[0]["url"]


def test_genbank_protein_url(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({
        "esearch.fcgi": {"esearchresult": {"idlist": ["1"], "count": "1"}},
        "esummary.fcgi": {"result": {"uids": ["1"], "1": {"caption": "ABC1", "title": "P"}}}}))
    recs, _total = ss._ncbi_db_search("p", "protein", 5)
    assert recs[0]["accession"] == "ABC1" and "/protein/" in recs[0]["url"]


def test_genbank_empty(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({
        "esearch.fcgi": {"esearchresult": {"idlist": [], "count": "0"}}}))
    recs, total = ss._ncbi_db_search("zzz", "nucleotide", 5)
    assert recs == [] and total == 0


def test_wikipedia_two_call(monkeypatch):
    def fake(url, **kw):
        if "list=search" in url:
            return json.dumps({"query": {"search": [
                {"title": "Plasmid", "pageid": 5, "snippet": "a <b>plasmid</b>"}]}})
        if "prop=extracts" in url:
            return json.dumps({"query": {"pages": {"5": {"pageid": 5,
                                                         "extract": "A plasmid is a molecule."}}}})
        raise AssertionError(url)
    monkeypatch.setattr(ss, "_online_http", fake)
    r = ss._wikipedia_search("plasmid", 3)
    assert r[0]["title"] == "Plasmid" and r[0]["summary"] == "A plasmid is a molecule."
    assert r[0]["url"] == "https://en.wikipedia.org/?curid=5"


def test_wikipedia_extract_failure_falls_back_to_snippet(monkeypatch):
    def fake(url, **kw):
        if "list=search" in url:
            return json.dumps({"query": {"search": [
                {"title": "P", "pageid": 5, "snippet": "snip <b>x</b>"}]}})
        raise RuntimeError("extracts down")
    monkeypatch.setattr(ss, "_online_http", fake)
    r = ss._wikipedia_search("p", 3)
    assert r[0]["summary"] == "snip x"      # snippet, HTML stripped


def test_web_brave_parse(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"brave.com": {"web": {"results": [
        {"title": "T", "url": "http://e", "description": "d <strong>e</strong>"}]}}}))
    r = ss._web_search("x", 5, "KEY")
    assert r[0]["title"] == "T" and r[0]["url"] == "http://e" and r[0]["snippet"] == "d e"


def test_web_ddg_html_parse_and_redirect_decode(monkeypatch):
    html = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg='
            'https%3A%2F%2Fexample.com%2Fp&amp;rut=z">Big <b>Title</b></a>'
            '<a class="result__snippet" href="x">Some snippet</a>')
    monkeypatch.setattr(ss, "_online_http", lambda url, **kw: html)
    r = ss._web_search("x", 5, "")
    assert r[0]["title"] == "Big Title" and r[0]["url"] == "https://example.com/p"
    assert r[0]["snippet"] == "Some snippet"


def test_web_ddg_block_raises(monkeypatch):
    monkeypatch.setattr(ss, "_online_http",
                        lambda url, **kw: "<html><head><title>Sorry</title> anomaly")
    with pytest.raises(RuntimeError, match="rate-limited"):
        ss._web_search("x", 5, "")


def test_patent_google_parse_and_entities(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"patents.google.com": {"results": {
        "total_num_results": 62, "cluster": [{"result": [
            {"id": "patent/US123A/en", "patent": {
                "title": "Gene &amp; method", "snippet": "Snippet &hellip;",
                "publication_number": "US123A", "priority_date": "2019-01-01",
                "assignee": "Acme Corp"}}]}]}}}))
    recs, total = ss._patent_search("x", 5, "")
    assert total == 62 and recs[0]["number"] == "US123A"
    assert recs[0]["title"] == "Gene & method" and recs[0]["assignee"] == "Acme Corp"
    assert recs[0]["url"].endswith("/patent/US123A/en")


def test_patent_google_id_fallback_number(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"patents.google.com": {"results": {
        "cluster": [{"result": [{"id": "patent/EP999B1/en", "patent": {"title": "t"}}]}]}}}))
    recs, _t = ss._patent_search("x", 5, "")
    assert recs[0]["number"] == "EP999B1"


def test_patent_google_block_raises(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", lambda url, **kw: "<html>Sorry...</html>")
    with pytest.raises(RuntimeError, match="rate-limited"):
        ss._patent_search("x", 5, "")


def test_patent_patentsview_parse(monkeypatch):
    monkeypatch.setattr(ss, "_online_http", _route({"patentsview.org": {
        "patents": [{"patent_id": "10000000", "patent_title": "Widget",
                     "patent_date": "2020-01-01", "patent_abstract": "An abstract.",
                     "assignees": [{"assignee_organization": "Acme"}]}],
        "total_hits": 5}}))
    recs, total = ss._patent_search("x", 5, "PVKEY")
    assert total == 5 and recs[0]["number"] == "10000000" and recs[0]["title"] == "Widget"
    assert recs[0]["assignee"] == "Acme" and recs[0]["url"].endswith("/patent/US10000000/en")


def test_ddg_parse_no_results_returns_empty(monkeypatch):
    # A genuine no-hits page (no result__a, no block markers) -> [] not an error.
    assert ss._ddg_parse_html("<html><body>No results.</body></html>") == []
