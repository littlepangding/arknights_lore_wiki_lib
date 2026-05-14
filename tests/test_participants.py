"""Tests for `libs.kb.participants` — WS-0 tiered char↔stage edges.

Unit-level coverage of speaker-line parsing, alias match modes, the
word-boundary ASCII counter, and the `<关键人物>` summary parser; then
`build_stage_participants` against hand-built synthetic KBs (full control
over speaker / narration / single-CJK-noise / subtraction scenarios),
plus an integration check through `query.event_chars` for the TC-4
invariant ("年 must not surface as a participant just because the prose
says 今年").
"""

from __future__ import annotations

import json

import pytest

from libs import game_data
from libs.kb import indexer, participants, paths, query


# --- speaker-line extraction -----------------------------------------


def test_extract_speaker_names_basic_and_counts():
    text = "阿米娅:出发吧。\n旁白:风起。\n阿米娅:还有一句。\n他离开了。"
    assert participants.extract_speaker_names(text) == {"阿米娅": 2, "旁白": 1}


def test_extract_speaker_names_fullwidth_colon():
    assert participants.extract_speaker_names("陈：今天的事。") == {"陈": 1}


def test_extract_speaker_names_ignores_chunk_wrapper_lines():
    text = "<章节>\n<活动名称>测试</活动名称>\n<正文>\n阿米娅:台词\n</正文>\n</章节>"
    assert participants.extract_speaker_names(text) == {"阿米娅": 1}


def test_extract_speaker_names_handles_multiline_directive_after_clean_script():
    """`[multiline(name="X")]Y` is collapsed to `X:Y` by clean_script
    *before* chunking, so the same parser recognizes it — no special
    case needed in WS-0."""
    cleaned = game_data.clean_script('[multiline(name="阿米娅")]这是一段较长的台词。')
    assert cleaned.startswith("阿米娅:")
    assert participants.extract_speaker_names(cleaned) == {"阿米娅": 1}


# --- alias match mode + ASCII word boundary --------------------------


def test_alias_mode_classification():
    assert participants.alias_mode("W") == "ascii"
    assert participants.alias_mode("Pith") == "ascii"
    assert participants.alias_mode("THRM-EX") == "ascii"
    assert participants.alias_mode("年") == "cjk_single"
    assert participants.alias_mode("阿米娅") == "cjk_multi"


def test_ascii_boundary_excludes_substring_of_word():
    rx = participants._compile_alias("W")
    assert participants._count_in_body("World的世界没有人。", "W", rx) == 0


def test_ascii_boundary_includes_name_before_cjk():
    rx = participants._compile_alias("W")
    assert participants._count_in_body("W走出帐篷，又见到W。", "W", rx) == 2


def test_ascii_boundary_hyphenated_name():
    rx = participants._compile_alias("THRM-EX")
    assert participants._count_in_body("THRM-EX走来。后来THRM-EX离开。", "THRM-EX", rx) == 2
    # but THRM-EX25 (digit suffix) is a different token
    assert participants._count_in_body("THRM-EX25型号。", "THRM-EX", rx) == 0


def test_cjk_alias_uses_plain_count():
    rx = participants._compile_alias("年")
    assert rx is None
    assert participants._count_in_body("今年的年终总结。", "年", None) == 2


# --- build_stage_participants ----------------------------------------


def test_participants_speaker_tier(tmp_path, make_event, make_char):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "阿米娅:出发吧。\n旁白:风起。")])
    make_char(kb, "char_amiya", name="阿米娅", appellation="Amiya")
    cm = indexer.load_char_manifests(kb)
    out = participants.build_stage_participants(kb, cm, {})
    assert out["char_amiya"] == [
        {
            "event_id": "ev1",
            "stage_idx": 0,
            "source": "participant",
            "tier": "speaker",
            "spoke_lines": 1,
            "mention_count": 1,
            "matched_aliases": ["阿米娅"],
        }
    ]


def test_participants_named_tier_for_multichar_canonical_in_narration(
    tmp_path, make_event, make_char
):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "凯尔希走进会议室。她没有说话。")])
    make_char(kb, "char_kal", name="凯尔希")
    cm = indexer.load_char_manifests(kb)
    row = participants.build_stage_participants(kb, cm, {})["char_kal"][0]
    assert row["tier"] == "named"
    assert row["spoke_lines"] == 0
    assert row["mention_count"] == 1


def test_participants_lone_single_cjk_hit_is_only_mentioned(
    tmp_path, make_event, make_char
):
    """The TC-4 noise case: prose says `今年` once, the char never
    speaks → `mentioned`, never `named`."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "今年的故事就此展开。")])
    make_char(kb, "char_nian", name="年")
    cm = indexer.load_char_manifests(kb)
    row = participants.build_stage_participants(kb, cm, {})["char_nian"][0]
    assert row["tier"] == "mentioned"
    assert row["mention_count"] == 1


def test_participants_single_cjk_promoted_to_named_at_count_two(
    tmp_path, make_event, make_char
):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "今年是年的转折。年走进了风暴。")])
    make_char(kb, "char_nian", name="年")
    cm = indexer.load_char_manifests(kb)
    row = participants.build_stage_participants(kb, cm, {})["char_nian"][0]
    assert row["tier"] == "named"
    assert row["mention_count"] >= 2


def test_participants_single_cjk_promoted_by_summary_hit(
    tmp_path, make_event, make_char
):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "今年的故事就此展开。")])
    make_char(kb, "char_nian", name="年")
    cm = indexer.load_char_manifests(kb)
    out = participants.build_stage_participants(
        kb, cm, {}, summary_char_ids_by_event={"ev1": frozenset({"char_nian"})}
    )
    assert out["char_nian"][0]["tier"] == "named"


def test_participants_ascii_boundary_both_directions(tmp_path, make_event, make_char):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "World很大，但里面空无一人。")])
    make_event(kb, "ev2", [("s0", "W走出帐篷。")])
    make_char(kb, "char_w", name="W")
    cm = indexer.load_char_manifests(kb)
    out = participants.build_stage_participants(kb, cm, {})
    # `World` ⊅ `W` → no ev1 edge; `W走` → ev2 edge, ASCII canonical → named.
    assert [(r["event_id"], r["tier"]) for r in out["char_w"]] == [("ev2", "named")]


def test_participants_dedupes_name_equals_appellation(tmp_path, make_event, make_char):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "W走出帐篷。")])
    make_char(kb, "char_w", name="W", appellation="W")
    cm = indexer.load_char_manifests(kb)
    out = participants.build_stage_participants(kb, cm, {})
    assert out["char_w"][0]["mention_count"] == 1  # not 2 — alias text deduped


def test_participants_deterministic_subtraction_is_per_stage(
    tmp_path, make_event, make_char
):
    """A deterministic storyset edge in `(ev1, stage 0)` suppresses the
    participant edge for *that stage only* — `ev1/1` and `ev2/0` still
    surface (unlike the pre-WS-0 event-level subtraction)."""
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "陈出现在第一幕。"), ("s1", "陈也在第二幕里出现。")])
    make_event(kb, "ev2", [("s0", "陈在另一个活动里出现。")])
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
    out = participants.build_stage_participants(kb, cm, det)
    assert {(r["event_id"], r["stage_idx"]) for r in out["char_chen"]} == {
        ("ev1", 1),
        ("ev2", 0),
    }


def test_participants_blocklist_drops_canonical_alias(tmp_path, make_event, make_char):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "博士提出建议。")])
    make_char(kb, "char_dr", name="博士")
    cm = indexer.load_char_manifests(kb)
    assert "char_dr" not in participants.build_stage_participants(kb, cm, {})


def test_participants_skips_curated_alias_for_ambiguous_canonical(
    tmp_path, make_event, make_char
):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "沉渊在远处徘徊。")])
    make_char(kb, "char_a1", name="暮落")
    make_char(kb, "char_a2", name="暮落")
    cm = indexer.load_char_manifests(kb)
    out = participants.build_stage_participants(
        kb, cm, {}, curated={"暮落": ["沉渊"]}, ambiguous_canonicals={"暮落"}
    )
    # Had the curated `沉渊` been attached, both chars would get an edge;
    # the ambiguous-canonical skip means neither does.
    assert "char_a1" not in out and "char_a2" not in out


def test_participants_empty_when_no_hits(tmp_path, make_event, make_char):
    kb = tmp_path / "kb"
    make_event(kb, "ev1", [("s0", "无人出场。")])
    make_char(kb, "char_x", name="阿米娅")
    cm = indexer.load_char_manifests(kb)
    assert participants.build_stage_participants(kb, cm, {}) == {}


# --- TC-4 through the query layer ------------------------------------


def test_tc4_today_not_a_participant_of_an_event_that_says_jinnian(
    tmp_path, make_event, make_char
):
    kb = tmp_path / "kb"
    make_event(kb, "ev_today", [("s0", "今年的故事就此展开。\n阿米娅:我们出发。")])
    make_char(kb, "char_nian", name="年")
    make_char(kb, "char_amiya", name="阿米娅", appellation="Amiya")
    indexer.build_all_indexes(kb)
    kb_loaded = query.load_kb(kb)

    named = {a.char_id for a in query.event_chars(kb_loaded, "ev_today")}
    assert "char_amiya" in named  # speaker → clears the default min-tier
    assert "char_nian" not in named  # the TC-4 invariant

    # still recoverable at the recall floor
    floor = {
        a.char_id
        for a in query.event_chars(kb_loaded, "ev_today", min_tier="mentioned")
    }
    assert "char_nian" in floor


# --- <关键人物> summary parsing + summary-edge builder -----------------


def test_parse_key_chars_dequotes_and_dedupes():
    md = (
        "---\nevent_id: ev1\n---\n\n<关键人物>\n"
        '格拉尼;可萝尔;"上尉";凯尔希;格拉尼\n</关键人物>\n<场景标签>\n卡西米尔\n</场景标签>\n'
    )
    assert participants.parse_key_chars(md) == ["格拉尼", "可萝尔", "上尉", "凯尔希"]


def test_parse_key_chars_fullwidth_semicolon():
    assert participants.parse_key_chars("<关键人物>\nA；B；A\n</关键人物>") == ["A", "B"]


def test_parse_key_chars_missing_tag():
    assert participants.parse_key_chars("no tag at all") == []


def test_build_char_to_events_summary_resolves_drops_and_reports(tmp_path):
    events_dir = tmp_path / "kb_summaries" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "ev1.md").write_text(
        "<关键人物>\n阿米娅;凯尔希;神农\n</关键人物>\n", encoding="utf-8"
    )
    alias_to_char_ids = {
        "阿米娅": ["char_amiya"],
        "凯尔希": ["char_kal", "char_other"],  # ambiguous → dropped, not "unresolved"
    }
    edges, unresolved = participants.build_char_to_events_summary(
        tmp_path / "kb_summaries", alias_to_char_ids, {}
    )
    assert edges == {
        "char_amiya": [
            {
                "event_id": "ev1",
                "stage_idx": None,
                "source": "summary",
                "tier": "named",
                "matched_aliases": ["阿米娅"],
            }
        ]
    }
    # 神农 has no operator backing → reported; 凯尔希 is ambiguous → silently skipped.
    assert unresolved == {"ev1": ["神农"]}


def test_build_char_to_events_summary_subtracts_deterministic(tmp_path):
    events_dir = tmp_path / "kb_summaries" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "ev1.md").write_text("<关键人物>\n阿米娅\n</关键人物>\n", encoding="utf-8")
    deterministic = {
        "char_amiya": [{"event_id": "ev1", "stage_idx": 0, "story_set_name": "x"}]
    }
    edges, unresolved = participants.build_char_to_events_summary(
        tmp_path / "kb_summaries", {"阿米娅": ["char_amiya"]}, deterministic
    )
    assert edges == {}  # deterministic edge already covers (char_amiya, ev1)
    assert unresolved == {}


def test_build_char_to_events_summary_no_summaries_dir(tmp_path):
    assert participants.build_char_to_events_summary(None, {}, {}) == ({}, {})
    assert participants.build_char_to_events_summary(tmp_path / "nope", {}, {}) == ({}, {})


def _write_stage_summary(summaries_root, eid, sidx, key_chars: str) -> None:
    p = paths.stage_summary_path(summaries_root, eid, sidx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"<关键人物>\n{key_chars}\n</关键人物>\n", encoding="utf-8")


def _srow(eid, sidx, names):
    return {
        "event_id": eid, "stage_idx": sidx, "source": "summary",
        "tier": "named", "matched_aliases": names,
    }


def test_build_char_to_events_summary_stage_scoped(tmp_path):
    sr = tmp_path / "kb_summaries"
    _write_stage_summary(sr, "ev1", 0, "阿米娅;陈")
    _write_stage_summary(sr, "ev1", 2, "陈")
    alias_to_char_ids = {"阿米娅": ["char_amiya"], "陈": ["char_chen"]}
    edges, unresolved = participants.build_char_to_events_summary(sr, alias_to_char_ids, {})
    assert edges["char_amiya"] == [_srow("ev1", 0, ["阿米娅"])]
    assert edges["char_chen"] == [_srow("ev1", 0, ["陈"]), _srow("ev1", 2, ["陈"])]
    assert unresolved == {}


def test_build_char_to_events_summary_stage_subsumes_event_scoped(tmp_path):
    sr = tmp_path / "kb_summaries"
    (sr / "events").mkdir(parents=True)
    (sr / "events" / "ev1.md").write_text("<关键人物>\n阿米娅;陈\n</关键人物>\n", encoding="utf-8")
    _write_stage_summary(sr, "ev1", 1, "阿米娅")  # 阿米娅 baked at stage level for ev1
    edges, _ = participants.build_char_to_events_summary(
        sr, {"阿米娅": ["char_amiya"], "陈": ["char_chen"]}, {}
    )
    # 阿米娅: only the stage-scoped row (the event-scoped one is subsumed)
    assert edges["char_amiya"] == [_srow("ev1", 1, ["阿米娅"])]
    # 陈: no stage summary mentions 陈, so it keeps its event-scoped edge
    assert edges["char_chen"] == [_srow("ev1", None, ["陈"])]


def test_build_char_to_events_summary_stage_subtracts_deterministic(tmp_path):
    sr = tmp_path / "kb_summaries"
    _write_stage_summary(sr, "ev1", 0, "阿米娅")
    _write_stage_summary(sr, "ev1", 1, "阿米娅")
    deterministic = {"char_amiya": [{"event_id": "ev1", "stage_idx": 0, "story_set_name": "x"}]}
    edges, _ = participants.build_char_to_events_summary(sr, {"阿米娅": ["char_amiya"]}, deterministic)
    # stage 0 is already a deterministic edge → only stage 1 survives
    assert edges["char_amiya"] == [_srow("ev1", 1, ["阿米娅"])]


def test_build_char_to_events_summary_unresolved_merged_across_layers(tmp_path):
    sr = tmp_path / "kb_summaries"
    (sr / "events").mkdir(parents=True)
    (sr / "events" / "ev1.md").write_text("<关键人物>\n神农\n</关键人物>\n", encoding="utf-8")
    _write_stage_summary(sr, "ev1", 0, "神农;颉")  # 神农 also unresolved here; 颉 unresolved
    edges, unresolved = participants.build_char_to_events_summary(sr, {}, {})
    assert edges == {}
    assert unresolved == {"ev1": ["神农", "颉"]}  # deduped + sorted


def test_summary_char_ids_by_event_includes_stage_scoped(tmp_path):
    sr = tmp_path / "kb_summaries"
    _write_stage_summary(sr, "ev1", 3, "陈")
    edges, _ = participants.build_char_to_events_summary(sr, {"陈": ["char_chen"]}, {})
    assert participants.summary_char_ids_by_event(edges) == {"ev1": frozenset({"char_chen"})}


def test_summary_char_ids_by_event_inversion():
    edges = {
        "char_a": [{"event_id": "ev1", "stage_idx": None, "source": "summary"}],
        "char_b": [
            {"event_id": "ev1", "stage_idx": None, "source": "summary"},
            {"event_id": "ev2", "stage_idx": None, "source": "summary"},
        ],
    }
    out = participants.summary_char_ids_by_event(edges)
    assert out == {"ev1": frozenset({"char_a", "char_b"}), "ev2": frozenset({"char_b"})}
