"""Per-stage and per-character chunk emission, no LLM.

Story-frontmatter wrapping mirrors `game_data.get_all_text_from_event` so an
LLM trained on the existing prompt corpus sees the same shape per stage chunk
as it does per event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from libs import game_data
from libs.kb import paths
from libs.kb._io import atomic_write_json, atomic_write_text
from libs.kb.paths import Family, Section


def _prune_stale_files(directory: Path, glob_pattern: str, keep: set[str]) -> None:
    """Delete files in `directory` matching `glob_pattern` whose name is not
    in `keep`. Used after rewriting an entity to drop renamed/removed
    chunks before they leak into later grep / filesystem scans."""
    if not directory.is_dir():
        return
    for p in directory.glob(glob_pattern):
        if p.name not in keep:
            p.unlink()


# --- per-stage chunks -----------------------------------------------------


def format_stage_chunk(
    event_id: str,
    event_name: str,
    stage_idx: int,
    stage: dict,
    raw_text: str,
) -> str:
    """Frontmatter + cleaned body in the `<章节>` shape. `avgTag` is appended
    to the chapter heading so `_beg`/`_end` stages that share a `storyName`
    remain distinguishable (matches `game_data.get_all_text_from_event`)."""
    chapter = stage["name"]
    avg_tag = stage.get("avgTag")
    if avg_tag:
        chapter = f"{stage['name']}（{avg_tag}）"
    info = stage.get("storyInfoTxt") or ""
    return (
        "<章节>\n"
        f"<活动名称>{event_name}</活动名称>\n"
        f"<活动ID>{event_id}</活动ID>\n"
        f"<章节序号>{stage_idx:02d}</章节序号>\n"
        f"<章节名称>{chapter}</章节名称>\n"
        f"<章节简介>{info}</章节简介>\n"
        f"<正文>\n{raw_text}\n</正文>\n"
        "</章节>\n"
    )


def collect_storytxt_prefixes(stages: list[dict]) -> list[str]:
    """Return the sorted set of distinct `storyTxt` prefixes across stages.

    Most events are single-prefix, but the live corpus has at least two
    mixed-prefix cases (`main_0`: `obt/guide` + `obt/main`; `act3d0`:
    `activities/act3d0` + `activities/act11d7`). Recording only the first
    stage's prefix would silently lose the second subtree, so callers
    relying on event-level provenance miss part of the event.
    """
    seen: set[str] = set()
    for s in stages:
        seen.add(paths.story_txt_prefix(s.get("storyTxt", "")))
    seen.discard("")
    return sorted(seen)


def write_event(
    kb_root: Path,
    game_data_path: str,
    event_id: str,
    event_data: dict,
    source_data_version: str,
) -> dict:
    """Write `event.json` + one `stage_<NN>_<slug>.txt` per stage.

    Returns the event manifest dict (also persisted as event.json).
    """
    name = event_data["name"]
    entry_type = event_data["entryType"]
    stages = event_data["stages"]
    first_story_txt = stages[0]["storyTxt"] if stages else ""
    family: Family = paths.source_family(first_story_txt, entry_type)
    prefixes = collect_storytxt_prefixes(stages)

    event_dir = paths.event_dir(kb_root, event_id)
    event_dir.mkdir(parents=True, exist_ok=True)
    stage_records: list[dict] = []
    total_length = 0
    for idx, stage in enumerate(stages):
        raw = game_data.get_raw_story_txt(game_data_path, stage["storyTxt"])
        chunk = format_stage_chunk(event_id, name, idx, stage, raw)
        fname = paths.stage_filename(idx, stage["name"], stage.get("avgTag"))
        atomic_write_text(event_dir / fname, chunk)
        stage_records.append(
            {
                "idx": idx,
                "name": stage["name"],
                "avgTag": stage.get("avgTag"),
                "file": fname,
                "length": len(chunk),
                "story_txt": stage["storyTxt"],
            }
        )
        total_length += len(chunk)

    _prune_stale_files(event_dir, "stage_*.txt", {s["file"] for s in stage_records})

    manifest = {
        "event_id": event_id,
        "name": name,
        "entryType": entry_type,
        "source_family": family,
        "storyTxt_prefixes": prefixes,
        "stages": stage_records,
        "total_length": total_length,
        "source_data_version": source_data_version,
    }
    atomic_write_json(paths.event_json_path(kb_root, event_id), manifest)
    return manifest


# --- per-char sectional layout -------------------------------------------


def format_profile(char: dict) -> str | None:
    """`<干员招聘文本>` + optional `<国家>` line. `nationId` is included so
    `char get --section profile` answers basic origin questions without a
    second manifest lookup (per DESIGN.md "On-disk layout")."""
    usage = char.get("itemUsage")
    desc = char.get("itemDesc")
    if not usage and not desc:
        return None
    body = "\n".join(x for x in (usage, desc) if x)
    out = f"<干员招聘文本>\n{body}\n</干员招聘文本>\n"
    nation = char.get("nationId")
    if nation:
        out += f"<国家>{nation}</国家>\n"
    return out


def format_voice(char: dict) -> str | None:
    words = char.get("words")
    if not words:
        return None
    body = "\n".join(words)
    return f"<干员语音>\n{body}\n</干员语音>\n"


def format_archive(char: dict) -> str | None:
    stories = char.get("stories")
    if not stories:
        return None
    body = "\n".join(f"{title}:\n{text}" for title, text in stories.items())
    return f"<干员档案>\n{body}\n</干员档案>\n"


def format_skins(char: dict) -> str | None:
    skins = char.get("skins")
    if not skins:
        return None
    lines = ["<干员皮肤>"]
    populated = 0
    for s in skins:
        if s.get("description") is None:
            continue
        lines.append(
            f"名称:{s.get('skinName','')}\t描述:{s.get('dialog','')}\t用途:{s.get('usage','')}\t旁白:{s.get('description','')}"
        )
        populated += 1
    if not populated:
        return None
    lines.append("</干员皮肤>\n")
    return "\n".join(lines)


def format_modules(char: dict) -> str | None:
    mods = char.get("uniequip")
    if not mods:
        return None
    blocks = []
    for s in mods:
        if s.get("uniEquipDesc") is None:
            continue
        blocks.append(
            f"<模组名称>{s.get('uniEquipName','')}</模组名称>\n<模组描述>\n{s['uniEquipDesc']}\n</模组描述>\n"
        )
    if not blocks:
        return None
    return "<干员模组>\n" + "\n".join(blocks) + "</干员模组>\n"


SECTION_FORMATTERS: dict[Section, Callable[[dict], str | None]] = {
    "profile": format_profile,
    "voice": format_voice,
    "archive": format_archive,
    "skins": format_skins,
    "modules": format_modules,
}


def extract_section_texts(char: dict) -> dict[Section, str]:
    out: dict[Section, str] = {}
    for section, fn in SECTION_FORMATTERS.items():
        text = fn(char)
        if text:
            out[section] = text
    return out


# --- storyset link resolution --------------------------------------------


def build_storytxt_index(story_review: dict) -> dict[str, list[tuple[str, int]]]:
    """`storyTxt -> [(event_id, stage_idx), ...]`.

    Multi-hits are preserved so callers can detect ambiguity. M3 measured
    zero ambiguous and zero missing entries on the 2026-05-08 corpus.
    """
    idx: dict[str, list[tuple[str, int]]] = {}
    for event_id, ev in story_review.items():
        for i, stage in enumerate(ev["stages"]):
            idx.setdefault(stage["storyTxt"], []).append((event_id, i))
    return idx


def resolve_storysets(
    char: dict,
    storytxt_index: dict[str, list[tuple[str, int]]],
) -> tuple[list[dict], list[dict]]:
    """Return (linked, warnings).

    `linked` is the list of `{storySetName, storyTxt, linked_event_id,
    linked_stage_idx}` dicts to persist. `warnings` records any storyset
    whose `storyTxt` is missing from the story-review or hits >1 stages, so
    `kb_build.py` can surface them to the user (per DESIGN.md "Risks" row 4).
    """
    linked: list[dict] = []
    warnings: list[dict] = []
    for ss in char.get("storysets") or []:
        story_txt = ss["storyTxt"]
        hits = storytxt_index.get(story_txt, [])
        if not hits:
            warnings.append({"storySetName": ss["storySetName"], "storyTxt": story_txt, "reason": "missing"})
            continue
        if len(hits) > 1:
            warnings.append(
                {
                    "storySetName": ss["storySetName"],
                    "storyTxt": story_txt,
                    "reason": "ambiguous",
                    "candidates": [{"event_id": e, "stage_idx": i} for e, i in hits],
                }
            )
            continue
        eid, sidx = hits[0]
        linked.append(
            {
                "storySetName": ss["storySetName"],
                "storyTxt": story_txt,
                "linked_event_id": eid,
                "linked_stage_idx": sidx,
            }
        )
    return linked, warnings


# --- alias computation ---------------------------------------------------


def compute_char_aliases(
    char: dict,
    *,
    curated: dict[str, list[str]] | None = None,
    ambiguous_canonicals: Iterable[str] | None = None,
) -> list[str]:
    """Return de-duped aliases for `manifest.aliases`.

    Raw-only mode: `[name, appellation]` (non-empty, deduped).
    Enriched mode (`curated` provided): plus every alias whose canonical
    matches `name` AND that canonical is not in `ambiguous_canonicals`.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(s: str | None):
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    name = char.get("name")
    appellation = char.get("appellation")
    _add(name)
    _add(appellation)

    if curated is not None and name:
        ambig = set(ambiguous_canonicals or ())
        if name not in ambig:
            for alias in curated.get(name, []):
                _add(alias)
    return out


# --- top-level char writer ----------------------------------------------


def write_char(
    kb_root: Path,
    char_id: str,
    char: dict,
    storytxt_index: dict[str, list[tuple[str, int]]],
    *,
    curated_aliases: dict[str, list[str]] | None = None,
    ambiguous_canonicals: Iterable[str] | None = None,
) -> tuple[dict, list[dict]]:
    """Write `chars/<char_id>/` (manifest, sections, storysets).

    Returns (manifest, storyset_warnings). Caller is responsible for
    skipping nameless records — `name` is required by the manifest schema
    (see DESIGN.md "Build pipeline" step 4)."""
    name = char.get("name")
    if not name:
        raise ValueError(f"write_char called for nameless record {char_id!r}")

    section_texts = extract_section_texts(char)
    for section, text in section_texts.items():
        atomic_write_text(paths.char_section_path(kb_root, char_id, section), text)

    # Section files must match `manifest.sections` exactly — a section that
    # disappeared between builds cannot leave its old file behind.
    for section in set(paths.SECTIONS) - section_texts.keys():
        paths.char_section_path(kb_root, char_id, section).unlink(missing_ok=True)

    linked, warnings = resolve_storysets(char, storytxt_index)
    atomic_write_json(paths.char_storysets_path(kb_root, char_id), linked)

    aliases = compute_char_aliases(
        char,
        curated=curated_aliases,
        ambiguous_canonicals=ambiguous_canonicals,
    )
    manifest = {
        "char_id": char_id,
        "name": name,
        "appellation": char.get("appellation"),
        "aliases": aliases,
        "nationId": char.get("nationId"),
        "sections": list(section_texts.keys()),
        "storyset_count": len(linked),
    }
    atomic_write_json(paths.char_manifest_path(kb_root, char_id), manifest)
    return manifest, warnings
