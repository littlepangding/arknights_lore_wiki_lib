"""Deterministic per-character fact cards (no LLM).

Parses the structured `基础档案` / `综合体检测试` blocks out of the handbook,
keeps `客观履历` verbatim, and records skin / module / storyset names. Every
top-level field is tagged with its source table in `sources` so a downstream
validator can cite it. Built by `kb_build` (written into `chars/<id>/card.json`),
read via `kb_query char card`.

The point of the card is to be the cheapest possible correctness anchor: a
basics claim on a wiki page (`性别` / `出身地` / `生日` / …) can be checked
against `basic_info` without re-reading prose or trusting an LLM.
"""

from __future__ import annotations

import re

# `【字段名】值`. A field whose inline value is empty (e.g. `矿石病感染情况`,
# whose sentence sits on the following line) absorbs subsequent lines until
# the next `【…】`.
_FIELD_RE = re.compile(r"^【([^】]+)】[ \t]*(.*)$")


def parse_bracket_block(text: str) -> dict[str, str]:
    """Parse a `【字段】值` block into an ordered dict. Lines that follow a
    field with no inline value are appended to that field's value."""
    out: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal cur, buf
        if cur is not None:
            out[cur] = "\n".join(buf).strip()
        cur, buf = None, []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        m = _FIELD_RE.match(line)
        if m:
            _flush()
            cur = m.group(1).strip()
            v = m.group(2).strip()
            buf = [v] if v else []
        elif cur is not None and line:
            buf.append(line)
    _flush()
    return out


def build_card(char_id: str, char: dict, storysets_linked: list[dict]) -> dict:
    """Assemble the fact card. `char` is one entry of `game_data.get_all_char_info`'s
    first return value; `storysets_linked` is `chunker.resolve_storysets(...)[0]`."""
    stories: dict[str, str] = char.get("stories") or {}
    basic = parse_bracket_block(stories.get("基础档案", ""))
    exam = parse_bracket_block(stories.get("综合体检测试", ""))
    objective = (stories.get("客观履历") or "").strip() or None

    skin_names = [
        s["skinName"]
        for s in (char.get("skins") or [])
        if s.get("description") is not None and s.get("skinName")
    ]
    module_names = [
        s["uniEquipName"]
        for s in (char.get("uniequip") or [])
        if s.get("uniEquipDesc") is not None and s.get("uniEquipName")
    ]
    storysets = [
        {
            "name": ss["storySetName"],
            "event_id": ss["linked_event_id"],
            "stage_idx": ss["linked_stage_idx"],
        }
        for ss in storysets_linked
    ]

    return {
        "char_id": char_id,
        "name": char.get("name"),
        "appellation": char.get("appellation"),
        "nationId": char.get("nationId"),
        "basic_info": basic,
        "physical_exam": exam,
        "objective_record": objective,
        "archive_sections": list(stories.keys()),
        "skin_names": skin_names,
        "module_names": module_names,
        "storysets": storysets,
        "sources": {
            "name": "character_table",
            "appellation": "character_table",
            "nationId": "character_table",
            "basic_info": "handbook_info_table:基础档案",
            "physical_exam": "handbook_info_table:综合体检测试",
            "objective_record": "handbook_info_table:客观履历",
            "archive_sections": "handbook_info_table:storyTextAudio titles",
            "skin_names": "skin_table",
            "module_names": "uniequip_table",
            "storysets": "handbook_info_table:handbookAvgList + story_review_table",
        },
    }
