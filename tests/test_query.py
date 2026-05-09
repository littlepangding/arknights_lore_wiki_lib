"""Tests for `libs.kb.query`. Most cases use the shared `loaded_kb`
fixture (mini gamedata → chunker → indexer → query.load_kb). A handful
take `build_real_kb` directly for cases that need a curated alias file
or a custom KB layout. Resolver tests against ambiguous canonicals use
a hand-built KB via the `make_char` factory."""

from __future__ import annotations

import pytest

from libs.kb import indexer, query


# --- hand-built KB for resolver-ambiguity tests --------------------


@pytest.fixture
def ambiguous_kb(tmp_path, make_char):
    """KB with two chars sharing display name `暮落` (the documented
    ambiguity case) plus one unambiguous char with a curated alias.
    No events; only the resolver-relevant indexes are built."""
    kb = tmp_path / "kb"
    make_char(kb, "char_a1", name="暮落", appellation="Aprot_1")
    make_char(kb, "char_a2", name="暮落", appellation="Aprot_2")
    make_char(
        kb,
        "char_amiya",
        name="阿米娅",
        appellation="Amiya",
        nation="rhodes",
        sections=["profile"],
        profile_text="<干员招聘文本>...</干员招聘文本>\n",
    )
    curated_path = tmp_path / "char_alias.txt"
    curated_path.write_text("暮落;沉渊\n阿米娅;Amy\n", encoding="utf-8")
    indexer.build_all_indexes(kb, curated_aliases_path=curated_path)
    return query.load_kb(kb)


# --- load_kb -----------------------------------------------------------


def test_load_kb_round_trip(loaded_kb):
    assert set(loaded_kb.event_manifests) == {"act_test", "main_01", "mem_aria"}
    assert set(loaded_kb.char_manifests) == {"char_test_001", "char_test_002"}
    assert loaded_kb.events_by_family["activity"] == ["act_test"]
    assert loaded_kb.events_by_family["mainline"] == ["main_01"]
    assert loaded_kb.events_by_family["operator_record"] == ["mem_aria"]


def test_load_kb_handles_missing_indexes(tmp_path):
    """A bare KB root with nothing built yet should still load, with
    empty containers — useful during partial builds and tests."""
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    kb = query.load_kb(kb_root)
    assert kb.event_manifests == {}
    assert kb.char_manifests == {}
    assert kb.alias_to_char_ids == {}


def test_load_kb_precomputes_direct_name_lookup(loaded_kb):
    """`KB.direct_name_to_char_ids` is the resolver fast-path. It must
    contain both `name` and `appellation` keys for every char."""
    assert loaded_kb.direct_name_to_char_ids["艾莉亚"] == ["char_test_001"]
    assert loaded_kb.direct_name_to_char_ids["Aria"] == ["char_test_001"]


# --- event browsing --------------------------------------------------


def test_list_events_no_filter_returns_sorted(loaded_kb):
    eids = [e.event_id for e in query.list_events(loaded_kb)]
    assert eids == sorted(eids)
    assert set(eids) == {"act_test", "main_01", "mem_aria"}


def test_list_events_filters_by_family(loaded_kb):
    assert [e.event_id for e in query.list_events(loaded_kb, family="mainline")] == ["main_01"]
    assert [e.event_id for e in query.list_events(loaded_kb, family="activity")] == ["act_test"]
    assert [e.event_id for e in query.list_events(loaded_kb, family="operator_record")] == ["mem_aria"]
    assert query.list_events(loaded_kb, family="mini_activity") == []


def test_list_families_counts(loaded_kb):
    counts = query.list_families(loaded_kb)
    assert counts["activity"] == 1
    assert counts["mainline"] == 1
    assert counts["operator_record"] == 1
    assert counts["mini_activity"] == 0
    assert counts["other"] == 0


def test_get_event_returns_meta(loaded_kb):
    ev = query.get_event(loaded_kb, "act_test")
    assert ev is not None
    assert ev.name == "测试活动"
    assert ev.source_family == "activity"
    assert len(ev.stages) == 3


def test_get_event_returns_none_for_unknown(loaded_kb):
    assert query.get_event(loaded_kb, "no_such_event") is None


def test_get_stage_text_reads_chunk(loaded_kb):
    txt = query.get_stage_text(loaded_kb, "act_test", 0)
    assert txt is not None
    assert "<章节序号>00</章节序号>" in txt


def test_get_stage_text_returns_none_for_bad_index(loaded_kb):
    assert query.get_stage_text(loaded_kb, "act_test", 99) is None
    assert query.get_stage_text(loaded_kb, "no_event", 0) is None


# --- character data --------------------------------------------------


def test_list_chars_filtered_by_nation(loaded_kb):
    rhodes = query.list_chars(loaded_kb, nation="testland")
    assert {c.char_id for c in rhodes} == {"char_test_001", "char_test_002"}
    assert query.list_chars(loaded_kb, nation="nope") == []


def test_get_char_section_specific(loaded_kb):
    profile = query.get_char_section(loaded_kb, "char_test_001", "profile")
    assert profile is not None
    assert "<干员招聘文本>" in profile


def test_get_char_section_all_concatenates(loaded_kb):
    full = query.get_char_section(loaded_kb, "char_test_001", "all")
    assert full is not None
    assert "<干员招聘文本>" in full
    assert "<干员档案>" in full


def test_get_char_section_returns_none_for_missing(loaded_kb):
    # char_test_002 has only profile (no voice/archive)
    assert query.get_char_section(loaded_kb, "char_test_002", "voice") is None


def test_char_storysets_returns_typed_links(loaded_kb):
    links = query.char_storysets(loaded_kb, "char_test_001")
    assert len(links) == 1
    assert links[0].linked_event_id == "mem_aria"
    assert links[0].linked_stage_idx == 0


# --- resolver -------------------------------------------------------


def test_resolve_by_name_returns_resolved(loaded_kb):
    r = query.resolve_operator_name(loaded_kb, "艾莉亚")
    assert isinstance(r, query.Resolved)
    assert r.char_id == "char_test_001"


def test_resolve_by_appellation_returns_resolved(loaded_kb):
    r = query.resolve_operator_name(loaded_kb, "Aria")
    assert isinstance(r, query.Resolved)
    assert r.char_id == "char_test_001"


def test_resolve_unknown_name_returns_missing(loaded_kb):
    """NPC / title / group lookups land here in v1 — agent should fall
    back to grep_text per the AGENTS_GUIDE."""
    r = query.resolve_operator_name(loaded_kb, "特蕾西娅")
    assert isinstance(r, query.Missing)
    assert r.name == "特蕾西娅"


def test_resolve_curated_alias_when_canonical_unique(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb", curated={"艾莉亚": ["空山"]})
    kb = query.load_kb(kb_root)
    r = query.resolve_operator_name(kb, "空山")
    assert isinstance(r, query.Resolved)
    assert r.char_id == "char_test_001"


def test_resolve_returns_ambiguous_for_duplicate_canonical(ambiguous_kb):
    r = query.resolve_operator_name(ambiguous_kb, "暮落")
    assert isinstance(r, query.Ambiguous)
    assert sorted(r.candidates) == ["char_a1", "char_a2"]


def test_resolve_returns_ambiguous_for_curated_alias_of_collision(ambiguous_kb):
    """`沉渊` is a curated alias for `暮落`, which collides — the
    resolver surfaces all candidates rather than picking arbitrarily."""
    r = query.resolve_operator_name(ambiguous_kb, "沉渊")
    assert isinstance(r, query.Ambiguous)
    assert sorted(r.candidates) == ["char_a1", "char_a2"]


def test_resolve_unique_curated_alias(ambiguous_kb):
    r = query.resolve_operator_name(ambiguous_kb, "Amy")  # curated alias for unique 阿米娅
    assert isinstance(r, query.Resolved)
    assert r.char_id == "char_amiya"


# --- cross-references -----------------------------------------------


def test_char_appearances_filter_source(loaded_kb):
    det_only = query.char_appearances(loaded_kb, "char_test_001", source="deterministic")
    assert len(det_only) == 1
    assert det_only[0].source == "deterministic"
    assert det_only[0].event_id == "mem_aria"
    inf_only = query.char_appearances(loaded_kb, "char_test_001", source="inferred")
    assert all(a.source == "inferred" for a in inf_only)
    assert {a.event_id for a in inf_only} <= {"act_test", "main_01"}
    # `both` returns the union
    both = query.char_appearances(loaded_kb, "char_test_001", source="both")
    assert len(both) == len(det_only) + len(inf_only)


def test_event_chars_returns_appearances_with_metadata(loaded_kb):
    chars = query.event_chars(loaded_kb, "mem_aria")
    # 艾莉亚 has a deterministic edge in mem_aria/0
    aria = next(a for a in chars if a.char_id == "char_test_001")
    assert aria.source == "deterministic"
    assert aria.story_set_name == "故乡的山"
    assert aria.stage_idx == 0


def test_event_chars_filter_source(loaded_kb):
    chars = query.event_chars(loaded_kb, "act_test", source="inferred")
    assert all(a.source == "inferred" for a in chars)
    assert {a.char_id for a in chars} >= {"char_test_001", "char_test_002"}


def test_stage_chars_tight_scope(loaded_kb):
    s0 = query.stage_chars(loaded_kb, "act_test", 0)
    s1 = query.stage_chars(loaded_kb, "act_test", 1)
    assert all(a.stage_idx == 0 for a in s0)
    assert all(a.stage_idx == 1 for a in s1)


def test_group_by_event_rolls_up_appearances():
    apps = [
        query.Appearance(char_id="c1", event_id="ev1", stage_idx=0, source="inferred", count=1, match_class="canonical"),
        query.Appearance(char_id="c1", event_id="ev1", stage_idx=2, source="inferred", count=3, match_class="canonical"),
        query.Appearance(char_id="c2", event_id="ev2", stage_idx=0, source="deterministic", story_set_name="ss"),
    ]
    out = query.group_by_event(apps)
    assert set(out.keys()) == {"ev1", "ev2"}
    assert len(out["ev1"]) == 2
    assert len(out["ev2"]) == 1


# --- grep_text ------------------------------------------------------


def test_grep_text_literal_matches_in_event_bodies(loaded_kb):
    hits = query.grep_text(loaded_kb, "群山为证", scope="events")
    # The Sticker line "石碑铭文：群山为证" lives in act_test stage 0
    assert any(h.event_id == "act_test" and h.stage_idx == 0 for h in hits)
    for h in hits:
        assert "群山为证" in h.line


def test_grep_text_literal_handles_special_chars(loaded_kb):
    """Literal matching is the safe default because NPC/group names
    with parens/brackets break naive regex. A literal grep for
    `（行动前）` should match the chapter heading."""
    hits = query.grep_text(loaded_kb, "（行动前）", scope="events")
    assert hits


def test_grep_text_regex_opt_in(loaded_kb):
    hits = query.grep_text(loaded_kb, r"<章节序号>0\d</章节序号>", scope="events", regex=True)
    assert hits


def test_grep_text_chars_scope_returns_section_field(loaded_kb):
    hits = query.grep_text(loaded_kb, "干员招聘文本", scope="chars")
    assert hits
    assert all(h.char_id is not None and h.section is not None for h in hits)


def test_grep_text_invalid_scope_raises(loaded_kb):
    with pytest.raises(ValueError):
        query.grep_text(loaded_kb, "x", scope="bogus")  # type: ignore[arg-type]


def test_grep_text_skips_files_without_literal_match(loaded_kb):
    """The file-level pre-check should make missing-pattern queries
    return immediately without iterating any file's lines."""
    hits = query.grep_text(loaded_kb, "PATTERN_NOT_IN_ANY_FIXTURE_FILE")
    assert hits == []


# --- summaries ------------------------------------------------------


def test_get_event_summary_returns_none_when_summaries_root_unset(loaded_kb):
    assert query.get_event_summary(loaded_kb, "act_test") is None


def test_get_event_summary_reads_md_when_present(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries = tmp_path / "kb_summaries"
    (summaries / "events").mkdir(parents=True)
    (summaries / "events" / "act_test.md").write_text("# 测试", encoding="utf-8")
    kb = query.load_kb(kb_root, summaries_root=summaries)
    assert query.get_event_summary(kb, "act_test") == "# 测试"
    assert query.get_event_summary(kb, "no_event") is None
