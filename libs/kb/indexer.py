"""Pure-code (no LLM) builders for the `data/kb/indexes/*.json` files.

Reads from `data/kb/{events,chars}/<id>/` on disk; writes to
`data/kb/indexes/`. Two passes: deterministic edges from each char's
`storysets.json` (cheap, exact, 372/372 verified linkage), then inferred
edges from substring grep over the per-stage text chunks (recall floor;
subtractive against the deterministic `(char_id, event_id)` pairs so we
never duplicate edges that the deterministic pass already nailed).

The class-aware match floor is the load-bearing piece of the inferred
pass. Single-char zh operator names (`陈`, `年`, `夕`, ...) live in
class A with no length floor — a 2-char floor across the board would
silently drop 23 operators. Curated aliases keep the 2-char floor
because they are the more noise-prone source.

The inferred pass reads each stage chunk **once** and greps every
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


def _build_alias_inputs(
    char_manifests: dict[str, dict],
    curated: dict[str, list[str]] | None,
    ambiguous: set[str],
) -> dict[str, list[tuple[str, MatchClass]]]:
    """Per-char grep aliases, deduped by text.

    22 operators in the live corpus have `name == appellation` (`W`,
    `Sharp`, `Stormeye`, `Pith`, `Touch`, ...); without per-text
    dedup, every mention would be counted twice in the inferred
    `count`. When the same text appears in multiple alias sources
    (e.g. canonical name vs. curated alias), the higher-precedence
    class wins.

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


def build_char_to_events_inferred(
    kb_root: Path,
    char_manifests: dict[str, dict],
    deterministic: dict[str, list[dict]],
    *,
    curated: dict[str, list[str]] | None = None,
    ambiguous_canonicals: set[str] | None = None,
    event_manifests: dict[str, dict] | None = None,
) -> dict[str, list[dict]]:
    """Substring-grep each char's grep-aliases against per-stage chunks.

    Reads each stage file once, greps all char aliases against it (so
    the cost scales with stages, not stages × chars). Aggregates per
    `(char_id, stage_idx)` — one row per stage, with `count` summed
    across that char's aliases and `match_class` set to the
    highest-precision class that fired.

    Subtraction rule applies at the `(char_id, event_id)` pair level:
    if the deterministic pass already linked a char to an event in
    *any* stage, every inferred row in that event for that char is
    suppressed.
    """
    if ambiguous_canonicals is None:
        ambiguous_canonicals = compute_ambiguous_canonicals(char_manifests)
    if event_manifests is None:
        event_manifests = load_event_manifests(kb_root)

    deterministic_pairs: set[tuple[str, str]] = {
        (cid, link["event_id"])
        for cid, links in deterministic.items()
        for link in links
    }

    per_char = _build_alias_inputs(char_manifests, curated, ambiguous_canonicals)

    # cid -> eid -> stage_idx -> {count, match_class}
    agg: dict[str, dict[str, dict[int, dict]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for eid, ev in event_manifests.items():
        event_dir = paths.event_dir(kb_root, eid)
        # Pre-narrow chars whose deterministic pair already covers this event.
        active = [
            (cid, aliases)
            for cid, aliases in per_char.items()
            if (cid, eid) not in deterministic_pairs
        ]
        if not active:
            continue
        for stage in ev["stages"]:
            stage_path = event_dir / stage["file"]
            try:
                body = stage_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            for cid, aliases in active:
                stage_count = 0
                stage_best_mc: MatchClass | None = None
                for alias, mc in aliases:
                    n = body.count(alias)
                    if n == 0:
                        continue
                    stage_count += n
                    if (
                        stage_best_mc is None
                        or _MATCH_CLASS_PRECEDENCE[mc] > _MATCH_CLASS_PRECEDENCE[stage_best_mc]
                    ):
                        stage_best_mc = mc
                if stage_count > 0:
                    agg[cid][eid][stage["idx"]] = {
                        "count": stage_count,
                        "match_class": stage_best_mc,
                    }

    out: dict[str, list[dict]] = {}
    for cid in sorted(agg):
        rows: list[dict] = []
        for eid in sorted(agg[cid]):
            for sidx in sorted(agg[cid][eid]):
                row = agg[cid][eid][sidx]
                rows.append(
                    {
                        "event_id": eid,
                        "stage_idx": sidx,
                        "count": row["count"],
                        "match_class": row["match_class"],
                    }
                )
        out[cid] = rows
    return out


def build_event_to_chars(
    deterministic: dict[str, list[dict]],
    inferred: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Flat one-row-per-(char_id, stage_idx). Both source layers share
    the same row shape so consumers don't case-split."""
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
    for cid, hits in inferred.items():
        for hit in hits:
            by_event[hit["event_id"]].append(
                {
                    "char_id": cid,
                    "source": "inferred",
                    "stage_idx": hit["stage_idx"],
                    "count": hit["count"],
                    "match_class": hit["match_class"],
                }
            )
    out: dict[str, list[dict]] = {}
    for eid in sorted(by_event):
        rows = by_event[eid]
        rows.sort(key=lambda r: (r["char_id"], r["stage_idx"], r["source"]))
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
    inferred: dict[str, list[dict]],
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
                "has_inferred_appearances": bool(inferred.get(cid)),
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
) -> dict:
    """Read existing event/char manifests + storysets, build every
    index, write `kb_root/indexes/*.json`. Returns a small summary
    dict suitable for the build report."""
    event_manifests = load_event_manifests(kb_root)
    char_manifests = load_char_manifests(kb_root)
    curated = (
        parse_curated_alias_file(curated_aliases_path) if curated_aliases_path else None
    )
    ambiguous = compute_ambiguous_canonicals(char_manifests)

    events_by_family = build_events_by_family(event_manifests)
    deterministic = build_char_to_events_deterministic(kb_root, char_manifests)
    inferred = build_char_to_events_inferred(
        kb_root,
        char_manifests,
        deterministic,
        curated=curated,
        ambiguous_canonicals=ambiguous,
        event_manifests=event_manifests,
    )
    event_to_chars = build_event_to_chars(deterministic, inferred)
    stage_table = build_stage_table(event_manifests)
    char_table = build_char_table(char_manifests, inferred)

    atomic_write_json(paths.index_path(kb_root, "events_by_family"), events_by_family)
    atomic_write_json(
        paths.index_path(kb_root, "char_to_events_deterministic"), deterministic
    )
    atomic_write_json(
        paths.index_path(kb_root, "char_to_events_inferred"), inferred
    )
    atomic_write_json(paths.index_path(kb_root, "event_to_chars"), event_to_chars)
    atomic_write_json(paths.index_path(kb_root, "stage_table"), stage_table)
    atomic_write_json(paths.index_path(kb_root, "char_table"), char_table)
    # Always rewrite char_alias.json (even in raw-only mode) so a
    # prior enriched build's curated entries don't survive a later
    # raw-only rebuild and silently leak into resolver output.
    alias_index = build_char_alias_index(char_manifests, curated=curated)
    atomic_write_json(paths.index_path(kb_root, "char_alias"), alias_index)

    return {
        "events": len(event_manifests),
        "chars": len(char_manifests),
        "deterministic_chars_with_edges": sum(1 for v in deterministic.values() if v),
        "inferred_chars_with_edges": sum(1 for v in inferred.values() if v),
        "ambiguous_canonicals": sorted(ambiguous),
        "curated_alias_canonicals": (len(curated) if curated else 0),
    }
