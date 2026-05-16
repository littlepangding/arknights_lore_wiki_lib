"""Pure-code (no LLM) builders for the `data/kb/indexes/*.json` files.

Reads from `data/kb/{events,chars}/<id>/` on disk; writes to
`data/kb/indexes/`. Three char↔stage edge layers:

1. **deterministic** — from each char's `storysets.json` (cheap, exact,
   372/372 verified linkage). The ground truth; outranks everything.
2. **participant** — :mod:`libs.kb.participants`: per-stage, tiered
   (`speaker`/`named`/`mentioned`), built from the cleaned chunk text
   (speaker-line parsing + word-boundary-aware narration grep).
   Subtractive against the deterministic `(char_id, event_id, stage_idx)`
   triples so we never duplicate an edge the deterministic pass nailed.
3. **summary** — :mod:`libs.kb.participants`: event-scoped, from the
   `<关键人物>` tag of the baked `kb_summaries/events/<id>.md`. Hash-gated
   free (reads the already-baked `.md`; no LLM call). Subtractive against
   the deterministic `(char_id, event_id)` pairs.

The class-aware match floor (`classify_alias`) is the load-bearing
piece. Single-char zh operator names (`陈`, `年`, `夕`, ...) keep no
length floor — a 2-char floor across the board would silently drop 23
operators. Curated aliases keep the 2-char floor because they are the
more noise-prone source.

The participant pass reads each stage chunk **once** and greps every
char's aliases against the body. Reading per-char would be ~860K disk
reads at corpus scale (1937 stages × ~444 chars).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Literal

from libs.kb import paths
from libs.kb._io import (
    atomic_write_json,
    load_dir_manifests,
    read_json,
    read_json_or,
)
from libs.kb.paths import Family, FAMILIES, MatchClass


# Common-noun / role-name overlaps that would drown the inferred edges in
# noise. Mirrors the list in docs/PROMPTS.md "Blocklist for char-name grep".
BLOCKLIST: frozenset[str] = frozenset(
    {
        "博士",
        "干员",
        "罗德岛",
        "医疗",
        "近卫",
        "重装",
        "狙击",
        "术师",
        "辅助",
        "特种",
        "先锋",
        "源石",
        "矿石病",
    }
)


AliasSource = Literal["canonical", "curated", "fuzzy"]

# Best-precision wins when one (char_id, stage) is hit by aliases of
# multiple classes. `canonical` (multi-char operator name) is the most
# trustworthy; `canonical_short` (single zh char) is the most noisy and
# always loses if any other class also fires.
_MATCH_CLASS_PRECEDENCE: dict[MatchClass, int] = {
    "canonical": 4,
    "curated": 3,
    "canonical_short": 2,
    "fuzzy": 1,
}


# --- curated alias file parser -----------------------------------------


def parse_curated_alias_file(path: Path | str) -> dict[str, list[str]]:
    """Read `arknights_lore_wiki/data/char_alias.txt`.

    Returns `{canonical: [alias, ...]}`. The canonical itself (line[0])
    is the dict key and is *not* repeated in its alias list — callers
    that need both should treat the key as the first alias.

    Returns `{}` if the file is missing; the indexer treats that as
    raw-only mode (DESIGN.md "Aliases: where they come from").
    """
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, list[str]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        names = [v.strip() for v in line.split(";") if v.strip()]
        if not names:
            continue
        canonical = names[0]
        out.setdefault(canonical, []).extend(names[1:])
    for canonical, aliases in out.items():
        seen: set[str] = set()
        deduped: list[str] = []
        for a in aliases:
            if a and a not in seen:
                seen.add(a)
                deduped.append(a)
        out[canonical] = deduped
    return out


# --- on-disk loaders ----------------------------------------------------


def load_event_manifests(kb_root: Path) -> dict[str, dict]:
    return load_dir_manifests(paths.events_root(kb_root), "event.json")


def load_char_manifests(kb_root: Path) -> dict[str, dict]:
    return load_dir_manifests(paths.chars_root(kb_root), "manifest.json")


def load_char_storysets(kb_root: Path, char_id: str) -> list[dict]:
    return read_json_or(paths.char_storysets_path(kb_root, char_id), [])


# --- name → char_ids helpers (used by ambiguity + alias index + grep) -


def compute_name_to_char_ids(char_manifests: dict[str, dict]) -> dict[str, list[str]]:
    """`{display_name: [char_id, ...]}` — multi-target entries are the
    9 known duplicate-display-name cases (`暮落`, `郁金香`, `Sharp`,
    `Stormeye`, `Pith`, `Touch`, plus three `预备干员-*`)."""
    out: dict[str, list[str]] = defaultdict(list)
    for cid, mf in char_manifests.items():
        nm = mf.get("name")
        if nm:
            out[nm].append(cid)
    return dict(out)


def compute_ambiguous_canonicals(char_manifests: dict[str, dict]) -> set[str]:
    """Display names that map to >1 char_id — curated alias attachment
    is forbidden for these (picking one owner is arbitrary; attaching
    to all silently broadens scope). Surfaced via `Ambiguous` instead.
    """
    return {n for n, ids in compute_name_to_char_ids(char_manifests).items() if len(ids) > 1}


# --- alias classification --------------------------------------------


def classify_alias(text: str, source: AliasSource) -> MatchClass | None:
    """Apply the per-class length floor + blocklist. Returns the
    grep-pass `match_class`, or `None` if the alias should be dropped.

    Class A (canonical) keeps single-char names (`canonical_short`);
    B/C demand ≥2 chars. The single-char retention restores recall on
    23 single-zh-char operators (`陈`, `年`, `夕`, ...) that an
    indiscriminate length floor would silently drop.
    """
    if not text or text in BLOCKLIST:
        return None
    if source == "canonical":
        return "canonical_short" if len(text) == 1 else "canonical"
    if len(text) < 2:
        return None
    return source


# --- index builders ----------------------------------------------------


def build_events_by_family(event_manifests: dict[str, dict]) -> dict[Family, list[str]]:
    out: dict[Family, list[str]] = {f: [] for f in FAMILIES}
    for eid, ev in event_manifests.items():
        out[ev["source_family"]].append(eid)
    for fam in out:
        out[fam].sort()
    return out


def build_char_to_events_deterministic(
    kb_root: Path,
    char_manifests: dict[str, dict],
) -> dict[str, list[dict]]:
    """One row per linked storyset, sorted by `(event_id, stage_idx)`."""
    out: dict[str, list[dict]] = {}
    for cid in char_manifests:
        ss = load_char_storysets(kb_root, cid)
        if not ss:
            continue
        rows = [
            {
                "event_id": s["linked_event_id"],
                "stage_idx": s["linked_stage_idx"],
                "story_set_name": s["storySetName"],
            }
            for s in ss
        ]
        rows.sort(key=lambda r: (r["event_id"], r["stage_idx"]))
        out[cid] = rows
    return out


def build_alias_inputs(
    char_manifests: dict[str, dict],
    curated: dict[str, list[str]] | None,
    ambiguous: set[str],
) -> dict[str, list[tuple[str, MatchClass]]]:
    """Per-char grep aliases (`{char_id: [(surface, match_class), ...]}`),
    deduped by surface text.

    22 operators in the live corpus have `name == appellation` (`W`,
    `Sharp`, `Stormeye`, `Pith`, `Touch`, ...); without per-text dedup,
    every mention would be counted twice. When the same text appears in
    multiple alias sources (canonical name vs. curated alias), the
    higher-precedence class wins.

    Curated aliases are skipped when the char's `name` is ambiguous —
    attaching them to a single owner would be arbitrary; the resolver
    surfaces them via the multi-target `alias_to_char_ids` row instead.
    """
    out: dict[str, list[tuple[str, MatchClass]]] = {}
    for cid, mf in char_manifests.items():
        per_text: dict[str, MatchClass] = {}

        def consider(text: str | None, source: AliasSource) -> None:
            if not text:
                return
            mc = classify_alias(text, source)
            if mc is None:
                return
            existing = per_text.get(text)
            if (
                existing is None
                or _MATCH_CLASS_PRECEDENCE[mc] > _MATCH_CLASS_PRECEDENCE[existing]
            ):
                per_text[text] = mc

        consider(mf.get("name"), "canonical")
        consider(mf.get("appellation"), "canonical")
        if curated:
            name = mf.get("name")
            if name and name not in ambiguous:
                for alias in curated.get(name, []):
                    consider(alias, "curated")

        if per_text:
            out[cid] = list(per_text.items())
    return out


def build_event_to_chars(
    deterministic: dict[str, list[dict]],
    participant: dict[str, list[dict]],
    summary: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Flat per-event char rows merged from the three edge layers. Each
    row carries `char_id` + `source` + `stage_idx` (`None` for the
    event-scoped `summary` rows) plus the source-specific extras
    (`story_set_name` for deterministic; `tier`/`spoke_lines`/
    `mention_count`/`matched_aliases` for participant; `tier`/
    `matched_aliases` for summary). Sorted so a row with a real
    `stage_idx` sorts before the event-scoped `None`."""
    by_event: dict[str, list[dict]] = defaultdict(list)
    for cid, links in deterministic.items():
        for link in links:
            by_event[link["event_id"]].append(
                {
                    "char_id": cid,
                    "source": "deterministic",
                    "stage_idx": link["stage_idx"],
                    "story_set_name": link["story_set_name"],
                }
            )
    for layer in (participant, summary):
        for cid, rows in layer.items():
            for r in rows:
                by_event[r["event_id"]].append(
                    {"char_id": cid, **{k: v for k, v in r.items() if k != "event_id"}}
                )

    def _key(r: dict) -> tuple:
        sidx = r["stage_idx"]
        return (r["char_id"], 1 if sidx is None else 0, -1 if sidx is None else sidx, r["source"])

    out: dict[str, list[dict]] = {}
    for eid in sorted(by_event):
        rows = by_event[eid]
        rows.sort(key=_key)
        out[eid] = rows
    return out


def build_stage_table(event_manifests: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for eid, ev in event_manifests.items():
        for s in ev["stages"]:
            rows.append(
                {
                    "event_id": eid,
                    "stage_idx": s["idx"],
                    "name": s["name"],
                    "avgTag": s.get("avgTag"),
                    "source_family": ev["source_family"],
                    "storyTxt_prefix": paths.story_txt_prefix(s.get("story_txt", "")),
                    "file_path": f"events/{eid}/{s['file']}",
                    "length": s["length"],
                }
            )
    rows.sort(key=lambda r: (r["event_id"], r["stage_idx"]))
    return rows


def build_char_table(
    char_manifests: dict[str, dict],
    participant: dict[str, list[dict]],
) -> list[dict]:
    rows: list[dict] = []
    for cid, mf in char_manifests.items():
        rows.append(
            {
                "char_id": cid,
                "name": mf["name"],
                "nationId": mf.get("nationId"),
                "sections": list(mf.get("sections", [])),
                "storyset_count": mf.get("storyset_count", 0),
                "has_participant_appearances": bool(participant.get(cid)),
            }
        )
    rows.sort(key=lambda r: r["char_id"])
    return rows


def build_char_alias_index(
    char_manifests: dict[str, dict],
    curated: dict[str, list[str]] | None = None,
) -> dict:
    """Resolver lookup table: `alias -> [char_id, ...]`.

    Multi-target rows are how we encode ambiguity — `resolve_operator_name`
    collapses 1 row to `Resolved`, ≥2 rows to `Ambiguous`, 0 rows to
    `Missing`. Curated aliases whose canonical maps to multiple operators
    (the `暮落 / 沉渊` case) attach to *all* candidates.
    """
    alias_to_char_ids: dict[str, list[str]] = defaultdict(list)
    for cid, mf in char_manifests.items():
        for raw in (mf.get("name"), mf.get("appellation")):
            if raw and cid not in alias_to_char_ids[raw]:
                alias_to_char_ids[raw].append(cid)

    if curated:
        name_to_ids = compute_name_to_char_ids(char_manifests)
        for canonical, aliases in curated.items():
            owners = name_to_ids.get(canonical, [])
            if not owners:
                continue
            for alias in aliases:
                if not alias:
                    continue
                for cid in owners:
                    if cid not in alias_to_char_ids[alias]:
                        alias_to_char_ids[alias].append(cid)

    return {"alias_to_char_ids": dict(sorted(alias_to_char_ids.items()))}


# --- top-level orchestration ------------------------------------------


def build_all_indexes(
    kb_root: Path,
    *,
    curated_aliases_path: Path | str | None = None,
    summaries_root: Path | str | None = None,
    curated_entities_path: Path | str | None = None,
    relations_root: Path | str | None = None,
    curated_relations_path: Path | str | None = None,
) -> dict:
    """Read existing event/char manifests + storysets, build every
    index, write `kb_root/indexes/*.json` + `kb_root/entities.jsonl`.
    Returns a small summary dict suitable for the build report.

    `summaries_root` (default: none) points at the baked
    `kb_summaries/` so the event-scoped `summary` edge layer can read
    `<关键人物>`. Absent → that layer is empty (no LLM call either way).

    `curated_entities_path` (default: none) points at the optional
    `<wiki>/data/entities_curated.jsonl` non-operator override file.
    Absent → entities.jsonl contains operator rows + (when
    `summaries_root` is present) auto-seeded `unknown` placeholders
    from unresolved `<关键人物>` names."""
    # Local import: `participants` and `entities` both reach back into
    # this module, so importing them at module scope would be circular.
    # `cooccurrence` is fine module-scoped but co-located here for symmetry.
    from libs.kb import cooccurrence, entities, participants, relations

    event_manifests = load_event_manifests(kb_root)
    char_manifests = load_char_manifests(kb_root)
    curated = (
        parse_curated_alias_file(curated_aliases_path) if curated_aliases_path else None
    )
    ambiguous = compute_ambiguous_canonicals(char_manifests)
    sumroot = Path(summaries_root) if summaries_root else None

    events_by_family = build_events_by_family(event_manifests)
    deterministic = build_char_to_events_deterministic(kb_root, char_manifests)
    alias_index = build_char_alias_index(char_manifests, curated=curated)

    summary_edges, unresolved_summary = participants.build_char_to_events_summary(
        sumroot, alias_index["alias_to_char_ids"], deterministic
    )
    participant = participants.build_stage_participants(
        kb_root,
        char_manifests,
        deterministic,
        curated=curated,
        ambiguous_canonicals=ambiguous,
        event_manifests=event_manifests,
        summary_char_ids_by_event=participants.summary_char_ids_by_event(summary_edges),
    )
    event_to_chars = build_event_to_chars(deterministic, participant, summary_edges)
    stage_table = build_stage_table(event_manifests)
    char_table = build_char_table(char_manifests, participant)

    atomic_write_json(paths.index_path(kb_root, "events_by_family"), events_by_family)
    atomic_write_json(
        paths.index_path(kb_root, "char_to_events_deterministic"), deterministic
    )
    atomic_write_json(
        paths.index_path(kb_root, "char_to_events_participant"), participant
    )
    atomic_write_json(
        paths.index_path(kb_root, "char_to_events_summary"), summary_edges
    )
    atomic_write_json(paths.index_path(kb_root, "event_to_chars"), event_to_chars)
    atomic_write_json(paths.index_path(kb_root, "stage_table"), stage_table)
    atomic_write_json(paths.index_path(kb_root, "char_table"), char_table)
    # Always rewrite char_alias.json (even in raw-only mode) so a
    # prior enriched build's curated entries don't survive a later
    # raw-only rebuild and silently leak into resolver output.
    atomic_write_json(paths.index_path(kb_root, "char_alias"), alias_index)
    # Drop the pre-WS-0 index name if a stale copy is sitting around.
    paths.index_path(kb_root, "char_to_events_inferred").unlink(missing_ok=True)

    ent_curated = Path(curated_entities_path) if curated_entities_path else None
    # `unresolved_summary` is `{event_id: [names]}` from the participant
    # builder above — invert once for entity auto-seeding instead of
    # re-walking 2000+ summary files.
    ent_summary = entities.build_entities(
        char_manifests,
        alias_to_char_ids=alias_index["alias_to_char_ids"],
        curated_aliases=curated,
        ambiguous_canonicals=ambiguous,
        curated_entities_path=ent_curated,
        unresolved_summary_names=entities.invert_unresolved_by_event(
            unresolved_summary
        ),
    )
    entities.write_entities_jsonl(
        paths.entities_jsonl_path(kb_root), ent_summary["entities"]
    )

    cooccur_rows = cooccurrence.build_cooccurrence(event_to_chars)
    cooccurrence.write_cooccurrence_jsonl(
        paths.cooccurrence_jsonl_path(kb_root), cooccur_rows
    )

    rel_root = Path(relations_root) if relations_root else None
    rel_curated = Path(curated_relations_path) if curated_relations_path else None
    if rel_root is not None and rel_root.is_dir():
        relation_rows, relation_curated_errors = relations.collate_relations(
            rel_root, rel_curated
        )
    else:
        relation_rows, relation_curated_errors = [], []
    relations.write_relations_jsonl(
        paths.relations_jsonl_path(kb_root), relation_rows
    )

    return {
        "events": len(event_manifests),
        "chars": len(char_manifests),
        "events_by_family": events_by_family,
        "deterministic_link_count": sum(len(v) for v in deterministic.values()),
        "deterministic_chars_with_edges": sum(1 for v in deterministic.values() if v),
        "participant_chars_with_edges": sum(1 for v in participant.values() if v),
        "participant_edge_count": sum(len(v) for v in participant.values()),
        "summary_chars_with_edges": sum(1 for v in summary_edges.values() if v),
        "summary_edge_count": sum(len(v) for v in summary_edges.values()),
        "unresolved_summary_names": unresolved_summary,
        "ambiguous_canonicals": sorted(ambiguous),
        "curated_alias_canonicals": (len(curated) if curated else 0),
        "entity_count": len(ent_summary["entities"]),
        "entity_operator_count": ent_summary["operator_count"],
        "entity_curated_count": ent_summary["curated_count"],
        "entity_auto_seeded_count": ent_summary["auto_seeded_count"],
        "entity_curated_errors": ent_summary["curated_errors"],
        "entity_curated_warnings": ent_summary["curated_warnings"],
        "cooccurrence_pair_count": len(cooccur_rows),
        "cooccurrence_stage_total": sum(r["co_stage_count"] for r in cooccur_rows),
        "relation_count": len(relation_rows),
        "relation_bake_count": sum(1 for r in relation_rows if r.get("source") == "bake"),
        "relation_curated_count": sum(1 for r in relation_rows if r.get("source") == "curated"),
        "relation_curated_errors": relation_curated_errors,
    }
