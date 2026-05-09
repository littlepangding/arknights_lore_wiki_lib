"""Unit + small-integration tests for `libs.kb.chunker`. Most assertions are
on pure formatters; the `write_event` / `write_char` cases drive the whole
pipeline against `tests/fixtures/mini_gamedata/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from libs import game_data
from libs.kb import chunker, paths


# --- format_stage_chunk -----------------------------------------------


def test_format_stage_chunk_includes_all_frontmatter_tags():
    stage = {"name": "测试一", "avgTag": "行动前", "storyInfoTxt": "前情提要"}
    out = chunker.format_stage_chunk("act_test", "测试活动", 0, stage, "正文内容")
    # Frontmatter shape — every tag present, ordered as per design
    assert out.startswith("<章节>\n<活动名称>测试活动</活动名称>\n<活动ID>act_test</活动ID>\n")
    assert "<章节序号>00</章节序号>" in out
    assert "<章节名称>测试一（行动前）</章节名称>" in out  # avgTag appended
    assert "<章节简介>前情提要</章节简介>" in out
    assert "<正文>\n正文内容\n</正文>" in out
    assert out.rstrip().endswith("</章节>")


def test_format_stage_chunk_omits_avgtag_when_absent():
    stage = {"name": "起点", "avgTag": None, "storyInfoTxt": ""}
    out = chunker.format_stage_chunk("main_01", "主线", 1, stage, "x")
    # No 「（avgTag）」 suffix in chapter heading when avgTag is None/empty
    assert "<章节名称>起点</章节名称>" in out
    # 章节简介 is empty but the tag is still emitted (downstream regex friendly)
    assert "<章节简介></章节简介>" in out


def test_format_stage_chunk_zero_pads_idx():
    stage = {"name": "x", "avgTag": None, "storyInfoTxt": ""}
    out = chunker.format_stage_chunk("e", "n", 7, stage, "body")
    assert "<章节序号>07</章节序号>" in out
    out2 = chunker.format_stage_chunk("e", "n", 12, stage, "body")
    assert "<章节序号>12</章节序号>" in out2


# --- per-section formatters --------------------------------------------


def test_format_profile_only_when_populated():
    assert chunker.format_profile({"itemUsage": "u", "itemDesc": "d"}).startswith("<干员招聘文本>")
    # one-of-two is enough
    assert "u" in chunker.format_profile({"itemUsage": "u"})
    # neither -> None
    assert chunker.format_profile({}) is None
    assert chunker.format_profile({"itemUsage": "", "itemDesc": ""}) is None


def test_format_profile_appends_national_id_when_present():
    """DESIGN.md "On-disk layout" makes profile.txt = recruitment text + nationId
    so an agent answering a basic origin question doesn't need a second lookup."""
    out = chunker.format_profile({"itemUsage": "u", "itemDesc": "d", "nationId": "rhodes"})
    assert "<国家>rhodes</国家>" in out
    # No nationId line when the field is absent or empty
    out = chunker.format_profile({"itemUsage": "u"})
    assert "<国家>" not in out
    out = chunker.format_profile({"itemUsage": "u", "nationId": ""})
    assert "<国家>" not in out


def test_format_voice_joins_words_or_returns_none():
    out = chunker.format_voice({"words": ["a", "b"]})
    assert out is not None and "a\nb" in out and out.startswith("<干员语音>")
    assert chunker.format_voice({"words": []}) is None
    assert chunker.format_voice({}) is None


def test_format_archive_joins_titled_stories():
    out = chunker.format_archive({"stories": {"基础档案": "代号x"}})
    assert out is not None
    assert "基础档案:" in out and "代号x" in out
    assert chunker.format_archive({}) is None


def test_format_skins_drops_entries_with_none_description():
    skins = [
        {"skinName": "夏日", "dialog": "d", "usage": "u", "description": "在海边"},
        {"skinName": "标准", "dialog": "x", "usage": "y", "description": None},
    ]
    out = chunker.format_skins({"skins": skins})
    assert out is not None
    assert "夏日" in out
    assert "标准" not in out  # filtered (description is None)


def test_format_skins_returns_none_when_all_descriptions_null():
    skins = [{"skinName": "a", "dialog": "", "usage": "", "description": None}]
    assert chunker.format_skins({"skins": skins}) is None


def test_format_modules_drops_entries_with_none_desc():
    mods = [
        {"uniEquipName": "A", "uniEquipDesc": "提升效率"},
        {"uniEquipName": "B", "uniEquipDesc": None},
    ]
    out = chunker.format_modules({"uniequip": mods})
    assert out is not None
    assert "A" in out and "B" not in out


def test_extract_section_texts_returns_only_populated():
    char = {"itemUsage": "u", "itemDesc": "d"}  # only profile populated
    sec = chunker.extract_section_texts(char)
    assert list(sec.keys()) == ["profile"]


# --- storyset linking --------------------------------------------------


def test_build_storytxt_index_groups_multi_hits():
    sr = {
        "e1": {"stages": [{"storyTxt": "a"}, {"storyTxt": "b"}]},
        "e2": {"stages": [{"storyTxt": "a"}]},  # duplicates 'a'
    }
    idx = chunker.build_storytxt_index(sr)
    assert idx["a"] == [("e1", 0), ("e2", 0)]
    assert idx["b"] == [("e1", 1)]


def test_resolve_storysets_links_unique_match():
    char = {"storysets": [{"storySetName": "故乡", "storyTxt": "obt/memory/aria_1"}]}
    idx = {"obt/memory/aria_1": [("mem_aria", 0)]}
    linked, warnings = chunker.resolve_storysets(char, idx)
    assert warnings == []
    assert linked == [
        {
            "storySetName": "故乡",
            "storyTxt": "obt/memory/aria_1",
            "linked_event_id": "mem_aria",
            "linked_stage_idx": 0,
        }
    ]


def test_resolve_storysets_warns_on_missing():
    char = {"storysets": [{"storySetName": "nope", "storyTxt": "obt/memory/missing"}]}
    linked, warnings = chunker.resolve_storysets(char, {})
    assert linked == []
    assert len(warnings) == 1 and warnings[0]["reason"] == "missing"


def test_resolve_storysets_warns_on_ambiguous():
    char = {"storysets": [{"storySetName": "amb", "storyTxt": "x"}]}
    idx = {"x": [("e1", 0), ("e2", 1)]}
    linked, warnings = chunker.resolve_storysets(char, idx)
    assert linked == []
    assert warnings[0]["reason"] == "ambiguous"
    assert warnings[0]["candidates"] == [
        {"event_id": "e1", "stage_idx": 0},
        {"event_id": "e2", "stage_idx": 1},
    ]


def test_resolve_storysets_handles_no_storysets_key():
    linked, warnings = chunker.resolve_storysets({}, {})
    assert linked == [] and warnings == []


# --- alias computation -------------------------------------------------


def test_compute_aliases_raw_mode_dedupes():
    out = chunker.compute_char_aliases({"name": "艾莉亚", "appellation": "Aria"})
    assert out == ["艾莉亚", "Aria"]


def test_compute_aliases_raw_mode_drops_empty_and_dupes():
    out = chunker.compute_char_aliases({"name": "X", "appellation": "X"})
    assert out == ["X"]
    out = chunker.compute_char_aliases({"name": "X", "appellation": ""})
    assert out == ["X"]


def test_compute_aliases_enriched_mode_appends_curated():
    char = {"name": "临光", "appellation": "Nearl"}
    curated = {"临光": ["玛嘉烈"]}
    out = chunker.compute_char_aliases(char, curated=curated)
    assert out == ["临光", "Nearl", "玛嘉烈"]


def test_compute_aliases_enriched_mode_skips_ambiguous_canonical():
    """If `name` collides on multiple operators, curated aliases must NOT be
    auto-attached — they go to a separate ambiguous_aliases bucket (handled
    by indexer.py)."""
    char = {"name": "暮落", "appellation": "Aprot"}
    curated = {"暮落": ["沉渊"]}
    out = chunker.compute_char_aliases(
        char, curated=curated, ambiguous_canonicals={"暮落"}
    )
    assert "沉渊" not in out
    assert out == ["暮落", "Aprot"]


# --- write_event integration via fixture ------------------------------


def test_write_event_against_mini_fixture(tmp_path, mini_gamedata_path):
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    kb = tmp_path / "kb"
    manifest = chunker.write_event(
        kb, mini_gamedata_path, "act_test", sr["act_test"], "test-vN"
    )
    # Manifest shape
    assert manifest["event_id"] == "act_test"
    assert manifest["entryType"] == "ACTIVITY"
    assert manifest["source_family"] == "activity"
    assert manifest["storyTxt_prefixes"] == ["activities/test"]
    assert manifest["source_data_version"] == "test-vN"
    assert len(manifest["stages"]) == 3
    # Stage files exist
    event_dir = paths.event_dir(kb, "act_test")
    for s in manifest["stages"]:
        assert (event_dir / s["file"]).is_file()
    # event.json round-trips
    with open(paths.event_json_path(kb, "act_test"), encoding="utf-8") as f:
        roundtrip = json.load(f)
    assert roundtrip == manifest


def test_collect_storytxt_prefixes_pure():
    # Single-prefix event
    stages = [{"storyTxt": "activities/test/a"}, {"storyTxt": "activities/test/b"}]
    assert chunker.collect_storytxt_prefixes(stages) == ["activities/test"]
    # Mixed-prefix event (mirrors real act3d0 / main_0): output is the deduped
    # sorted set so callers can't miss the second subtree.
    stages = [
        {"storyTxt": "obt/guide/beg/0"},
        {"storyTxt": "obt/main/level_main_01_01"},
        {"storyTxt": "obt/main/level_main_01_02"},
    ]
    assert chunker.collect_storytxt_prefixes(stages) == ["obt/guide", "obt/main"]
    # Empty / missing storyTxt -> dropped (no `""` entry leaking into the list)
    assert chunker.collect_storytxt_prefixes([]) == []
    assert chunker.collect_storytxt_prefixes([{"storyTxt": ""}]) == []


def test_write_event_records_all_prefixes_for_mixed_events(
    tmp_path, mini_gamedata_path, monkeypatch
):
    """Reproduces the live `main_0` / `act3d0` shape: a single event whose
    stages live under two distinct prefixes. The event manifest must surface
    both, otherwise consumers indexing on `storyTxt_prefixes` miss part of
    the event's text. Monkeypatched script reader avoids needing extra
    fixture files for the synthetic second prefix."""
    fake_event = {
        "name": "mixed prefix event",
        "entryType": "MAINLINE",
        "stages": [
            {"name": "序章", "avgTag": "幕间", "storyInfoTxt": "", "storyTxt": "obt/guide/beg/0_welcome"},
            {"name": "起点", "avgTag": None, "storyInfoTxt": "", "storyTxt": "obt/main/level_main_01_01"},
        ],
    }
    monkeypatch.setattr(
        game_data, "get_raw_story_txt", lambda gp, st: f"<<{st}>>"
    )
    kb = tmp_path / "kb"
    manifest = chunker.write_event(kb, "/unused", "mixed_evt", fake_event, "v")
    assert manifest["storyTxt_prefixes"] == ["obt/guide", "obt/main"]
    # source_family still derived from entryType+first stage (MAINLINE wins).
    assert manifest["source_family"] == "mainline"


def test_write_event_picks_correct_family_for_each_event(tmp_path, mini_gamedata_path):
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    kb = tmp_path / "kb"
    families = {}
    for eid, ev in sr.items():
        m = chunker.write_event(kb, mini_gamedata_path, eid, ev, "v")
        families[eid] = m["source_family"]
    assert families == {
        "act_test": "activity",
        "main_01": "mainline",
        "mem_aria": "operator_record",
    }


def test_write_event_stage_chunk_contains_cleaned_dialogue(tmp_path, mini_gamedata_path):
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    kb = tmp_path / "kb"
    chunker.write_event(kb, mini_gamedata_path, "act_test", sr["act_test"], "v")
    # Stage 0 of act_test = level_test_01_beg, has Sticker text + Decision options
    stage_files = sorted(paths.event_dir(kb, "act_test").glob("stage_00_*.txt"))
    assert len(stage_files) == 1
    body = stage_files[0].read_text(encoding="utf-8")
    # clean_script keeps Sticker text -> 旁白:石碑铭文：群山为证
    assert "旁白:石碑铭文：群山为证" in body
    # {@nickname} -> 博士
    assert "早上好，博士。" in body
    # avgTag 行动前 in chapter heading
    assert "<章节名称>测试一（行动前）</章节名称>" in body


# --- write_char integration via fixture -------------------------------


def test_write_char_full(tmp_path, mini_gamedata_path):
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    ci, _ = game_data.get_all_char_info(mini_gamedata_path)
    idx = chunker.build_storytxt_index(sr)
    kb = tmp_path / "kb"
    manifest, warnings = chunker.write_char(kb, "char_test_001", ci["char_test_001"], idx)
    assert warnings == []
    assert manifest["char_id"] == "char_test_001"
    assert manifest["name"] == "艾莉亚"
    assert manifest["appellation"] == "Aria"
    assert manifest["aliases"] == ["艾莉亚", "Aria"]
    assert manifest["nationId"] == "testland"
    assert set(manifest["sections"]) == {"profile", "voice", "archive", "skins", "modules"}
    assert manifest["storyset_count"] == 1
    # Files on disk
    cdir = paths.char_dir(kb, "char_test_001")
    for sec in manifest["sections"]:
        assert (cdir / f"{sec}.txt").is_file()
    # storysets resolved
    storysets = json.loads(paths.char_storysets_path(kb, "char_test_001").read_text())
    assert storysets == [
        {
            "storySetName": "故乡的山",
            "storyTxt": "obt/memory/story_aria_1",
            "linked_event_id": "mem_aria",
            "linked_stage_idx": 0,
        }
    ]


def test_write_char_sparse_no_handbook(tmp_path, mini_gamedata_path):
    """char_test_002 has no handbook entry, no voice, no skins, no modules.
    Only profile should be populated; storysets.json is an empty list."""
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    ci, _ = game_data.get_all_char_info(mini_gamedata_path)
    idx = chunker.build_storytxt_index(sr)
    kb = tmp_path / "kb"
    manifest, warnings = chunker.write_char(kb, "char_test_002", ci["char_test_002"], idx)
    assert warnings == []
    assert manifest["sections"] == ["profile"]
    assert manifest["storyset_count"] == 0
    cdir = paths.char_dir(kb, "char_test_002")
    assert (cdir / "profile.txt").is_file()
    assert not (cdir / "voice.txt").exists()
    assert not (cdir / "archive.txt").exists()
    # storysets.json still written, but empty
    assert json.loads(paths.char_storysets_path(kb, "char_test_002").read_text()) == []


def test_write_char_rejects_nameless(tmp_path, mini_gamedata_path):
    """The 5 nameless `npc_*` records must not reach `write_char`. Build pipe
    skips them; this `ValueError` is the safety net per design."""
    ci, _ = game_data.get_all_char_info(mini_gamedata_path)
    nameless = ci["npc_test_npc"]
    assert not nameless.get("name")
    with pytest.raises(ValueError, match="nameless"):
        chunker.write_char(tmp_path / "kb", "npc_test_npc", nameless, {})


def test_write_event_prunes_stale_stage_files(tmp_path, mini_gamedata_path):
    """Rewriting an event with fewer stages must not leave the old chunks
    on disk — they would otherwise leak into grep / filesystem scans."""
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    kb = tmp_path / "kb"
    chunker.write_event(kb, mini_gamedata_path, "act_test", sr["act_test"], "v1")
    full_files = sorted(p.name for p in paths.event_dir(kb, "act_test").glob("stage_*.txt"))
    assert len(full_files) == 3

    # Rewrite with a single stage. Use the existing first stage so the raw
    # script lookup still succeeds.
    trimmed = dict(sr["act_test"])
    trimmed["stages"] = [trimmed["stages"][0]]
    manifest = chunker.write_event(kb, mini_gamedata_path, "act_test", trimmed, "v2")
    after = sorted(p.name for p in paths.event_dir(kb, "act_test").glob("stage_*.txt"))
    assert len(after) == 1
    assert after[0] == manifest["stages"][0]["file"]


def test_write_event_keeps_unrelated_files(tmp_path, mini_gamedata_path):
    """The prune step targets `stage_*.txt` only — sibling artifacts (e.g.
    a future audit-report json a caller drops in the dir) should survive."""
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    kb = tmp_path / "kb"
    chunker.write_event(kb, mini_gamedata_path, "act_test", sr["act_test"], "v1")
    sentinel = paths.event_dir(kb, "act_test") / "audit_report.json"
    sentinel.write_text("{}", encoding="utf-8")
    chunker.write_event(kb, mini_gamedata_path, "act_test", sr["act_test"], "v2")
    assert sentinel.exists()


def test_write_char_prunes_stale_section_files(tmp_path, mini_gamedata_path):
    """Section files must match `manifest.sections` exactly — a section that
    disappears between builds cannot leave its old file behind."""
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    ci, _ = game_data.get_all_char_info(mini_gamedata_path)
    idx = chunker.build_storytxt_index(sr)
    kb = tmp_path / "kb"
    full = ci["char_test_001"]
    chunker.write_char(kb, "char_test_001", full, idx)
    cdir = paths.char_dir(kb, "char_test_001")
    assert (cdir / "voice.txt").exists()
    assert (cdir / "skins.txt").exists()
    assert (cdir / "modules.txt").exists()

    # Rewrite as a profile-only char (drop voice / archive / skins / modules).
    sparse = {k: v for k, v in full.items() if k in ("name", "appellation", "itemUsage", "itemDesc", "nationId")}
    manifest, _ = chunker.write_char(kb, "char_test_001", sparse, idx)
    assert manifest["sections"] == ["profile"]
    assert (cdir / "profile.txt").exists()
    for gone in ("voice.txt", "archive.txt", "skins.txt", "modules.txt"):
        assert not (cdir / gone).exists(), f"{gone} should have been pruned"


def test_write_char_atomic_writes_use_replace(tmp_path, mini_gamedata_path):
    """Re-writing the same char shouldn't leave .tmp_* artifacts behind."""
    sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
    ci, _ = game_data.get_all_char_info(mini_gamedata_path)
    idx = chunker.build_storytxt_index(sr)
    kb = tmp_path / "kb"
    chunker.write_char(kb, "char_test_001", ci["char_test_001"], idx)
    chunker.write_char(kb, "char_test_001", ci["char_test_001"], idx)
    # No leftover tempfiles
    leftovers = list(paths.char_dir(kb, "char_test_001").glob(".tmp_*"))
    assert leftovers == []
