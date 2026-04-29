import os
import json
import re
from libs.bases import (
    get_simple_filename,
)
from pypinyin import lazy_pinyin
from itertools import chain

handbook_info_filename = "zh_CN/gamedata/excel/handbook_info_table.json"
character_filename = "zh_CN/gamedata/excel/character_table.json"
charword_filename = "zh_CN/gamedata/excel/charword_table.json"
skin_filename = "zh_CN/gamedata/excel/skin_table.json"
uniequip_filename = "zh_CN/gamedata/excel/uniequip_table.json"
enemy_handbook_filename = "zh_CN/gamedata/excel/enemy_handbook_table.json"

LINE_CHANGE = "\n"


def extract_data_from_story_review_table(game_data_path):
    file_path = os.path.join(
        game_data_path, "zh_CN/gamedata/excel/story_review_table.json"
    )
    with open(file_path, "r") as file:
        raw_json = json.load(file)
    print(f"loaded story_review from {file_path}\nNo. Entries: {len(raw_json.keys())}")
    ret = {}
    for k, val in raw_json.items():
        event_id = val["id"]
        assert event_id not in ret, ret[event_id]
        # Sort by storySort so chapters are emitted in narrative order even if
        # the upstream JSON is ever reshuffled. In practice the JSON is sorted
        # already, but the field is meaningful and cheap to honor.
        stages_raw = sorted(
            val["infoUnlockDatas"], key=lambda v: v.get("storySort", 0)
        )
        ret[event_id] = {
            "name": val["name"],
            "entryType": val["entryType"],
            "stages": [
                {
                    "name": v["storyName"],
                    # avgTag distinguishes 行动前 / 行动后 / 幕间 — without it,
                    # _beg and _end stages share an identical chapter heading.
                    "avgTag": v.get("avgTag"),
                    "storyInfoTxt": (
                        _get_story_info_text(game_data_path, v["storyInfo"])
                        if v["storyInfo"] is not None
                        else ""
                    ),
                    "storyTxt": v["storyTxt"],
                }
                for v in stages_raw
            ],
        }

    return ret


def extract_data_from_character_table(game_data_path, character_filename):

    with open(os.path.join(game_data_path, character_filename), "r") as file:
        character_raw_json = json.load(file)
    print(
        f"loaded character_table from {character_filename}\nNo. Entries: {len(character_raw_json.keys())}"
    )

    def _get_data(char):
        keys = [
            "name",
            "itemUsage",
            "itemDesc",
            "nationId",
        ]
        return {k: char[k] for k in keys}

    ret = {}
    for k, v in character_raw_json.items():
        if not k.startswith("char"):
            continue
        ret[k] = _get_data(v)
    return ret


def extract_data_from_charword_table(game_data_path, charword_filename):
    with open(os.path.join(game_data_path, charword_filename), "r") as file:
        charword_raw_json = json.load(file)
    print(
        f"loaded charword_table from {charword_filename}\nNo. Entries: {len(charword_raw_json.keys())}"
    )
    ret = {}
    for k, val in chain(
        charword_raw_json["charWords"].items(),
        charword_raw_json["charExtraWords"].items(),
    ):
        char_id = val["charId"]
        word = val["voiceText"]
        if char_id not in ret:
            ret[char_id] = {"words": []}
        ret[char_id]["words"].append(word)

    return ret


def _get_story_info_text(game_data_path, info_link):
    with open(
        os.path.join(
            game_data_path,
            "zh_CN/gamedata/story/",
            "[uc]" + info_link + ".txt",
        ),
        "r",
    ) as file:
        file_content = file.read()
    return file_content


def extract_data_from_handbook_info_table(game_data_path, filename):
    with open(os.path.join(game_data_path, filename), "r") as file:
        raw_json = json.load(file)
    print(
        f"loaded handbook_info_table from {filename}\nNo. Entries: {len(raw_json.keys())}"
    )
    ret = {}
    for k, val in raw_json["handbookDict"].items():
        char_id = val["charID"]
        assert char_id not in ret, ret[char_id]

        ret[char_id] = {
            "stories": {
                v1["storyTitle"]: "\n".join([v2["storyText"] for v2 in v1["stories"]])
                for v1 in val["storyTextAudio"]
            },
            "storysets": [
                {
                    "storySetName": v1["storySetName"],
                    "storyTxt": v1["avgList"][0]["storyTxt"],
                    "storyInfoTxt": _get_story_info_text(
                        game_data_path, v1["avgList"][0]["storyInfo"]
                    ),
                }
                for v1 in val["handbookAvgList"]
            ],
        }

    return ret


def extract_data_from_skin_table(game_data_path, filename):
    with open(os.path.join(game_data_path, filename), "r") as file:
        raw_json = json.load(file)
    print(f"loaded skin from {filename}\nNo. Entries: {len(raw_json.keys())}")
    ret = {}
    for k, val in raw_json["charSkins"].items():
        if not k.startswith("char"):
            continue
        assert "charID" in val or "charId" in val, val.keys()
        char_id = val["charId"] if "charId" in val else val["charID"]

        if char_id not in ret:
            ret[char_id] = {"skins": []}
        ret[char_id]["skins"].append(
            {
                "skinName": val["displaySkin"]["skinName"],
                "dialog": val["displaySkin"]["dialog"],
                "usage": val["displaySkin"]["usage"],
                "description": val["displaySkin"]["description"],
            }
        )

    return ret


def get_char_info_raw(all_data):
    all_charid = set(
        [v1 for v1 in chain(*[list(v.keys()) for _, v in all_data.items()])]
    )
    print(f"the number of all char id: {len(all_charid)}")
    ret = {char_id: {"charId": char_id} for char_id in all_charid}
    for char_id in all_charid:
        for _, val in all_data.items():
            if char_id not in val:
                continue
            ret[char_id].update(val[char_id])
    ret_name = {val["name"]: val for k, val in ret.items() if val.get("name", None)}
    return ret, ret_name


def extract_data_from_uniequip_table(game_data_path, filename):
    with open(os.path.join(game_data_path, filename), "r") as file:
        raw_json = json.load(file)
    print(f"loaded uniequip from {filename}\nNo. Entries: {len(raw_json.keys())}")
    ret = {}
    for k, val in raw_json["equipDict"].items():
        assert "charID" in val or "charId" in val, val.keys()
        char_id = val["charId"] if "charId" in val else val["charID"]

        if char_id not in ret:
            ret[char_id] = {"uniequip": []}
        ret[char_id]["uniequip"].append(
            {
                "uniEquipName": val["uniEquipName"],
                "uniEquipDesc": val["uniEquipDesc"],
            }
        )

    return ret


def get_all_char_info(game_data_path):
    all_data = {}
    all_data["character"] = extract_data_from_character_table(
        game_data_path, character_filename
    )
    all_data["charword"] = extract_data_from_charword_table(
        game_data_path, charword_filename
    )
    all_data["handbook_info"] = extract_data_from_handbook_info_table(
        game_data_path, handbook_info_filename
    )
    all_data["skin"] = extract_data_from_skin_table(game_data_path, skin_filename)
    all_data["uniequip"] = extract_data_from_uniequip_table(
        game_data_path, uniequip_filename
    )

    char_info, char_name_info = get_char_info_raw(all_data)
    return char_info, char_name_info


def clean_script(text: str) -> str:
    # Replace {@nickname} with 博士
    text = text.replace("{@nickname}", "博士")

    def extract_options(match):
        options = match.group(1)
        return f"博士（多个选择）:{options}"  # optional: split into separate lines

    text = re.sub(
        r'\[Decision\(options="(.*?)",\s*values=".*?"\)\]', extract_options, text
    )

    def extract_subtitle(match):
        options = match.group(1)
        return f"旁白:{options}"  # optional: split into separate lines

    text = re.sub(
        r'\[Subtitle\(text="(.*?)"(?:,.*?)?\)\]',
        extract_subtitle,
        text,
    )

    # Sticker text often carries narration / inscriptions / scripture (e.g.
    # 圣巡 scripture in act46side). `text=` is not always the first param so
    # we match it anywhere inside the bracket. The literal `\n` escape in the
    # source separates inscription lines — split on it and emit each
    # non-empty piece as its own 旁白: line so the prefix isn't lost. Sticker
    # tags without `text=` (e.g. `[Sticker(id="st1")]` to clear) fall through
    # to the catch-all bracket-line strip below.
    def extract_sticker(match):
        pieces = [p.strip() for p in match.group(1).split("\\n") if p.strip()]
        return "\n".join(f"旁白:{p}" for p in pieces)

    text = re.sub(
        r'\[Sticker\([^\]]*?text="(.*?)"[^\]]*?\)\]',
        extract_sticker,
        text,
    )

    # Replace [name="CHARACTER"]Dialogue → CHARACTER: Dialogue
    pattern = re.compile(
        r'^\[(?:multiline\()?name="([^"]+)"(?:,end=true)?\)?\](.*)$', re.MULTILINE
    )
    text = pattern.sub(r"\1:\2", text)
    # text = re.sub(
    #     r'\[*?name="([^"]+)"[^"]?\](.*?)\n',
    #     lambda m: f"{m.group(1)}: {m.group(2).strip()}\n",
    #     text,
    #     flags=re.DOTALL,
    # )

    # Remove lines that start and end with brackets (e.g., [some_tag])
    text = re.sub(r"^\s*\[[^\]]*\]\s*$", "", text, flags=re.MULTILINE)

    # Remove empty lines
    text = "\n".join(line for line in text.splitlines() if line.strip())

    return text.strip()


def _get_raw_story_txt(game_data_path, story_txt):
    filename = os.path.join(game_data_path, "zh_CN/gamedata/story", story_txt + ".txt")
    with open(filename, "r") as f:
        raw_txt = f.read()

    return clean_script(raw_txt)


def get_all_text_from_event(game_data_path, event_data):
    lines = [f"<活动名称>{event_data['name']}</活动名称>"]
    for v in event_data["stages"]:
        chapter_name = v["name"]
        if v.get("avgTag"):
            chapter_name = f"{v['name']}（{v['avgTag']}）"
        lines.append(f"<章节>")
        lines.append(f"<章节名称>{chapter_name}</章节名称>")
        lines.append(f"<章节简介>{v['storyInfoTxt']}</章节简介>")
        lines.append(
            f"<正文>\n{_get_raw_story_txt(game_data_path, v['storyTxt'])}\n</正文>"
        )
        lines.append(f"</章节>\n")
    return "\n".join(lines)


def get_char_info_text_prompt(val):
    lines = []
    lines.append("<干员信息>")
    lines.append(f"<干员名称>\n{val['name']}</干员名称>")
    if "itemUsage" in val and "itemDesc" in val:
        lines.append(
            f"\n<干员招聘文本>\n{val['itemUsage']}\n{val['itemDesc']}\n</干员招聘文本>"
        )
    if "words" in val:
        lines.append(f"\n<干员语音>\n{LINE_CHANGE.join(val['words'])}\n</干员语音>")
    if "stories" in val:
        tmp = LINE_CHANGE.join([f"{k1}:{v1}" for k1, v1 in val["stories"].items()])
        lines.append(f"\n<干员档案>\n{tmp}\n</干员档案>")

    if "skins" in val:
        lines.append("\n<干员皮肤>")
        for s in val["skins"]:
            if s.get("description", None) is None:
                continue
            lines.append(
                f"名称:{s['skinName']}\t描述:{s['dialog']}\t用途:{s['usage']}\t旁白:{s['description']}"
            )
        lines.append("</干员皮肤>")

    if "uniequip" in val:
        lines.append("\n<干员模组>")
        for s in val["uniequip"]:
            if s.get("uniEquipDesc", None) is None:
                continue
            lines.append(
                f"<模组名称>{s['uniEquipName']}</模组名称>\n<模组描述>\n{s['uniEquipDesc']}\n</模组描述>\n"
            )
        lines.append("</干员模组>")
    lines.append("</干员信息>")
    return "\n".join(lines)


def get_char_file_name(char_name, char_name_info):
    if char_name in char_name_info:
        return char_name_info[char_name]["charId"]
    char_id = "_".join(lazy_pinyin(char_name))
    filename = get_simple_filename(char_id)
    return "extended_char_" + filename
