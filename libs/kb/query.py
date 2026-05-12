"""Pure-function retrieval API over the built KB.

Every function takes a `KB` (loaded indexes + manifest cache) as its
first arg and returns Python values. CLI wrappers in
`scripts/kb_query.py` print JSON; nothing in this module touches stdout.

The resolver returns a tagged sum (`Resolved | Ambiguous | Missing`)
rather than a plain `char_id`, because the corpus has 9 known duplicate
display names plus curated aliases that point at duplicate canonicals
— silently picking one would be a lie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from libs.kb import indexer, paths
from libs.kb._io import read_json, read_json_or
from libs.kb.paths import Family, FAMILIES, MatchClass, Section, SECTIONS


SectionOrAll = Literal["profile", "voice", "archive", "skins", "modules", "all"]
SourceFilter = Literal["deterministic", "inferred", "both"]
GrepScope = Literal["events", "chars", "summaries", "all"]

SOURCE_FILTERS: tuple[SourceFilter, ...] = ("deterministic", "inferred", "both")
GREP_SCOPES: tuple[GrepScope, ...] = ("events", "chars", "summaries", "all")
SECTIONS_OR_ALL: tuple[SectionOrAll, ...] = (
    "profile",
    "voice",
    "archive",
    "skins",
    "modules",
    "all",
)


# --- API dataclasses ---------------------------------------------------


@dataclass(frozen=True)
class EventMeta:
    event_id: str
    name: str
    entryType: str
    source_family: Family
    storyTxt_prefixes: tuple[str, ...]
    stages: tuple[dict, ...]
    total_length: int


@dataclass(frozen=True)
class CharMeta:
    char_id: str
    name: str
    appellation: str | None
    aliases: tuple[str, ...]
    nationId: str | None
    sections: tuple[Section, ...]
    storyset_count: int


@dataclass(frozen=True)
class Appearance:
    """One char in one stage. `count` and `match_class` are populated for
    inferred rows only; `story_set_name` is populated for deterministic
    rows only — keeping them on a single shape lets callers iterate
    without case-splitting."""

    char_id: str
    event_id: str
    stage_idx: int
    source: Literal["deterministic", "inferred"]
    count: int | None = None
    match_class: MatchClass | None = None
    story_set_name: str | None = None


@dataclass(frozen=True)
class StorySetLink:
    storySetName: str
    storyTxt: str
    linked_event_id: str
    linked_stage_idx: int


@dataclass(frozen=True)
class Match:
    event_id: str | None
    stage_idx: int | None
    char_id: str | None
    section: Section | None
    line: str
    line_no: int
    snippet: str
    source: Literal["stage", "char_section", "event_summary"] = "stage"


# --- resolver sum type -------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    char_id: str
    kind: Literal["resolved"] = "resolved"


@dataclass(frozen=True)
class Ambiguous:
    candidates: tuple[str, ...]
    kind: Literal["ambiguous"] = "ambiguous"


@dataclass(frozen=True)
class Missing:
    name: str
    kind: Literal["missing"] = "missing"


Resolution = Resolved | Ambiguous | Missing


# --- the loaded KB -----------------------------------------------------


@dataclass
class KB:
    root: Path
    summaries_root: Path | None
    events_by_family: dict[Family, list[str]]
    event_manifests: dict[str, dict]
    char_manifests: dict[str, dict]
    char_to_events_deterministic: dict[str, list[dict]]
    char_to_events_inferred: dict[str, list[dict]]
    event_to_chars: dict[str, list[dict]]
    stage_table: list[dict]
    char_table: list[dict]
    alias_to_char_ids: dict[str, list[str]]
    # Resolver lookup precomputed at load time so per-call cost is O(1)
    # rather than O(chars). Built from name + appellation; the curated
    # alias index is layered on top in `resolve_operator_name`.
    direct_name_to_char_ids: dict[str, list[str]] = field(default_factory=dict)


def load_kb(
    kb_root: Path | str,
    summaries_root: Path | str | None = None,
) -> KB:
    """Load every index + manifest into memory once. Cheap (JSON only,
    no chunk text)."""
    root = Path(kb_root)
    sumroot = Path(summaries_root) if summaries_root else None

    events_by_family = read_json_or(
        paths.index_path(root, "events_by_family"), {f: [] for f in FAMILIES}
    )
    deterministic = read_json_or(
        paths.index_path(root, "char_to_events_deterministic"), {}
    )
    inferred = read_json_or(paths.index_path(root, "char_to_events_inferred"), {})
    event_to_chars = read_json_or(paths.index_path(root, "event_to_chars"), {})
    stage_table = read_json_or(paths.index_path(root, "stage_table"), [])
    char_table = read_json_or(paths.index_path(root, "char_table"), [])
    alias_index = read_json_or(
        paths.index_path(root, "char_alias"), {"alias_to_char_ids": {}}
    )

    event_manifests = indexer.load_event_manifests(root)
    char_manifests = indexer.load_char_manifests(root)

    direct = indexer.compute_name_to_char_ids(char_manifests)
    for cid, mf in char_manifests.items():
        ap = mf.get("appellation")
        if ap and cid not in direct.setdefault(ap, []):
            direct[ap].append(cid)

    return KB(
        root=root,
        summaries_root=sumroot,
        events_by_family=events_by_family,
        event_manifests=event_manifests,
        char_manifests=char_manifests,
        char_to_events_deterministic=deterministic,
        char_to_events_inferred=inferred,
        event_to_chars=event_to_chars,
        stage_table=stage_table,
        char_table=char_table,
        alias_to_char_ids=alias_index.get("alias_to_char_ids", {}),
        direct_name_to_char_ids=direct,
    )


# --- event browsing ----------------------------------------------------


def _event_meta(ev: dict) -> EventMeta:
    return EventMeta(
        event_id=ev["event_id"],
        name=ev["name"],
        entryType=ev["entryType"],
        source_family=ev["source_family"],
        storyTxt_prefixes=tuple(ev.get("storyTxt_prefixes", [])),
        stages=tuple(ev.get("stages", [])),
        total_length=ev.get("total_length", 0),
    )


def list_events(kb: KB, family: Family | None = None) -> list[EventMeta]:
    if family is None:
        evs = sorted(kb.event_manifests.values(), key=lambda e: e["event_id"])
    else:
        evs = [
            kb.event_manifests[eid]
            for eid in kb.events_by_family.get(family, [])
            if eid in kb.event_manifests
        ]
    return [_event_meta(e) for e in evs]


def list_families(kb: KB) -> dict[Family, int]:
    return {f: len(kb.events_by_family.get(f, [])) for f in FAMILIES}


def get_event(kb: KB, event_id: str) -> EventMeta | None:
    ev = kb.event_manifests.get(event_id)
    return _event_meta(ev) if ev else None


def get_stage_meta(kb: KB, event_id: str, stage_idx: int) -> dict | None:
    """Stage row from `event.json` (`name`, `avgTag`, `file`, `length`,
    `story_txt`). Returned as the raw dict so callers stay decoupled
    from `EventMeta`'s frozen-dataclass shape."""
    ev = kb.event_manifests.get(event_id)
    if not ev:
        return None
    for s in ev["stages"]:
        if s["idx"] == stage_idx:
            return s
    return None


def get_stage_text(kb: KB, event_id: str, stage_idx: int) -> str | None:
    s = get_stage_meta(kb, event_id, stage_idx)
    if not s:
        return None
    p = paths.event_dir(kb.root, event_id) / s["file"]
    return p.read_text(encoding="utf-8") if p.is_file() else None


# --- character data ----------------------------------------------------


def _char_meta(mf: dict) -> CharMeta:
    return CharMeta(
        char_id=mf["char_id"],
        name=mf["name"],
        appellation=mf.get("appellation"),
        aliases=tuple(mf.get("aliases", [])),
        nationId=mf.get("nationId"),
        sections=tuple(mf.get("sections", [])),
        storyset_count=mf.get("storyset_count", 0),
    )


def list_chars(kb: KB, nation: str | None = None) -> list[CharMeta]:
    items = sorted(kb.char_manifests.values(), key=lambda c: c["char_id"])
    if nation is not None:
        items = [c for c in items if c.get("nationId") == nation]
    return [_char_meta(c) for c in items]


def resolve_operator_name(kb: KB, name_or_alias: str) -> Resolution:
    """Resolve against (a) every char's `name` + `appellation` and
    (b) the curated alias index when present. Multi-target rows from
    either source produce `Ambiguous`; zero-target produces `Missing`.
    NPC / title / group lookups land in `Missing` — that's the
    documented v1 contract; the caller is expected to fall back to
    `grep_text`."""
    candidates: list[str] = []
    seen: set[str] = set()
    for src in (kb.direct_name_to_char_ids, kb.alias_to_char_ids):
        for cid in src.get(name_or_alias, []):
            if cid not in seen:
                seen.add(cid)
                candidates.append(cid)
    if not candidates:
        return Missing(name=name_or_alias)
    if len(candidates) == 1:
        return Resolved(char_id=candidates[0])
    return Ambiguous(candidates=tuple(candidates))


def get_char_section(
    kb: KB, char_id: str, section: SectionOrAll = "all"
) -> str | None:
    """`section='all'` concatenates every populated section file in the
    canonical SECTIONS order. Returns `None` if every requested file is
    absent."""
    if section == "all":
        parts: list[str] = []
        cdir = paths.char_dir(kb.root, char_id)
        for sec in SECTIONS:
            p = cdir / f"{sec}.txt"
            if p.is_file():
                parts.append(p.read_text(encoding="utf-8"))
        return "".join(parts) if parts else None
    p = paths.char_section_path(kb.root, char_id, section)  # type: ignore[arg-type]
    return p.read_text(encoding="utf-8") if p.is_file() else None


def char_storysets(kb: KB, char_id: str) -> list[StorySetLink]:
    p = paths.char_storysets_path(kb.root, char_id)
    if not p.is_file():
        return []
    return [StorySetLink(**row) for row in read_json(p)]


# --- cross-references --------------------------------------------------


def _det_appearance(char_id: str, row: dict) -> Appearance:
    return Appearance(
        char_id=char_id,
        event_id=row["event_id"],
        stage_idx=row["stage_idx"],
        source="deterministic",
        story_set_name=row.get("story_set_name"),
    )


def _inf_appearance(char_id: str, row: dict) -> Appearance:
    return Appearance(
        char_id=char_id,
        event_id=row["event_id"],
        stage_idx=row["stage_idx"],
        source="inferred",
        count=row.get("count"),
        match_class=row.get("match_class"),
    )


def char_appearances(
    kb: KB, char_id: str, source: SourceFilter = "both"
) -> list[Appearance]:
    out: list[Appearance] = []
    if source in ("deterministic", "both"):
        out.extend(
            _det_appearance(char_id, r)
            for r in kb.char_to_events_deterministic.get(char_id, [])
        )
    if source in ("inferred", "both"):
        out.extend(
            _inf_appearance(char_id, r)
            for r in kb.char_to_events_inferred.get(char_id, [])
        )
    out.sort(key=lambda a: (a.event_id, a.stage_idx, a.source))
    return out


def event_chars(
    kb: KB, event_id: str, source: SourceFilter = "both"
) -> list[Appearance]:
    rows = kb.event_to_chars.get(event_id, [])
    out: list[Appearance] = []
    for row in rows:
        if source != "both" and row["source"] != source:
            continue
        out.append(
            Appearance(
                char_id=row["char_id"],
                event_id=event_id,
                stage_idx=row["stage_idx"],
                source=row["source"],
                count=row.get("count"),
                match_class=row.get("match_class"),
                story_set_name=row.get("story_set_name"),
            )
        )
    return out


def stage_chars(
    kb: KB,
    event_id: str,
    stage_idx: int,
    source: SourceFilter = "both",
) -> list[Appearance]:
    """Tight scope: chars whose edge points at *this exact* stage."""
    return [a for a in event_chars(kb, event_id, source) if a.stage_idx == stage_idx]


def group_by_event(appearances: Iterable[Appearance]) -> dict[str, list[Appearance]]:
    """Caller-side rollup: one entry per event_id with all appearances
    that point at that event."""
    out: dict[str, list[Appearance]] = {}
    for a in appearances:
        out.setdefault(a.event_id, []).append(a)
    return out


# --- free-text search --------------------------------------------------


def grep_text(
    kb: KB,
    pattern: str,
    scope: GrepScope = "all",
    *,
    regex: bool = False,
    snippet_len: int = 200,
) -> list[Match]:
    """Literal substring by default; opt into regex via `regex=True`.

    The fallback path is hit hardest by names that break naive regex
    (parens / hyphens / smart quotes in NPC and group names) — literal
    is the only safe default.
    """
    if scope not in GREP_SCOPES:
        raise ValueError(f"scope must be one of {GREP_SCOPES}, got {scope!r}")
    if regex:
        rx = re.compile(pattern)

        def line_matches(line: str) -> bool:
            return rx.search(line) is not None

        def file_could_match(body: str) -> bool:
            return rx.search(body) is not None

    else:

        def line_matches(line: str) -> bool:
            return pattern in line

        def file_could_match(body: str) -> bool:
            return pattern in body

    out: list[Match] = []
    if scope in ("events", "all"):
        for eid, ev in kb.event_manifests.items():
            event_dir = paths.event_dir(kb.root, eid)
            for s in ev["stages"]:
                p = event_dir / s["file"]
                if not p.is_file():
                    continue
                body = p.read_text(encoding="utf-8")
                if not file_could_match(body):
                    continue
                for i, line in enumerate(body.splitlines(), start=1):
                    if line_matches(line):
                        out.append(
                            Match(
                                event_id=eid,
                                stage_idx=s["idx"],
                                char_id=None,
                                section=None,
                                line=line,
                                line_no=i,
                                snippet=line[:snippet_len],
                            )
                        )
    if scope in ("chars", "all"):
        for cid in kb.char_manifests:
            cdir = paths.char_dir(kb.root, cid)
            for sec in SECTIONS:
                p = cdir / f"{sec}.txt"
                if not p.is_file():
                    continue
                body = p.read_text(encoding="utf-8")
                if not file_could_match(body):
                    continue
                for i, line in enumerate(body.splitlines(), start=1):
                    if line_matches(line):
                        out.append(
                            Match(
                                event_id=None,
                                stage_idx=None,
                                char_id=cid,
                                section=sec,
                                line=line,
                                line_no=i,
                                snippet=line[:snippet_len],
                                source="char_section",
                            )
                        )
    if scope in ("summaries", "all") and kb.summaries_root is not None:
        sum_events_dir = kb.summaries_root / "events"
        if sum_events_dir.is_dir():
            for p in sorted(sum_events_dir.glob("*.md")):
                eid = p.stem
                body = p.read_text(encoding="utf-8")
                if not file_could_match(body):
                    continue
                for i, line in enumerate(body.splitlines(), start=1):
                    if line_matches(line):
                        out.append(
                            Match(
                                event_id=eid,
                                stage_idx=None,
                                char_id=None,
                                section=None,
                                line=line,
                                line_no=i,
                                snippet=line[:snippet_len],
                                source="event_summary",
                            )
                        )
    return out


def get_card(kb: KB, char_id: str) -> dict | None:
    """Read the deterministic fact card for a char, or `None` if absent
    (e.g. an old build, or a char with no handbook entry)."""
    return read_json_or(paths.char_card_path(kb.root, char_id), None)


def event_stages(kb: KB, event_id: str) -> list[dict] | None:
    """Per-chapter listing for one event (idx / name / avgTag / length /
    file / story_txt), straight from the event manifest — so an agent can
    target one `<章节>` instead of reading the whole event."""
    ev = kb.event_manifests.get(event_id)
    if ev is None:
        return None
    return [
        {
            "idx": s["idx"],
            "name": s["name"],
            "avgTag": s.get("avgTag"),
            "length": s.get("length"),
            "file": s.get("file"),
            "story_txt": s.get("story_txt"),
        }
        for s in ev.get("stages", [])
    ]


# --- event summaries (LLM-derived; populated by Phase 5) --------------


def get_event_summary(kb: KB, event_id: str) -> str | None:
    if kb.summaries_root is None:
        return None
    p = paths.event_summary_path(kb.summaries_root, event_id)
    return p.read_text(encoding="utf-8") if p.is_file() else None
