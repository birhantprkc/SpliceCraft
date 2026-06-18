"""Structural + behaviour guard for the extracted .dna history layer (L2).

`splicecraft_history` holds the `.dna` `<HistoryTree>` model
(`_CommercialSaaSHistoryNode`) and the pure provenance helpers. Byte-for-byte
.dna round-tripping is covered by test_commercialsaas_io.py; this file pins the
extraction and the memory-flagged universal date format.
"""
from __future__ import annotations

import splicecraft as sc
import splicecraft_history


def test_history_primitives_in_sibling_and_reexported():
    names = ("_HISTORY_MONTHS", "_history_now_str", "_history_human_dt",
             "_CommercialSaaSHistoryNode", "_coerce_int_or_zero")
    missing = [n for n in names if not hasattr(splicecraft_history, n)]
    assert not missing, f"missing from splicecraft_history: {missing}"
    for n in names:
        assert getattr(sc, n) is getattr(splicecraft_history, n), (
            f"sc.{n} is not the splicecraft_history object"
        )


def test_universal_date_format_preserved():
    """The slash-free "JUN 9 2026 14:30" universal format must survive the move
    (it routes through `_history_human_dt`)."""
    assert sc._history_human_dt("2026-06-09T14:30") == "JUN 9 2026 14:30"
    assert sc._history_human_dt("2026.12.25") == "DEC 25 2026"   # CommercialSaaS Notes form
    assert sc._history_human_dt("") == ""
    assert sc._history_human_dt("not a date") == ""


def test_history_node_lineage():
    """The relocated model still builds + traverses a lineage (local ElementTree)."""
    node = sc._CommercialSaaSHistoryNode.new(
        name="child", seq_len=10, circular=True, operation="op")
    parent = sc._CommercialSaaSHistoryNode.new(
        name="par", seq_len=5, circular=False, operation="src")
    node.add_parent(parent)
    assert [p.name for p in node.parents] == ["par"]
