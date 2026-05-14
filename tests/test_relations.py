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


def test_ensure_row_accepts_null_tail():
    """An ambiguous-tail row keeps the assertion with `tail=null` +
    `ambiguous_candidates` — the schema allows this so the row isn't
    silently dropped at write/load time."""
    relations._ensure_row(
        {"head": "a", "type": "ally_of", "tail": None, "tail_name": "暮落"}
    )


def test_ensure_row_rejects_non_string_non_null_tail():
    with pytest.raises(ValueError, match="tail"):
        relations._ensure_row({"head": "a", "type": "x", "tail": 123})


def test_ensure_row_rejects_missing_tail_key():
    with pytest.raises(ValueError, match="tail"):
        relations._ensure_row({"head": "a", "type": "x"})


# --- parse_curated_relations_file -----------------------------------


def test_parse_curated_relations_happy_path(tmp_path):
    p = tmp_path / "relations_curated.jsonl"
    p.write_text(
        "\n".join([
            "# a comment",
            "",
            json.dumps({"head": "char_a", "type": "member_of", "tail": "ent_x"}),
            json.dumps({"head": "char_b", "type": "ally_of", "tail": None,
                        "tail_name": "暮落"}),
        ]) + "\n",
        encoding="utf-8",
    )
    entries, errors = relations.parse_curated_relations_file(p)
    assert errors == []
    assert len(entries) == 2


def test_parse_curated_relations_collects_errors(tmp_path):
    p = tmp_path / "relations_curated.jsonl"
    p.write_text(
        "\n".join([
            "not json",
            json.dumps([1, 2]),  # not a dict
            json.dumps({"head": "ok", "type": "t", "tail": "x"}),
            json.dumps({"head": "missing-type", "tail": "x"}),
        ]) + "\n",
        encoding="utf-8",
    )
    entries, errors = relations.parse_curated_relations_file(p)
    assert len(entries) == 1
    assert {e["reason"].split(":")[0] for e in errors} >= {
        "invalid JSON",
        "not a JSON object",
        "relations row missing/empty 'type'",
    }


def test_parse_curated_relations_missing_file_is_empty(tmp_path):
    entries, errors = relations.parse_curated_relations_file(tmp_path / "absent.jsonl")
    assert entries == [] and errors == []


# --- collate_relations ----------------------------------------------


def _write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_collate_relations_merges_per_char_files(tmp_path):
    rroot = tmp_path / "kb_relations"
    chars = rroot / "chars"
    chars.mkdir(parents=True)
    _write_jsonl(chars / "char_a.jsonl", [_r("char_a", "member_of", "ent_x")])
    _write_jsonl(chars / "char_b.jsonl", [_r("char_b", "ally_of", "char_a")])
    rows, errs = relations.collate_relations(rroot)
    assert errs == []
    assert {r["head"] for r in rows} == {"char_a", "char_b"}
    # All baked rows carry source="bake".
    assert all(r["source"] == "bake" for r in rows)


def test_collate_relations_empty_when_root_missing(tmp_path):
    """A pre-bake KB has no `kb_relations/` dir; collation returns []."""
    rows, errs = relations.collate_relations(tmp_path / "absent")
    assert rows == [] and errs == []


def test_collate_relations_curated_overrides_bake(tmp_path):
    """A curated entry with the same (head, type, tail) as a baked row
    replaces it — the curator's hand-edit wins."""
    rroot = tmp_path / "kb_relations"
    chars = rroot / "chars"
    chars.mkdir(parents=True)
    _write_jsonl(
        chars / "char_a.jsonl",
        [_r("char_a", "member_of", "ent_x", notes="from bake")],
    )
    curated = tmp_path / "curated.jsonl"
    _write_jsonl(
        curated,
        [_r("char_a", "member_of", "ent_x", notes="from curator")],
    )
    rows, errs = relations.collate_relations(rroot, curated)
    assert errs == []
    assert len(rows) == 1
    assert rows[0]["notes"] == "from curator"
    assert rows[0]["source"] == "curated"


def test_collate_relations_curated_adds_new_assertions(tmp_path):
    """Curated entries without a matching baked row are additive — not
    every assertion has to be in the bake."""
    rroot = tmp_path / "kb_relations"
    rroot.mkdir()  # no chars/ subdir → 0 bake rows
    curated = tmp_path / "curated.jsonl"
    _write_jsonl(
        curated,
        [_r("ent_x", "creator_of", "ent_y"), _r("char_a", "rival_of", "char_b")],
    )
    rows, errs = relations.collate_relations(rroot, curated)
    assert errs == []
    assert len(rows) == 2
    assert all(r["source"] == "curated" for r in rows)


def test_collate_relations_sorted_by_head_type_tail(tmp_path):
    rroot = tmp_path / "kb_relations"
    chars = rroot / "chars"
    chars.mkdir(parents=True)
    _write_jsonl(
        chars / "char_z.jsonl",
        [_r("char_z", "ally_of", "char_a"), _r("char_z", "ally_of", "char_b")],
    )
    _write_jsonl(chars / "char_a.jsonl", [_r("char_a", "member_of", "ent_x")])
    rows, _ = relations.collate_relations(rroot)
    # `char_a` row sorts before any `char_z` row.
    assert rows[0]["head"] == "char_a"
    # Within `char_z`, `ally_of` rows sort by tail.
    z_rows = [r for r in rows if r["head"] == "char_z"]
    assert [r["tail"] for r in z_rows] == ["char_a", "char_b"]


def test_collate_relations_dedup_keeps_ambiguous_tail_name_distinct(tmp_path):
    """Two ambiguous-tail rows that point at different surfaces must not
    collapse — the dedup key includes tail_name when tail is null."""
    rroot = tmp_path / "kb_relations"
    chars = rroot / "chars"
    chars.mkdir(parents=True)
    _write_jsonl(
        chars / "char_x.jsonl",
        [
            _r("char_x", "ally_of", None, tail_name="暮落"),
            _r("char_x", "ally_of", None, tail_name="沉渊"),
        ],
    )
    rows, _ = relations.collate_relations(rroot)
    assert len(rows) == 2
    assert {r["tail_name"] for r in rows} == {"暮落", "沉渊"}
