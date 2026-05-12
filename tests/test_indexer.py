"""Tests for `libs.kb.indexer`. Pure helpers are exercised in isolation;
the integration paths use a hand-built tiny KB (for full control over
inferred-edge scenarios — subtraction, blocklist, match_class) or the
mini gamedata fixture via the `build_real_kb` factory in conftest.
"""

from __future__ import annotations

import json

import pytest

from libs.kb import indexer, paths


# --- parse_curated_alias_file ----------------------------------------


def test_parse_curated_alias_file_basic(tmp_path):
    p = tmp_path / "char_alias.txt"
    p.write_text("临光;玛嘉烈;Margaret\n阿米娅;Amiya\n", encoding="utf-8")
    out = indexer.parse_curated_alias_file(p)
    # canonical is the *key*, not in its own alias list
    assert out == {"临光": ["玛嘉烈", "Margaret"], "阿米娅": ["Amiya"]}


def test_parse_curated_alias_file_strips_and_skips_blank_lines(tmp_path):
    p = tmp_path / "alias.txt"
    p.write_text("\n  临光 ; 玛嘉烈 \n\n   \n", encoding="utf-8")
    out = indexer.parse_curated_alias_file(p)
    assert out == {"临光": ["玛嘉烈"]}


def test_parse_curated_alias_file_dedupes_repeated_canonicals(tmp_path):
    """Two lines starting with the same canonical concatenate, with
    duplicates removed so the resolver index doesn't double-count."""
    p = tmp_path / "alias.txt"
    p.write_text("临光;玛嘉烈\n临光;Margaret;玛嘉烈\n", encoding="utf-8")
    out = indexer.parse_curated_alias_file(p)
    assert out == {"临光": ["玛嘉烈", "Margaret"]}


def test_parse_curated_alias_file_returns_empty_when_missing(tmp_path):
    assert indexer.parse_curated_alias_file(tmp_path / "nope.txt") == {}


# --- classify_alias --------------------------------------------------


def test_classify_alias_canonical_short_for_single_zh_char():
    """Single-char operator names like `陈`/`年`/`夕` must NOT be
    silently dropped. They surface as `canonical_short` so consumers
    can downweight without losing recall."""
    assert indexer.classify_alias("陈", "canonical") == "canonical_short"


def test_classify_alias_canonical_for_multi_char():
    assert indexer.classify_alias("阿米娅", "canonical") == "canonical"
    assert indexer.classify_alias("Amiya", "canonical") == "canonical"


def test_classify_alias_curated_keeps_two_char_floor():
    assert indexer.classify_alias("玛嘉烈", "curated") == "curated"
    # single-char curated is dropped — these are noisier than canonical names
    assert indexer.classify_alias("玛", "curated") is None


def test_classify_alias_drops_blocklist_in_every_class():
    for src in ("canonical", "curated", "fuzzy"):
        assert indexer.classify_alias("博士", src) is None
        assert indexer.classify_alias("罗德岛", src) is None


def test_classify_alias_drops_empty_or_none():
    assert indexer.classify_alias("", "canonical") is None
    assert indexer.classify_alias("", "curated") is None


# --- compute_ambiguous_canonicals -----------------------------------


def test_compute_ambiguous_canonicals_finds_duplicate_names():
    cm = {
        "char_a1": {"name": "暮落"},
        "char_a2": {"name": "暮落"},
        "char_b": {"name": "阿米娅"},
    }
    assert indexer.compute_ambiguous_canonicals(cm) == {"暮落"}


def test_compute_ambiguous_canonicals_returns_empty_when_unique():
    cm = {"c1": {"name": "A"}, "c2": {"name": "B"}}
    assert indexer.compute_ambiguous_canonicals(cm) == set()


def test_compute_ambiguous_canonicals_ignores_nameless():
    cm = {"c1": {"name": ""}, "c2": {"name": None}}
    assert indexer.compute_ambiguous_canonicals(cm) == set()


# --- build_events_by_family -------------------------------------------


def test_build_events_by_family_groups_by_family():
    em = {
        "main_01": {"source_family": "mainline"},
        "act_x": {"source_family": "activity"},
        "act_y": {"source_family": "activity"},
        "rec_z": {"source_family": "operator_record"},
    }
    out = indexer.build_events_by_family(em)
    assert out["mainline"] == ["main_01"]
    assert out["activity"] == ["act_x", "act_y"]  # sorted
    assert out["operator_record"] == ["rec_z"]
    # empty families still present (callers expect every family key)
    assert out["mini_activity"] == []
    assert out["other"] == []


# --- build_char_to_events_deterministic -------------------------------


def test_deterministic_uses_storysets_json(tmp_path, make_char):
    kb = tmp_path / "kb"
    make_char(
        kb,
        "char_a",
        name="A",
        storysets=[
            {
                "storySetName": "x",
                "storyTxt": "p",
                "linked_event_id": "ev1",
                "linked_stage_idx": 2,
            }
        ],
    )
    make_char(kb, "char_b", name="B")  # no storysets
    cm = indexer.load_char_manifests(kb)
    out = indexer.build_char_to_events_deterministic(kb, cm)
    assert out == {
        "char_a": [{"event_id": "ev1", "stage_idx": 2, "story_set_name": "x"}]
    }
    # chars without any storysets aren't keyed in the index
    assert "char_b" not in out


# --- build_event_to_chars --------------------------------------------
# (the inferred-pass scenarios moved to test_participants.py with the
#  rewrite to the tiered `build_stage_participants`.)


def test_event_to_chars_merges_three_layers():
    det = {
        "char_a": [{"event_id": "ev1", "stage_idx": 2, "story_set_name": "ss"}],
    }
    participant = {
        "char_b": [
            {
                "event_id": "ev1",
                "stage_idx": 0,
                "source": "participant",
                "tier": "speaker",
                "spoke_lines": 3,
                "mention_count": 4,
                "matched_aliases": ["B"],
            },
            {
                "event_id": "ev1",
                "stage_idx": 3,
                "source": "participant",
                "tier": "named",
                "spoke_lines": 0,
                "mention_count": 2,
                "matched_aliases": ["B"],
            },
        ],
    }
    summary = {
        "char_c": [
            {
                "event_id": "ev1",
                "stage_idx": None,
                "source": "summary",
                "tier": "named",
                "matched_aliases": ["“C”"],
            }
        ]
    }
    out = indexer.build_event_to_chars(det, participant, summary)
    rows = out["ev1"]
    assert len(rows) == 4  # 1 deterministic + 2 participant + 1 summary

    det_row = next(r for r in rows if r["source"] == "deterministic")
    assert det_row["char_id"] == "char_a"
    assert det_row["stage_idx"] == 2
    assert det_row["story_set_name"] == "ss"
    assert "tier" not in det_row

    part_rows = [r for r in rows if r["source"] == "participant"]
    assert {r["stage_idx"] for r in part_rows} == {0, 3}
    for r in part_rows:
        assert r["tier"] in ("speaker", "named")
        assert r["matched_aliases"] == ["B"]
        assert "story_set_name" not in r

    sum_row = next(r for r in rows if r["source"] == "summary")
    assert sum_row["char_id"] == "char_c"
    assert sum_row["stage_idx"] is None
    assert sum_row["tier"] == "named"


def test_event_to_chars_sorted_char_then_stage_then_event_scoped_last():
    det = {
        "char_b": [{"event_id": "ev1", "stage_idx": 1, "story_set_name": "x"}],
        "char_a": [{"event_id": "ev1", "stage_idx": 0, "story_set_name": "y"}],
    }
    summary = {
        "char_a": [
            {
                "event_id": "ev1",
                "stage_idx": None,
                "source": "summary",
                "tier": "named",
                "matched_aliases": ["A"],
            }
        ]
    }
    out = indexer.build_event_to_chars(det, {}, summary)
    rows = out["ev1"]
    assert [r["char_id"] for r in rows] == ["char_a", "char_a", "char_b"]
    # within char_a: the stage-0 deterministic row before the event-scoped (None) summary row
    a_rows = [r for r in rows if r["char_id"] == "char_a"]
    assert a_rows[0]["source"] == "deterministic" and a_rows[0]["stage_idx"] == 0
    assert a_rows[1]["source"] == "summary" and a_rows[1]["stage_idx"] is None


# --- build_stage_table -----------------------------------------------


def test_stage_table_carries_source_family_and_prefix():
    em = {
        "ev1": {
            "source_family": "activity",
            "stages": [
                {
                    "idx": 0,
                    "name": "s0",
                    "avgTag": "行动前",
                    "file": "stage_00_s0.txt",
                    "length": 100,
                    "story_txt": "activities/x/s0",
                }
            ],
        }
    }
    rows = indexer.build_stage_table(em)
    assert rows == [
        {
            "event_id": "ev1",
            "stage_idx": 0,
            "name": "s0",
            "avgTag": "行动前",
            "source_family": "activity",
            "storyTxt_prefix": "activities/x",
            "file_path": "events/ev1/stage_00_s0.txt",
            "length": 100,
        }
    ]


# --- build_char_table ------------------------------------------------


def test_char_table_marks_participant_appearances():
    cm = {
        "char_a": {
            "char_id": "char_a",
            "name": "A",
            "nationId": "n1",
            "sections": ["profile"],
            "storyset_count": 0,
        },
        "char_b": {
            "char_id": "char_b",
            "name": "B",
            "nationId": None,
            "sections": [],
            "storyset_count": 1,
        },
    }
    participant = {
        "char_a": [
            {"event_id": "ev1", "stage_idx": 0, "source": "participant", "tier": "named"}
        ]
    }
    rows = indexer.build_char_table(cm, participant)
    rows_by_id = {r["char_id"]: r for r in rows}
    assert rows_by_id["char_a"]["has_participant_appearances"] is True
    assert rows_by_id["char_b"]["has_participant_appearances"] is False


# --- build_char_alias_index ------------------------------------------


def test_alias_index_raw_only_includes_name_and_appellation():
    cm = {
        "char_amiya": {"char_id": "char_amiya", "name": "阿米娅", "appellation": "Amiya"},
    }
    out = indexer.build_char_alias_index(cm)
    assert out["alias_to_char_ids"] == {
        "Amiya": ["char_amiya"],
        "阿米娅": ["char_amiya"],
    }


def test_alias_index_curated_attaches_when_canonical_unique():
    cm = {
        "char_nearl": {"char_id": "char_nearl", "name": "临光", "appellation": "Nearl"},
    }
    curated = {"临光": ["玛嘉烈"]}
    out = indexer.build_char_alias_index(cm, curated=curated)
    assert out["alias_to_char_ids"]["玛嘉烈"] == ["char_nearl"]
    assert out["alias_to_char_ids"]["Nearl"] == ["char_nearl"]


def test_alias_index_curated_attaches_to_all_owners_when_canonical_collides():
    """`暮落;沉渊` — both `暮落` and `沉渊` should resolve to BOTH
    char_ids. The resolver collapses ≥2-target rows to `Ambiguous`."""
    cm = {
        "char_a1": {"char_id": "char_a1", "name": "暮落", "appellation": "Aprot"},
        "char_a2": {"char_id": "char_a2", "name": "暮落", "appellation": "Aprot2"},
    }
    curated = {"暮落": ["沉渊"]}
    out = indexer.build_char_alias_index(cm, curated=curated)
    # `暮落` (the name itself) maps to both
    assert sorted(out["alias_to_char_ids"]["暮落"]) == ["char_a1", "char_a2"]
    # `沉渊` (curated) also maps to both
    assert sorted(out["alias_to_char_ids"]["沉渊"]) == ["char_a1", "char_a2"]


def test_alias_index_skips_curated_lines_with_no_owner():
    """Curated canonicals like `特蕾西娅` (NPC) have no operator-table
    backing, so the line cannot point at any char_id and is skipped."""
    cm = {"char_amiya": {"char_id": "char_amiya", "name": "阿米娅"}}
    curated = {"特蕾西娅": ["女皇"]}
    out = indexer.build_char_alias_index(cm, curated=curated)
    # 特蕾西娅 / 女皇 don't appear at all
    assert "特蕾西娅" not in out["alias_to_char_ids"]
    assert "女皇" not in out["alias_to_char_ids"]


# --- end-to-end via mini_gamedata fixture + chunker --------------


def test_build_all_indexes_against_mini_fixture(tmp_path, build_real_kb):
    kb = build_real_kb(tmp_path / "kb")
    # build_real_kb already runs build_all_indexes; re-run to capture summary
    summary = indexer.build_all_indexes(kb)

    assert summary["events"] == 3
    # 2 named chars in the mini fixture (艾莉亚, 布利欧 — npc skipped, token skipped)
    assert summary["chars"] == 2
    # 艾莉亚 has a deterministic edge to mem_aria via her storyset
    assert summary["deterministic_chars_with_edges"] == 1
    # Both chars speak in act_test / main_01 → both have participant edges
    assert summary["participant_chars_with_edges"] == 2
    # No baked summaries in the mini fixture → no summary-source edges
    assert summary["summary_edge_count"] == 0
    assert summary["unresolved_summary_names"] == {}

    # Every index file written, including char_alias.json (raw-only mode
    # gets a name+appellation-only alias index so a later raw rebuild
    # cannot inherit a stale enriched file).
    for name in (
        "events_by_family",
        "char_to_events_deterministic",
        "char_to_events_participant",
        "char_to_events_summary",
        "event_to_chars",
        "stage_table",
        "char_table",
        "char_alias",
    ):
        assert paths.index_path(kb, name).is_file(), f"missing {name}"
    # In raw-only mode, the alias index only contains name + appellation
    # rows — no curated-only entries.
    alias = json.loads(paths.index_path(kb, "char_alias").read_text())
    assert set(alias["alias_to_char_ids"]) == {"艾莉亚", "Aria", "布利欧", "Brio"}


def test_raw_rebuild_overwrites_stale_curated_aliases(tmp_path, build_real_kb):
    """A prior enriched build wrote `空山` (a curated alias for 艾莉亚)
    into `char_alias.json`. A subsequent rebuild WITHOUT the curated
    file must overwrite the index; otherwise raw-only resolution
    silently leaks the curated alias."""
    from libs.kb import query

    kb_root = tmp_path / "kb"
    build_real_kb(kb_root, curated={"艾莉亚": ["空山"]})
    kb = query.load_kb(kb_root)
    assert isinstance(query.resolve_operator_name(kb, "空山"), query.Resolved)

    # Rebuild without curated; the prior 空山 entry must not survive.
    indexer.build_all_indexes(kb_root)
    kb2 = query.load_kb(kb_root)
    assert isinstance(query.resolve_operator_name(kb2, "空山"), query.Missing)


def test_build_all_indexes_with_curated_alias_file(tmp_path, build_real_kb):
    kb = build_real_kb(tmp_path / "kb", curated={"艾莉亚": ["Aria_alt", "空山"]})
    alias_idx = json.loads(paths.index_path(kb, "char_alias").read_text())
    # 艾莉亚 maps via name; the curated aliases attach to char_test_001
    assert alias_idx["alias_to_char_ids"]["艾莉亚"] == ["char_test_001"]
    assert alias_idx["alias_to_char_ids"]["空山"] == ["char_test_001"]
    assert alias_idx["alias_to_char_ids"]["Aria_alt"] == ["char_test_001"]


def test_build_all_indexes_subtracts_deterministic_in_event_to_chars(
    tmp_path, build_real_kb
):
    """艾莉亚 has a deterministic edge in mem_aria/0 and also speaks in
    that single stage; the per-(char,event,stage) subtraction means the
    merged event_to_chars row for mem_aria records her exactly once
    (deterministic), with no participant duplicate for the same stage."""
    kb = build_real_kb(tmp_path / "kb")
    e2c = json.loads(paths.index_path(kb, "event_to_chars").read_text())
    mem_rows = [r for r in e2c["mem_aria"] if r["char_id"] == "char_test_001"]
    assert len(mem_rows) == 1
    assert mem_rows[0]["source"] == "deterministic"
