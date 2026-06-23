"""
test_record_cache — `_GB_PARSE_CACHE` isolation contract.

`_gb_text_to_record` caches parsed SeqRecords and hands each caller an isolation
COPY (`_clone_cached_record`) instead of a full `deepcopy` — ~7-8× faster
(57 ms → 8 ms on a 5 Mb / 5000-feature chromosome, the per-hit cost every
load-entry / diff / part-classify used to pay). The copy SHARES the immutable
`Seq`/locations but DUPLICATES the mutable feature/qualifier containers, so a
caller mutating the returned record must never poison the cache. These tests are
the tripwire: if a future refactor shares a mutable container (or a caller starts
mutating a shared object in place), one of them fails loudly.
"""
from __future__ import annotations

import copy

import pytest

import splicecraft as sc


def _gb_fixture() -> str:
    from Bio.SeqRecord import SeqRecord
    from Bio.SeqFeature import SeqFeature, FeatureLocation
    from Bio.Seq import Seq

    seq = Seq("ATGAAACGCATTAGCACCACCATTACCACCACCATCACCATTACCACAGGTAACGGTGCGGGCTGA" * 4)
    feats = [
        SeqFeature(FeatureLocation(0, 30), type="CDS",
                   qualifiers={"label": ["cds1"], "note": ["first"]}),
        SeqFeature(FeatureLocation(40, 80), type="gene",
                   qualifiers={"label": ["gene1"]}),
    ]
    rec = SeqRecord(seq, id="FIX", name="FIX",
                    annotations={"molecule_type": "DNA", "topology": "circular"})
    rec.features = feats
    return sc._record_to_gb_text(rec)


@pytest.fixture
def gb_text() -> str:
    return _gb_fixture()


def test_cache_hit_returns_distinct_containers(gb_text):
    a = sc._gb_text_to_record(gb_text)   # miss → parse + cache
    b = sc._gb_text_to_record(gb_text)   # hit → isolation copy
    assert a is not b
    assert a.features is not b.features
    assert a.features[0] is not b.features[0]
    assert a.features[0].qualifiers is not b.features[0].qualifiers
    assert a.annotations is not b.annotations


def test_feature_list_mutation_does_not_poison_cache(gb_text):
    a = sc._gb_text_to_record(gb_text)
    n0 = len(a.features)
    a.features.append(a.features[0])     # mutate the returned list…
    a.features.clear()                   # …and empty it
    b = sc._gb_text_to_record(gb_text)   # a subsequent hit must be clean
    assert len(b.features) == n0


def test_qualifier_mutation_does_not_poison_cache(gb_text):
    a = sc._gb_text_to_record(gb_text)
    a.features[0].qualifiers["label"][0] = "HACKED"   # mutate a value list
    a.features[0].qualifiers["new"] = ["x"]           # add a key
    del a.features[1].qualifiers["label"]             # drop a key
    b = sc._gb_text_to_record(gb_text)
    assert b.features[0].qualifiers["label"][0] == "cds1"
    assert "new" not in b.features[0].qualifiers
    assert "label" in b.features[1].qualifiers


def test_seq_and_annotation_reassignment_does_not_poison_cache(gb_text):
    a = sc._gb_text_to_record(gb_text)
    orig_len = len(a.seq)
    a.seq = a.seq[:10]                   # rebind on the copy
    a.annotations["topology"] = "linear"
    b = sc._gb_text_to_record(gb_text)
    assert len(b.seq) == orig_len
    assert b.annotations.get("topology") == "circular"


def test_isolation_copy_content_equals_deepcopy(gb_text):
    """The cheap copy must be content-identical to what a full deepcopy
    would have produced — same seq, feature count, coords, qualifiers."""
    a = sc._gb_text_to_record(gb_text)
    d = copy.deepcopy(a)
    assert str(a.seq) == str(d.seq)
    assert len(a.features) == len(d.features)
    for fa, fd in zip(a.features, d.features):
        assert fa.type == fd.type
        assert int(fa.location.start) == int(fd.location.start)
        assert int(fa.location.end) == int(fd.location.end)
        assert fa.qualifiers == fd.qualifiers


def test_immutable_seq_is_shared_across_hits(gb_text):
    """The speedup's source: the immutable `Seq` is shared by reference (NOT
    deep-copied) across cache hits. Content identical, object identical."""
    a = sc._gb_text_to_record(gb_text)
    b = sc._gb_text_to_record(gb_text)
    assert a.seq is b.seq
    assert str(a.seq) == str(b.seq)
