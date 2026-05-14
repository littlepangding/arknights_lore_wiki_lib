"""Unit tests for `libs.kb.entities` — the deterministic entity layer.

What's load-bearing here, beyond the obvious round-trip:

- **ID stability**: `synthetic_entity_id` must be deterministic across
  invocations and across processes (sha-of-utf-8-bytes), or the entity
  graph's primary keys silently drift.
- **Operator-precedence**: a curated entry whose `name` is an existing
  operator alias must be dropped, not merged — otherwise `绩` (the
  *NPC*) would silently steal hits from the operator-table-backed
  resolver.
- **Curator-over-auto-seed**: an auto-seeded `unknown` placeholder must
  yield to curated upgrades on the same `name`, or the curator can
  never fix a misclassification.
- **`name:null` hints stay outside**: this module accepts no `null`-
  named row by construction (the curated parser requires `name`), so
  TC-1's "hint, never a guessed id" stays honest.
"""

from __future__ import annotations

import json

import pytest

from libs.kb import entities


# --- synthetic id ------------------------------------------------------


def test_synthetic_entity_id_stable():
    a = entities.synthetic_entity_id("绩")
    b = entities.synthetic_entity_id("绩")
    assert a == b
    assert a.startswith("ent_") and len(a) == len("ent_") + 6


def test_synthetic_entity_id_changes_per_name():
    assert entities.synthetic_entity_id("绩") != entities.synthetic_entity_id("颉")


# --- operator entities -------------------------------------------------


def _mf(char_id: str, **kw) -> dict:
    return {"char_id": char_id, "name": kw.get("name"), "appellation": kw.get("appellation"), "nationId": kw.get("nationId")}


def test_build_operator_entities_basic():
    cm = {
        "char_002_amiya": _mf("char_002_amiya", name="阿米娅", appellation="Amiya"),
        "char_2025_shu": _mf("char_2025_shu", name="黍", appellation="Shu"),
    }
    rows = entities.build_operator_entities(cm, None, set())
    assert [r["id"] for r in rows] == ["char_002_amiya", "char_2025_shu"]
    amiya = rows[0]
    assert amiya["entity_type"] == "operator"
    assert amiya["char_id"] == "char_002_amiya"
    assert amiya["aliases"] == ["阿米娅", "Amiya"]
    assert amiya["sources"] == ["character_table"]


def test_build_operator_entities_dedup_when_name_eq_appellation():
    cm = {"char_w": _mf("char_w", name="W", appellation="W")}
    rows = entities.build_operator_entities(cm, None, set())
    assert rows[0]["aliases"] == ["W"]


def test_build_operator_entities_with_curated_aliases():
    cm = {"char_2025_shu": _mf("char_2025_shu", name="黍", appellation="Shu")}
    curated = {"黍": ["谓我何求"]}
    rows = entities.build_operator_entities(cm, curated, set())
    assert rows[0]["aliases"] == ["黍", "Shu", "谓我何求"]
    assert "char_alias.txt" in rows[0]["sources"]


def test_build_operator_entities_skips_curated_when_name_ambiguous():
    cm = {
        "char_a": _mf("char_a", name="暮落", appellation="A"),
        "char_b": _mf("char_b", name="暮落", appellation="B"),
    }
    curated = {"暮落": ["沉渊"]}
    rows = entities.build_operator_entities(cm, curated, ambiguous_canonicals={"暮落"})
    for r in rows:
        assert "沉渊" not in r["aliases"]
        assert r["sources"] == ["character_table"]


# --- curated parser ----------------------------------------------------


def _write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_parse_curated_entities_happy_path(tmp_path):
    p = tmp_path / "entities_curated.jsonl"
    _write_jsonl(p, [
        {"name": "绩", "entity_type": "npc", "aliases": ["绩"], "notes": "年家三女"},
        {"name": "罗德岛", "entity_type": "organization"},
    ])
    entries, errors = entities.parse_curated_entities_file(p)
    assert errors == []
    assert [e["name"] for e in entries] == ["绩", "罗德岛"]


def test_parse_curated_entities_missing_file_is_empty(tmp_path):
    entries, errors = entities.parse_curated_entities_file(tmp_path / "absent.jsonl")
    assert entries == [] and errors == []


def test_parse_curated_entities_rejects_operator_type(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(
        json.dumps({"name": "X", "entity_type": "operator"}) + "\n", encoding="utf-8"
    )
    entries, errors = entities.parse_curated_entities_file(p)
    assert entries == []
    assert len(errors) == 1 and "entity_type" in errors[0]["reason"]


def test_parse_curated_entities_rejects_bad_lines(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(
        "\n".join([
            "# a comment",
            "",
            "not json",
            json.dumps([1, 2]),  # not a dict
            json.dumps({"entity_type": "npc"}),  # missing name
            json.dumps({"name": "ok", "entity_type": "npc"}),
        ]) + "\n",
        encoding="utf-8",
    )
    entries, errors = entities.parse_curated_entities_file(p)
    assert [e["name"] for e in entries] == ["ok"]
    assert {e["reason"].split(":")[0] for e in errors} >= {
        "invalid JSON",
        "not a JSON object",
        "missing/empty `name`",
    }


# --- curated build + operator precedence ------------------------------


def test_build_curated_entities_drops_operator_alias():
    alias_to_char_ids = {"阿米娅": ["char_002_amiya"]}
    entries = [{"name": "阿米娅", "entity_type": "npc"}]
    rows, warnings = entities.build_curated_entities(entries, alias_to_char_ids)
    assert rows == []
    assert len(warnings) == 1
    assert warnings[0]["operator_candidates"] == ["char_002_amiya"]


def test_build_curated_entities_assigns_synthetic_id():
    entries = [{"name": "绩", "entity_type": "npc"}]
    rows, _ = entities.build_curated_entities(entries, alias_to_char_ids={})
    assert rows[0]["id"] == entities.synthetic_entity_id("绩")
    assert rows[0]["char_id"] is None
    assert rows[0]["aliases"][0] == "绩"  # name is first alias even if absent in `aliases`


def test_build_curated_entities_honors_explicit_id():
    entries = [{"id": "ent_custom", "name": "罗德岛", "entity_type": "organization"}]
    rows, _ = entities.build_curated_entities(entries, alias_to_char_ids={})
    assert rows[0]["id"] == "ent_custom"


# --- auto-seeding ------------------------------------------------------


def test_build_auto_seeded_entities_promotes_unresolved():
    unresolved = {"上尉": ["act31side"], "黍": ["act31side"]}
    rows = entities.build_auto_seeded_entities(
        unresolved, curated_names=set(), existing_ids=set()
    )
    names = {r["name"] for r in rows}
    assert names == {"上尉", "黍"}
    assert all(r["entity_type"] == "unknown" for r in rows)
    assert all(r["sources"] == ["kb_summaries:<关键人物>"] for r in rows)


def test_build_auto_seeded_skips_curated_names():
    unresolved = {"绩": ["act31side"]}
    rows = entities.build_auto_seeded_entities(
        unresolved, curated_names={"绩"}, existing_ids=set()
    )
    assert rows == []


def test_build_auto_seeded_does_not_mutate_existing_ids():
    existing = {"char_a"}
    entities.build_auto_seeded_entities(
        {"绩": ["e1"]}, curated_names=set(), existing_ids=existing
    )
    assert existing == {"char_a"}


def test_invert_unresolved_by_event():
    by_event = {"e1": ["绩", "颉"], "e2": ["绩"], "e3": []}
    flipped = entities.invert_unresolved_by_event(by_event)
    assert flipped == {"绩": ["e1", "e2"], "颉": ["e1"]}


# --- top-level builder -------------------------------------------------


def test_build_entities_top_level_integration(tmp_path):
    cm = {
        "char_2025_shu": _mf("char_2025_shu", name="黍", appellation="Shu"),
        "char_002_amiya": _mf("char_002_amiya", name="阿米娅", appellation="Amiya"),
    }
    alias_to_char_ids = {
        "黍": ["char_2025_shu"],
        "Shu": ["char_2025_shu"],
        "阿米娅": ["char_002_amiya"],
        "Amiya": ["char_002_amiya"],
    }
    curated_path = tmp_path / "entities_curated.jsonl"
    _write_jsonl(curated_path, [
        {"name": "绩", "entity_type": "npc", "notes": "年家三女"},
        {"name": "阿米娅", "entity_type": "npc"},  # collides with operator → dropped
    ])
    # The caller (kb_build) hands us the already-accumulated unresolved
    # map from participants.build_char_to_events_summary; only 颉 fell
    # through the alias index there.
    unresolved = {"颉": ["act31side"]}

    summary = entities.build_entities(
        cm,
        alias_to_char_ids=alias_to_char_ids,
        curated_aliases=None,
        ambiguous_canonicals=set(),
        curated_entities_path=curated_path,
        unresolved_summary_names=unresolved,
    )
    by_name = {e["name"]: e for e in summary["entities"]}

    # Operators present.
    assert by_name["黍"]["entity_type"] == "operator"
    assert by_name["阿米娅"]["entity_type"] == "operator"
    # Curated NPC accepted (no operator collision).
    assert by_name["绩"]["entity_type"] == "npc"
    assert by_name["绩"]["sources"] == ["entities_curated.jsonl"]
    # Auto-seeded: 颉 was the only unresolved name passed in.
    assert by_name["颉"]["entity_type"] == "unknown"
    assert "黍" not in [e["name"] for e in summary["entities"] if e["entity_type"] == "unknown"]
    # Curated collision was warned about, not silently merged.
    assert summary["curated_count"] == 1
    assert any(
        w["name"] == "阿米娅" for w in summary["curated_warnings"]
    )
    # Operator-first sort order.
    assert summary["entities"][0]["entity_type"] == "operator"


def test_build_entities_no_curated_no_unresolved():
    cm = {"char_002_amiya": _mf("char_002_amiya", name="阿米娅", appellation="Amiya")}
    summary = entities.build_entities(
        cm,
        alias_to_char_ids={"阿米娅": ["char_002_amiya"]},
    )
    assert summary["operator_count"] == 1
    assert summary["curated_count"] == 0
    assert summary["auto_seeded_count"] == 0
    assert summary["entities"][0]["id"] == "char_002_amiya"


# --- I/O + alias index ------------------------------------------------


def test_write_and_load_round_trip(tmp_path):
    rows = [
        {
            "id": "char_002_amiya",
            "name": "阿米娅",
            "entity_type": "operator",
            "char_id": "char_002_amiya",
            "appellation": "Amiya",
            "aliases": ["阿米娅", "Amiya"],
            "sources": ["character_table"],
        },
        {
            "id": "ent_abcdef",
            "name": "绩",
            "entity_type": "npc",
            "char_id": None,
            "appellation": None,
            "aliases": ["绩"],
            "sources": ["entities_curated.jsonl"],
        },
    ]
    p = tmp_path / "entities.jsonl"
    entities.write_entities_jsonl(p, rows)
    loaded = entities.load_entities(p)
    assert loaded == rows


def test_load_entities_missing_file_is_empty(tmp_path):
    assert entities.load_entities(tmp_path / "absent.jsonl") == []


def test_build_entity_alias_index_collects_collisions():
    rows = [
        {"id": "char_a", "aliases": ["暮落"]},
        {"id": "char_b", "aliases": ["暮落"]},
        {"id": "ent_x", "aliases": ["绩"]},
    ]
    idx = entities.build_entity_alias_index(rows)
    assert idx["暮落"] == ["char_a", "char_b"]
    assert idx["绩"] == ["ent_x"]
