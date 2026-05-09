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


# --- build_char_to_events_inferred ------------------------------------


def test_inferred_records_count_and_match_class(tmp_path, make_event, make_char):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "阿米娅说：我们出发吧。阿米娅笑了。")])
    make_char(kb, "char_amiya", name="阿米娅", appellation="Amiya")
    cm = indexer.load_char_manifests(kb)
    inf = indexer.build_char_to_events_inferred(kb, cm, {})
    rows = inf["char_amiya"]
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "ev1"
    assert row["stage_idx"] == 0
    assert row["count"] == 2  # `阿米娅` appears twice
    assert row["match_class"] == "canonical"


def test_inferred_canonical_short_for_single_char_operator(tmp_path, make_event, make_char):
    """Class A no-floor rule keeps single-zh-char operators recoverable.
    The downweight is encoded as `match_class=canonical_short`."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "陈走进门。")])
    make_char(kb, "char_chen", name="陈")
    cm = indexer.load_char_manifests(kb)
    inf = indexer.build_char_to_events_inferred(kb, cm, {})
    assert inf["char_chen"][0]["match_class"] == "canonical_short"


def test_inferred_picks_highest_precision_class_per_stage(tmp_path, make_event, make_char):
    """When a char's name (canonical) AND a curated alias (curated) both
    fire in the same stage, the row's `match_class` is the higher-
    precision one (`canonical`)."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "临光转身。玛嘉烈在前面。")])
    make_char(kb, "char_nearl", name="临光", appellation="Nearl")
    cm = indexer.load_char_manifests(kb)
    curated = {"临光": ["玛嘉烈"]}
    inf = indexer.build_char_to_events_inferred(kb, cm, {}, curated=curated)
    row = inf["char_nearl"][0]
    assert row["count"] == 2  # 临光 once, 玛嘉烈 once
    assert row["match_class"] == "canonical"  # canonical beats curated


def test_inferred_subtracts_when_deterministic_pair_exists(tmp_path, make_event, make_char):
    """If a deterministic edge already links `(char, event)`, no
    inferred rows for that char are emitted in that event — even if
    other stages of the same event mention the char."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "陈出现"), ("s1", "陈在另一幕")])
    make_event(kb, "ev2", [("s0", "陈也在这里")])
    make_char(
        kb,
        "char_chen",
        name="陈",
        storysets=[
            {
                "storySetName": "x",
                "storyTxt": "y",
                "linked_event_id": "ev1",
                "linked_stage_idx": 0,
            }
        ],
    )
    cm = indexer.load_char_manifests(kb)
    det = indexer.build_char_to_events_deterministic(kb, cm)
    inf = indexer.build_char_to_events_inferred(kb, cm, det)
    rows = inf["char_chen"]
    # ev1 entirely subtracted (incl. stage 1 even though stage 0 is what
    # was deterministically linked); ev2 emitted normally
    assert {r["event_id"] for r in rows} == {"ev2"}


def test_inferred_blocklist_drops_canonical_alias(tmp_path, make_event, make_char):
    """Even a canonical name is blocklisted if it overlaps with role
    nouns. Defensive against pathological char data."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "博士提议。")])
    make_char(kb, "char_dr", name="博士")
    cm = indexer.load_char_manifests(kb)
    inf = indexer.build_char_to_events_inferred(kb, cm, {})
    assert "char_dr" not in inf  # zero edges produced, key not added


def test_inferred_skips_curated_aliases_when_canonical_is_ambiguous(tmp_path, make_event, make_char):
    """`暮落` collides on two char_ids; the curated alias `沉渊` would
    auto-attach to one (arbitrary) or both (broadens scope) — neither
    is honest, so the inferred pass skips curated aliases for ambiguous
    canonicals."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "沉渊出现。")])
    make_char(kb, "char_a1", name="暮落")
    make_char(kb, "char_a2", name="暮落")
    cm = indexer.load_char_manifests(kb)
    curated = {"暮落": ["沉渊"]}
    inf = indexer.build_char_to_events_inferred(
        kb, cm, {}, curated=curated, ambiguous_canonicals={"暮落"}
    )
    # 暮落 itself classified canonical (not in body) → no rows.
    # 沉渊 (curated for ambiguous canonical) is skipped.
    assert "char_a1" not in inf
    assert "char_a2" not in inf


def test_inferred_dedupes_when_name_equals_appellation(tmp_path, make_event, make_char):
    """22 operators in the live corpus have `name == appellation`
    (`W`, `Sharp`, `Stormeye`, ...). Without per-char text dedup, a
    single mention would be counted twice (once per alias source)."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "W走出帐篷。")])
    make_char(kb, "char_w", name="W", appellation="W")
    cm = indexer.load_char_manifests(kb)
    inf = indexer.build_char_to_events_inferred(kb, cm, {})
    assert inf["char_w"][0]["count"] == 1


def test_inferred_dedupe_keeps_highest_precision_class(tmp_path, make_event, make_char):
    """If the same text appears as both a canonical name and a curated
    alias of the same char, the canonical class wins (precedence 4 vs 3)."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "凯尔希出现。")])
    make_char(kb, "char_kal", name="凯尔希", appellation="Kalts")
    cm = indexer.load_char_manifests(kb)
    # Curated map repeating the canonical text would normally never
    # happen (parser puts canonical as key, not value), but we feed it
    # in explicitly to verify the precedence rule.
    inf = indexer.build_char_to_events_inferred(
        kb, cm, {}, curated={"凯尔希": ["凯尔希"]}
    )
    row = inf["char_kal"][0]
    assert row["count"] == 1  # not 2 — text deduped
    assert row["match_class"] == "canonical"  # canonical beats curated


def test_inferred_returns_empty_for_chars_with_no_hits(tmp_path, make_event, make_char):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "无人出现。")])
    make_char(kb, "char_x", name="阿米娅")
    cm = indexer.load_char_manifests(kb)
    inf = indexer.build_char_to_events_inferred(kb, cm, {})
    assert inf == {}


# --- build_event_to_chars --------------------------------------------


def test_event_to_chars_flat_one_row_per_stage():
    det = {
        "char_a": [{"event_id": "ev1", "stage_idx": 2, "story_set_name": "ss"}],
    }
    inf = {
        "char_b": [
            {"event_id": "ev1", "stage_idx": 0, "count": 5, "match_class": "canonical"},
            {"event_id": "ev1", "stage_idx": 3, "count": 9, "match_class": "canonical"},
        ],
    }
    out = indexer.build_event_to_chars(det, inf)
    rows = out["ev1"]
    assert len(rows) == 3  # 1 deterministic + 2 inferred
    # Deterministic row carries story_set_name, no count/match_class
    det_row = next(r for r in rows if r["source"] == "deterministic")
    assert det_row["char_id"] == "char_a"
    assert det_row["stage_idx"] == 2
    assert det_row["story_set_name"] == "ss"
    assert "count" not in det_row
    # Inferred rows carry count + match_class, no story_set_name
    inf_rows = [r for r in rows if r["source"] == "inferred"]
    assert {r["stage_idx"] for r in inf_rows} == {0, 3}
    for r in inf_rows:
        assert r["count"] > 0
        assert r["match_class"] == "canonical"
        assert "story_set_name" not in r


def test_event_to_chars_sorted_by_char_then_stage():
    det = {
        "char_b": [{"event_id": "ev1", "stage_idx": 1, "story_set_name": "x"}],
        "char_a": [{"event_id": "ev1", "stage_idx": 0, "story_set_name": "y"}],
    }
    out = indexer.build_event_to_chars(det, {})
    assert [r["char_id"] for r in out["ev1"]] == ["char_a", "char_b"]


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


def test_char_table_marks_inferred_appearances():
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
    inf = {"char_a": [{"event_id": "ev1", "stage_idx": 0, "count": 1, "match_class": "canonical"}]}
    rows = indexer.build_char_table(cm, inf)
    rows_by_id = {r["char_id"]: r for r in rows}
    assert rows_by_id["char_a"]["has_inferred_appearances"] is True
    assert rows_by_id["char_b"]["has_inferred_appearances"] is False


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
    # Both chars appear in act_test / main_01 → both have inferred edges
    assert summary["inferred_chars_with_edges"] == 2

    # Every index file written, including char_alias.json (raw-only mode
    # gets a name+appellation-only alias index so a later raw rebuild
    # cannot inherit a stale enriched file).
    for name in (
        "events_by_family",
        "char_to_events_deterministic",
        "char_to_events_inferred",
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
    """艾莉亚 has a deterministic edge in mem_aria/0; her name appears in
    that stage's body too. The merged event_to_chars row for mem_aria
    should record her exactly once (deterministic), with no inferred
    duplicate."""
    kb = build_real_kb(tmp_path / "kb")
    e2c = json.loads(paths.index_path(kb, "event_to_chars").read_text())
    mem_rows = [r for r in e2c["mem_aria"] if r["char_id"] == "char_test_001"]
    assert len(mem_rows) == 1
    assert mem_rows[0]["source"] == "deterministic"
