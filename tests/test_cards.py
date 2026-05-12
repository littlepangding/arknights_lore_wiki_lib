"""Unit tests for `libs.kb.cards` — deterministic per-character fact cards."""

from __future__ import annotations

from libs.kb import cards


def test_parse_bracket_block_inline_values():
    block = "【代号】黍\n【性别】女\n【身高】165cm"
    assert cards.parse_bracket_block(block) == {
        "代号": "黍",
        "性别": "女",
        "身高": "165cm",
    }


def test_parse_bracket_block_value_on_following_lines():
    # `矿石病感染情况` has no inline value; the sentence sits on the next line.
    block = "【生日】6月6日\n【矿石病感染情况】\n参照医学检测报告，确认为非感染者。\n【种族】未公开"
    parsed = cards.parse_bracket_block(block)
    assert parsed["生日"] == "6月6日"
    assert parsed["矿石病感染情况"] == "参照医学检测报告，确认为非感染者。"
    assert parsed["种族"] == "未公开"


def test_parse_bracket_block_empty_and_garbage():
    assert cards.parse_bracket_block("") == {}
    assert cards.parse_bracket_block(None) == {}
    # Lines before the first 【…】 are ignored, not crashed on.
    assert cards.parse_bracket_block("一句无关的话\n【代号】X") == {"代号": "X"}


def test_parse_bracket_block_preserves_order():
    parsed = cards.parse_bracket_block("【c】3\n【a】1\n【b】2")
    assert list(parsed) == ["c", "a", "b"]


def test_build_card_full():
    char = {
        "name": "黍",
        "appellation": "Shu",
        "nationId": "yan",
        "stories": {
            "基础档案": "【代号】黍\n【性别】女\n【出身地】炎",
            "综合体检测试": "【物理强度】标准\n【源石技艺适应性】缺陷",
            "客观履历": "黍，炎国农业天师。",
            "档案资料一": "（无关紧要的散文）",
        },
        "skins": [
            {"skinName": "春日宴", "description": "旁白"},
            {"skinName": "无旁白皮肤", "description": None},  # excluded
        ],
        "uniequip": [
            {"uniEquipName": "黍证章", "uniEquipDesc": "..."},
            {"uniEquipName": "空模组", "uniEquipDesc": None},  # excluded
        ],
    }
    linked = [
        {"storySetName": "谓我何求", "linked_event_id": "story_shu_set_1", "linked_stage_idx": 0}
    ]
    card = cards.build_card("char_2025_shu", char, linked)

    assert card["char_id"] == "char_2025_shu"
    assert card["name"] == "黍"
    assert card["appellation"] == "Shu"
    assert card["nationId"] == "yan"
    assert card["basic_info"] == {"代号": "黍", "性别": "女", "出身地": "炎"}
    assert card["physical_exam"] == {"物理强度": "标准", "源石技艺适应性": "缺陷"}
    assert card["objective_record"] == "黍，炎国农业天师。"
    assert card["archive_sections"] == ["基础档案", "综合体检测试", "客观履历", "档案资料一"]
    assert card["skin_names"] == ["春日宴"]
    assert card["module_names"] == ["黍证章"]
    assert card["storysets"] == [
        {"name": "谓我何求", "event_id": "story_shu_set_1", "stage_idx": 0}
    ]
    # Every non-`sources` field is accounted for in `sources`.
    assert set(card["sources"]) >= {
        "name",
        "appellation",
        "nationId",
        "basic_info",
        "physical_exam",
        "objective_record",
        "skin_names",
        "module_names",
        "storysets",
    }


def test_build_card_no_handbook():
    # An NPC / extended char with no handbook entry: card still carries the
    # character_table-derived fields, the parsed blocks are just empty.
    char = {"name": "无名村民", "appellation": None, "nationId": None}
    card = cards.build_card("char_xxx", char, [])
    assert card["name"] == "无名村民"
    assert card["basic_info"] == {}
    assert card["physical_exam"] == {}
    assert card["objective_record"] is None
    assert card["archive_sections"] == []
    assert card["skin_names"] == []
    assert card["module_names"] == []
    assert card["storysets"] == []
