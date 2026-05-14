"""Path helpers and the source-family classifier for the KB layer.

`source_family` is the single source of truth for the four-way family axis;
both build and indexer depend on it staying in sync. Other helpers take an
explicit `kb_root` / `summaries_root` so tests can point them at tmp dirs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from libs.bases import get_simple_filename


KB_DIRNAME = "data/kb"
SUMMARIES_DIRNAME = "kb_summaries"
RELATIONS_DIRNAME = "kb_relations"

Family = Literal["mainline", "activity", "mini_activity", "operator_record", "other"]
FAMILIES: tuple[Family, ...] = (
    "mainline",
    "activity",
    "mini_activity",
    "operator_record",
    "other",
)

Section = Literal["profile", "voice", "archive", "skins", "modules"]
SECTIONS: tuple[Section, ...] = (
    "profile",
    "voice",
    "archive",
    "skins",
    "modules",
)

MatchClass = Literal["canonical", "canonical_short", "curated", "fuzzy"]


def default_kb_root() -> Path:
    return Path.cwd() / KB_DIRNAME


def default_summaries_root() -> Path:
    return Path.cwd() / SUMMARIES_DIRNAME


def default_relations_root() -> Path:
    return Path.cwd() / RELATIONS_DIRNAME


def safe_slug(s: str) -> str:
    """Filesystem-safe slug. Hashes to 6 hex chars when input has zh / punct."""
    return get_simple_filename(s)


def _normalize_story_txt(story_txt: str | None) -> str:
    if story_txt is None:
        return ""
    return story_txt.replace("\\", "/").lstrip("/")


def source_family(story_txt: str, entry_type: str) -> Family:
    """Classify an event into one of the four content families (or `other`).

    `entryType=MAINLINE` is checked first because `main_0` (the prologue)
    has its first stage under `obt/guide/beg/0_welcome_to_guide` — a
    prefix-only rule would route it to `other` even though it is mainline.
    """
    if entry_type == "MAINLINE":
        return "mainline"
    norm = _normalize_story_txt(story_txt)
    if norm.startswith("obt/main/"):
        return "mainline"
    if norm.startswith("obt/memory/"):
        return "operator_record"
    if norm.startswith("activities/"):
        if entry_type == "ACTIVITY":
            return "activity"
        if entry_type == "MINI_ACTIVITY":
            return "mini_activity"
    return "other"


def story_txt_prefix(story_txt: str) -> str:
    """First two path segments of a storyTxt, e.g. `obt/main` or `activities/act46side`."""
    parts = _normalize_story_txt(story_txt).split("/")
    return "/".join(parts[:2]) if parts != [""] else ""


# --- KB path helpers -------------------------------------------------------


def kb_manifest_path(kb_root: Path) -> Path:
    return kb_root / "manifest.json"


def events_root(kb_root: Path) -> Path:
    return kb_root / "events"


def event_dir(kb_root: Path, event_id: str) -> Path:
    return events_root(kb_root) / event_id


def event_json_path(kb_root: Path, event_id: str) -> Path:
    return event_dir(kb_root, event_id) / "event.json"


def stage_filename(stage_idx: int, name: str, avg_tag: str | None) -> str:
    """`stage_<NN>_<slug>.txt`. `avg_tag` is mixed into the slug so `_beg`
    and `_end` chapters that share a `storyName` don't collide on disk."""
    base = name if not avg_tag else f"{name}_{avg_tag}"
    return f"stage_{stage_idx:02d}_{safe_slug(base)}.txt"


def stage_path(kb_root: Path, event_id: str, stage_idx: int, name: str, avg_tag: str | None) -> Path:
    return event_dir(kb_root, event_id) / stage_filename(stage_idx, name, avg_tag)


def chars_root(kb_root: Path) -> Path:
    return kb_root / "chars"


def char_dir(kb_root: Path, char_id: str) -> Path:
    return chars_root(kb_root) / char_id


def char_manifest_path(kb_root: Path, char_id: str) -> Path:
    return char_dir(kb_root, char_id) / "manifest.json"


def char_section_path(kb_root: Path, char_id: str, section: Section) -> Path:
    return char_dir(kb_root, char_id) / f"{section}.txt"


def char_storysets_path(kb_root: Path, char_id: str) -> Path:
    return char_dir(kb_root, char_id) / "storysets.json"


def char_card_path(kb_root: Path, char_id: str) -> Path:
    return char_dir(kb_root, char_id) / "card.json"


def indexes_root(kb_root: Path) -> Path:
    return kb_root / "indexes"


def index_path(kb_root: Path, name: str) -> Path:
    """`name` is the bare basename (e.g. 'events_by_family'); '.json' is appended."""
    return indexes_root(kb_root) / f"{name}.json"


def entities_jsonl_path(kb_root: Path) -> Path:
    """The deterministic entity table — one JSON object per line. Built
    by `kb_build` from `character_table` + the curated overrides file +
    unresolved `<关键人物>` names; consumed by `kb_query entity …` and
    (later) the relation network / audit passes."""
    return kb_root / "entities.jsonl"


def cooccurrence_jsonl_path(kb_root: Path) -> Path:
    """Deterministic char-pair co-occurrence — one row per unordered
    `(a, b)`. Built by `kb_build` from the merged WS-0 `event_to_chars`
    index; consumed by `kb_query relations cooccur …` and (later) the
    relation bake's candidate-pair list."""
    return kb_root / "cooccurrence.jsonl"


def relations_jsonl_path(kb_root: Path) -> Path:
    """Typed relation table — one assertion per line. Populated by a
    future LLM bake (`scripts/kb_relations.py`); until then the file is
    absent and `load_relations` returns `[]`."""
    return kb_root / "relations.jsonl"


def curated_entities_path(wiki_path: Path) -> Path:
    """`<lore_wiki_path>/data/entities_curated.jsonl` — hand-curated
    non-operator entity overrides (named NPCs, organizations, ...).
    Sibling of the curated `char_alias.txt`."""
    return Path(wiki_path) / "data" / "entities_curated.jsonl"


# --- summaries path helpers -----------------------------------------------


def event_summary_path(summaries_root: Path, event_id: str) -> Path:
    return summaries_root / "events" / f"{event_id}.md"


def stages_summary_root(summaries_root: Path) -> Path:
    return summaries_root / "stages"


def event_stages_summary_dir(summaries_root: Path, event_id: str) -> Path:
    return stages_summary_root(summaries_root) / event_id


def stage_summary_path(summaries_root: Path, event_id: str, stage_idx: int) -> Path:
    return event_stages_summary_dir(summaries_root, event_id) / f"{stage_idx:02d}.md"


def summaries_manifest_path(summaries_root: Path) -> Path:
    return summaries_root / "manifest.json"


# --- relations bake path helpers ------------------------------------------


def relations_chars_root(relations_root: Path) -> Path:
    """`kb_relations/chars/` — one JSONL per char, tracked in git (LLM
    outputs). The collated query view lives at `data/kb/relations.jsonl`,
    derived by `kb_build` from these per-char files plus the curated
    override."""
    return relations_root / "chars"


def char_relations_path(relations_root: Path, char_id: str) -> Path:
    return relations_chars_root(relations_root) / f"{char_id}.jsonl"


def relations_manifest_path(relations_root: Path) -> Path:
    return relations_root / "manifest.json"


def curated_relations_path(wiki_path: Path) -> Path:
    """`<lore_wiki_path>/data/relations_curated.jsonl` — hand-curated
    relation overrides (same shape as a bake row; sibling of
    `entities_curated.jsonl`). Lets a curator pin assertions the LLM
    bake missed or hallucinated."""
    return Path(wiki_path) / "data" / "relations_curated.jsonl"
