"""Unit tests for `libs.kb.relations` — the typed-relation skeleton.

The module is intentionally minimal until the LLM relation bake lands.
What it must guarantee right now:

- **Loading a missing file degrades silently to `[]`** — pre-bake
  `kb_query relations …` calls return empty, never crash.
- **Malformed rows fail loud** — a corrupt file truncating the graph
  silently would be worse than a clear error during load.
- **Undirected `between` matches both orientations** — agents asking
  about a typed link rarely know which side is `head`.
"""

from __future__ import annotations

import json

import pytest

from libs.kb import relations


# --- I/O round-trip --------------------------------------------------


def test_write_and_load_round_trip(tmp_path):
    rows = [
        {
            "head": "char_002_amiya",
            "type": "member_of",
            "tail": "ent_abcdef",
            "source_event_ids": ["main_12"],
            "notes": "Rhodes Island captain (placeholder)",
        }
    ]
    p = tmp_path / "relations.jsonl"
    relations.write_relations_jsonl(p, rows)
    assert relations.load_relations(p) == rows


def test_load_relations_missing_file_is_empty(tmp_path):
    assert relations.load_relations(tmp_path / "absent.jsonl") == []


def test_write_rejects_row_missing_head(tmp_path):
    p = tmp_path / "relations.jsonl"
    with pytest.raises(ValueError, match="head"):
        relations.write_relations_jsonl(p, [{"type": "x", "tail": "y"}])
    # The atomic-write contract: nothing should have landed on disk.
    assert not p.exists()


def test_write_rejects_row_with_empty_type(tmp_path):
    p = tmp_path / "relations.jsonl"
    with pytest.raises(ValueError, match="type"):
        relations.write_relations_jsonl(
            p, [{"head": "a", "type": "", "tail": "b"}]
        )


def test_load_raises_on_malformed_row(tmp_path):
    p = tmp_path / "relations.jsonl"
    p.write_text(
        json.dumps({"head": "a", "type": "x", "tail": "b"}) + "\n"
        + json.dumps({"head": "missing-tail"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        relations.load_relations(p)


# --- in-memory query helpers ----------------------------------------


def _r(head, type_, tail, **kw):
    return {"head": head, "type": type_, "tail": tail, **kw}


def test_relations_for_matches_head_or_tail():
    rows = [
        _r("a", "member_of", "org"),
        _r("b", "ally_of", "a"),
        _r("c", "ally_of", "d"),
    ]
    out = relations.relations_for(rows, "a")
    assert len(out) == 2
    assert {r["type"] for r in out} == {"member_of", "ally_of"}


def test_relations_for_returns_empty_for_unknown_entity():
    rows = [_r("a", "x", "b")]
    assert relations.relations_for(rows, "z") == []


def test_relations_between_is_undirected_by_default():
    """The assertion direction depends on the bake's framing — undirected
    is the kinder default for an agent querying `between(amiya, rhodes)`."""
    rows = [_r("a", "member_of", "b"), _r("b", "leads", "a")]
    out = relations.relations_between(rows, "a", "b")
    assert len(out) == 2


def test_relations_between_directed_filters_one_orientation():
    rows = [_r("a", "member_of", "b"), _r("b", "leads", "a")]
    out = relations.relations_between(rows, "a", "b", directed=True)
    assert len(out) == 1
    assert out[0]["type"] == "member_of"


def test_list_relation_types_sorted_distinct():
    rows = [
        _r("a", "member_of", "b"),
        _r("c", "ally_of", "d"),
        _r("e", "member_of", "f"),
    ]
    assert relations.list_relation_types(rows) == ["ally_of", "member_of"]
