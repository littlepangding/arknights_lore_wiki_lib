"""Unit tests for `libs.kb.paths`. No I/O — every assertion is on pure
function output. The classifier is the load-bearing piece (DESIGN.md "Source
families"); the rest is path arithmetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.kb import paths


# --- source_family classifier ----------------------------------------


@pytest.mark.parametrize(
    "story_txt,entry_type,expected",
    [
        # mainline: entryType=MAINLINE is the strongest signal, regardless of prefix
        ("obt/main/level_main_01_01", "MAINLINE", "mainline"),
        ("obt/main/level_main_01_01", "NONE", "mainline"),
        # main_0 (the prologue): entryType=MAINLINE but first stage is in obt/guide/.
        # Without the entryType override this would land in `other`.
        ("obt/guide/beg/0_welcome_to_guide", "MAINLINE", "mainline"),
        # operator_record: obt/memory/* — was entryType=NONE in real data
        ("obt/memory/story_aria_1", "NONE", "operator_record"),
        ("obt/memory/story_x", "ACTIVITY", "operator_record"),  # prefix wins
        # activity vs mini_activity disambiguated by entryType when prefix is activities/
        ("activities/test/level_test_01_beg", "ACTIVITY", "activity"),
        ("activities/test/level_test_01_beg", "MINI_ACTIVITY", "mini_activity"),
        # activities/ with an unknown entryType -> other
        ("activities/test/level_test_01_beg", "NONE", "other"),
        # Unknown prefix -> other (safe fallback per design risk row)
        ("obt/guide/level_guide_01", "NONE", "other"),
        ("", "NONE", "other"),
        # Leading slash / backslash tolerance
        ("/obt/main/level_main_01_01", "MAINLINE", "mainline"),
        ("obt\\main\\level_main_01_01", "MAINLINE", "mainline"),
    ],
)
def test_source_family(story_txt, entry_type, expected):
    assert paths.source_family(story_txt, entry_type) == expected


def test_source_family_handles_none_input():
    assert paths.source_family(None, "ACTIVITY") == "other"


def test_families_constant_covers_all_outputs():
    seen = {
        paths.source_family("obt/main/x", "MAINLINE"),
        paths.source_family("obt/memory/x", "NONE"),
        paths.source_family("activities/x/y", "ACTIVITY"),
        paths.source_family("activities/x/y", "MINI_ACTIVITY"),
        paths.source_family("anywhere/else", "NONE"),
    }
    assert seen == set(paths.FAMILIES)


# --- story_txt_prefix --------------------------------------------------


@pytest.mark.parametrize(
    "story_txt,expected",
    [
        ("obt/main/level_main_01_01", "obt/main"),
        ("activities/act46side/level_a046_03_beg", "activities/act46side"),
        ("obt/memory/story_aria_1", "obt/memory"),
        ("/obt/main/x", "obt/main"),
        ("obt\\main\\x", "obt/main"),
        ("", ""),
    ],
)
def test_story_txt_prefix(story_txt, expected):
    assert paths.story_txt_prefix(story_txt) == expected


# --- safe_slug ---------------------------------------------------------


def test_safe_slug_passthrough_for_ascii():
    assert paths.safe_slug("level_test_01_beg") == "level_test_01_beg"
    assert paths.safe_slug("event42") == "event42"


def test_safe_slug_hashes_for_zh_or_punct():
    s = paths.safe_slug("测试一_行动前")
    # 6-char hex SHA prefix
    assert len(s) == 6
    assert all(c in "0123456789abcdef" for c in s)


def test_safe_slug_is_deterministic():
    assert paths.safe_slug("测试一_行动前") == paths.safe_slug("测试一_行动前")


# --- KB path helpers ---------------------------------------------------


def test_event_paths(tmp_path):
    kb = tmp_path / "kb"
    assert paths.event_dir(kb, "act_test") == kb / "events" / "act_test"
    assert paths.event_json_path(kb, "act_test") == kb / "events" / "act_test" / "event.json"


def test_stage_filename_zero_pads_and_uses_avg_tag():
    # ASCII name + avg_tag — stays as the literal slug
    assert paths.stage_filename(0, "level_test_01_beg", "行动前").startswith("stage_00_")
    # Single-digit idx is zero-padded to 2 digits
    assert paths.stage_filename(3, "abc", None) == "stage_03_abc.txt"
    # zh storyName collapses to a 6-char hash
    fn = paths.stage_filename(2, "测试一", "行动前")
    assert fn.startswith("stage_02_")
    assert fn.endswith(".txt")
    base = fn[len("stage_02_"):-len(".txt")]
    assert len(base) == 6
    # avg_tag changes the slug deterministically (avoids _beg/_end collisions)
    assert paths.stage_filename(0, "测试一", "行动前") != paths.stage_filename(0, "测试一", "行动后")


def test_stage_path(tmp_path):
    kb = tmp_path / "kb"
    p = paths.stage_path(kb, "act_test", 1, "x", None)
    assert p.parent == kb / "events" / "act_test"
    assert p.name == "stage_01_x.txt"


def test_char_paths(tmp_path):
    kb = tmp_path / "kb"
    assert paths.char_dir(kb, "char_test_001") == kb / "chars" / "char_test_001"
    assert paths.char_manifest_path(kb, "char_test_001").name == "manifest.json"
    assert paths.char_section_path(kb, "char_test_001", "voice").name == "voice.txt"
    assert paths.char_storysets_path(kb, "char_test_001").name == "storysets.json"


def test_index_path(tmp_path):
    kb = tmp_path / "kb"
    assert paths.index_path(kb, "events_by_family") == kb / "indexes" / "events_by_family.json"


def test_summary_paths(tmp_path):
    sr = tmp_path / "kb_summaries"
    assert paths.event_summary_path(sr, "act_test") == sr / "events" / "act_test.md"
    assert paths.summaries_manifest_path(sr) == sr / "manifest.json"


def test_default_roots_are_cwd_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths.default_kb_root() == tmp_path / "data" / "kb"
    assert paths.default_summaries_root() == tmp_path / "kb_summaries"
