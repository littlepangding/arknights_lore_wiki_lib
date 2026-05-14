"""Deterministic entity layer (no LLM).

An *entity* is anything with a stable identity that lore points at:

- **Operators** — `entity_type="operator"`, `id` is the `char_id`. One
  row per `chars/<char_id>/manifest.json`. Aliases are the operator's
  `name` + `appellation` + any curated `char_alias.txt` lines (when the
  display name is unambiguous, mirroring `build_alias_inputs`).
- **Named non-operators** — `entity_type ∈ {npc, organization,
  location, group}`, `id` is `ent_<6hex>` from a sha256 of the
  canonical name (mirrors `get_simple_filename`'s sha fallback).
  Source: a curated override file at
  `<lore_wiki_path>/data/entities_curated.jsonl`. These are how 绩 /
  颉 / 神农 / 罗德岛 / 叙拉古 enter the graph — as real nodes, not
  failed operator resolutions.
- **Uncurated leftovers** — `entity_type="unknown"`, `id=ent_<6hex>`.
  Auto-seeded from `<关键人物>` surface names that no alias resolved.
  The caller supplies the unresolved-name map (already accumulated by
  `participants.build_char_to_events_summary`) — this module does not
  re-walk `kb_summaries/`. They give the curator a concrete punch
  list; a later curation line in `entities_curated.jsonl` promotes
  one to a typed entity.

What is *not* an entity: a `name:null` hint (e.g. "年口中会做饭的弟弟").
Hints stay `null` on a relation edge — never invent an id for them.

Output is `data/kb/entities.jsonl` (one JSON object per line; gitignored,
regenerable from game data + curated files). Row shape:

    {
      "id": "char_002_amiya" | "ent_<6hex>",
      "name": "<canonical name>",
      "entity_type": "operator" | "npc" | "organization" | "location"
                    | "group" | "unknown",
      "char_id": "<char_id>" | null,
      "appellation": "<codename>" | null,
      "aliases": ["<surface>", ...],
      "sources": ["character_table" | "char_alias.txt"
                  | "entities_curated.jsonl"
                  | "kb_summaries:<关键人物>", ...],
      // optional: "notes" (curated), "first_event_ids" (auto-seeded)
    }

Operator precedence: a curated entry whose `name` is already an
operator alias is dropped with a warning — the operator already covers
it. An auto-seed whose `name` is already a curated entity is dropped
silently (curation wins).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from libs.kb._io import atomic_write_text, invert_alias_lists

EntityType = Literal[
    "operator", "npc", "organization", "location", "group", "unknown"
]
ENTITY_TYPES: tuple[EntityType, ...] = (
    "operator", "npc", "organization", "location", "group", "unknown",
)
# Curated entries may declare any of these. `operator` is reserved for
# rows seeded from `character_table` — a curator can't manually mint one.
_NON_OPERATOR_TYPES: frozenset[str] = frozenset(ENTITY_TYPES) - {"operator"}


def synthetic_entity_id(name: str) -> str:
    """`ent_<6hex>` derived from sha256(utf-8(name)). Mirrors the
    `get_simple_filename` sha-fallback shape so the id space feels
    consistent. 6 hex = 16.7M space; collisions are negligible for the
    hundreds of named NPCs we'll ever seed."""
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
    return f"ent_{h}"


# --- operator rows -----------------------------------------------------


def _operator_aliases(
    mf: dict,
    curated_for_operators: dict[str, list[str]] | None,
    ambiguous_canonicals: set[str],
) -> list[str]:
    """`name` + `appellation` + curated aliases (only when the display
    name is unambiguous; otherwise attaching to a single owner is
    arbitrary — same rule as `build_alias_inputs`)."""
    seen: set[str] = set()
    out: list[str] = []
    for x in (mf.get("name"), mf.get("appellation")):
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    if curated_for_operators:
        nm = mf.get("name")
        if nm and nm not in ambiguous_canonicals:
            for alias in curated_for_operators.get(nm, []):
                if alias and alias not in seen:
                    seen.add(alias)
                    out.append(alias)
    return out


def build_operator_entities(
    char_manifests: dict[str, dict],
    curated_aliases: dict[str, list[str]] | None,
    ambiguous_canonicals: set[str],
) -> list[dict]:
    """One entity row per char. `sources` records whether the curated
    alias file contributed aliases to this row (so a downstream auditor
    can tell whether raw or enriched data backed the resolution)."""
    out: list[dict] = []
    for cid in sorted(char_manifests):
        mf = char_manifests[cid]
        aliases = _operator_aliases(mf, curated_aliases, ambiguous_canonicals)
        sources = ["character_table"]
        if (
            curated_aliases
            and mf.get("name") in curated_aliases
            and mf.get("name") not in ambiguous_canonicals
        ):
            sources.append("char_alias.txt")
        out.append(
            {
                "id": cid,
                "name": mf.get("name"),
                "entity_type": "operator",
                "char_id": cid,
                "appellation": mf.get("appellation"),
                "aliases": aliases,
                "sources": sources,
            }
        )
    return out


# --- curated overrides --------------------------------------------------


def parse_curated_entities_file(
    path: Path,
) -> tuple[list[dict], list[dict]]:
    """Parse `entities_curated.jsonl`. Returns `(entries, errors)`.

    A broken line is collected into `errors` (with `line_no` + reason)
    instead of killing the build — same posture as
    `parse_curated_alias_file` for `char_alias.txt`. Blank lines and
    `#`-comment lines are skipped.
    """
    entries: list[dict] = []
    errors: list[dict] = []
    if not path.is_file():
        return entries, errors
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append({"line_no": i, "raw": raw, "reason": f"invalid JSON: {e}"})
            continue
        if not isinstance(row, dict):
            errors.append({"line_no": i, "raw": raw, "reason": "not a JSON object"})
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append({"line_no": i, "raw": raw, "reason": "missing/empty `name`"})
            continue
        etype = row.get("entity_type", "unknown")
        if etype not in _NON_OPERATOR_TYPES:
            errors.append(
                {
                    "line_no": i,
                    "raw": raw,
                    "reason": (
                        f"entity_type {etype!r} not in "
                        f"{sorted(_NON_OPERATOR_TYPES)} "
                        "(operator rows come from character_table, not curation)"
                    ),
                }
            )
            continue
        entries.append({**row, "name": name.strip(), "entity_type": etype})
    return entries, errors


def build_curated_entities(
    curated_entries: list[dict],
    alias_to_char_ids: dict[str, list[str]],
) -> tuple[list[dict], list[dict]]:
    """Curated entries → entity rows. A curated entry whose `name` is
    already an operator alias is dropped with a warning (operator wins,
    by the same precedence rule the resolver uses today)."""
    rows: list[dict] = []
    warnings: list[dict] = []
    seen_ids: set[str] = set()
    for entry in curated_entries:
        name = entry["name"]
        if name in alias_to_char_ids:
            warnings.append(
                {
                    "name": name,
                    "reason": "matches an operator alias — curated entry dropped",
                    "operator_candidates": list(alias_to_char_ids[name]),
                }
            )
            continue
        eid = entry.get("id") or synthetic_entity_id(name)
        if eid in seen_ids:
            warnings.append(
                {
                    "name": name,
                    "reason": f"id collision on {eid}; later entry shadows earlier",
                }
            )
        seen_ids.add(eid)
        aliases_in = entry.get("aliases") or []
        seen_al: set[str] = set()
        deduped: list[str] = []
        for a in [name, *aliases_in]:
            if isinstance(a, str) and a and a not in seen_al:
                seen_al.add(a)
                deduped.append(a)
        row = {
            "id": eid,
            "name": name,
            "entity_type": entry["entity_type"],
            "char_id": None,
            "appellation": entry.get("appellation"),
            "aliases": deduped,
            "sources": ["entities_curated.jsonl"],
        }
        if entry.get("notes"):
            row["notes"] = entry["notes"]
        rows.append(row)
    return rows, warnings


# --- auto-seeding from caller-supplied unresolved map ----------------


def invert_unresolved_by_event(
    unresolved_by_event: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Flip `participants.build_char_to_events_summary`'s
    `event_id -> [names]` output into the `name -> [event_ids]` view
    `build_entities` consumes. One pass over the existing accumulator;
    avoids re-reading any `kb_summaries/` files."""
    out: dict[str, set[str]] = defaultdict(set)
    for eid, names in unresolved_by_event.items():
        for n in names:
            out[n].add(eid)
    return {n: sorted(ev) for n, ev in sorted(out.items())}


def build_auto_seeded_entities(
    unresolved: dict[str, list[str]],
    curated_names: set[str],
    existing_ids: set[str],
) -> list[dict]:
    """Promote each unresolved-name to an `entity_type="unknown"` entry,
    *unless* curation already covers it. `existing_ids` is consulted but
    not mutated — local de-dup tracks new auto-seed ids so two unresolved
    names that hash to the same `ent_<6hex>` don't both land."""
    rows: list[dict] = []
    minted: set[str] = set()
    for name, evids in unresolved.items():
        if name in curated_names:
            continue
        eid = synthetic_entity_id(name)
        if eid in existing_ids or eid in minted:
            # A curated entity with a different `name` happened to hash
            # to the same id, or a previous auto-seed already claimed
            # it. Drop the duplicate; the seen entity wins.
            continue
        minted.add(eid)
        rows.append(
            {
                "id": eid,
                "name": name,
                "entity_type": "unknown",
                "char_id": None,
                "appellation": None,
                "aliases": [name],
                "sources": ["kb_summaries:<关键人物>"],
                # Cap to a handful so the JSONL line doesn't blow up on
                # names that appear in dozens of summaries.
                "first_event_ids": evids[:5],
            }
        )
    return rows


# --- top-level builder + I/O ------------------------------------------


def build_entities(
    char_manifests: dict[str, dict],
    *,
    alias_to_char_ids: dict[str, list[str]],
    curated_aliases: dict[str, list[str]] | None = None,
    ambiguous_canonicals: set[str] | None = None,
    curated_entities_path: Path | None = None,
    unresolved_summary_names: dict[str, list[str]] | None = None,
) -> dict:
    """Build the full entity list. Returns a summary dict with the
    sorted `entities` plus counts/errors/warnings for the build report.

    `unresolved_summary_names` is the `{name -> [event_ids]}` view of
    `<关键人物>` surface names that didn't resolve through any alias.
    Pass `invert_unresolved_by_event(...)` over the dict already
    returned by `participants.build_char_to_events_summary` — that's
    cheaper than re-walking `kb_summaries/`.

    Sort order: operators first (by `char_id`), then non-operators by
    id. Stable across rebuilds since ids are deterministic."""
    ambiguous_canonicals = ambiguous_canonicals or set()
    unresolved = unresolved_summary_names or {}

    operator_rows = build_operator_entities(
        char_manifests, curated_aliases, ambiguous_canonicals
    )
    existing_ids: set[str] = {r["id"] for r in operator_rows}

    if curated_entities_path is not None:
        curated_entries, curated_errors = parse_curated_entities_file(
            curated_entities_path
        )
    else:
        curated_entries, curated_errors = [], []
    curated_rows, curated_warnings = build_curated_entities(
        curated_entries, alias_to_char_ids
    )
    for r in curated_rows:
        existing_ids.add(r["id"])

    curated_names = {r["name"] for r in curated_rows}
    auto_rows = build_auto_seeded_entities(unresolved, curated_names, existing_ids)

    entities = sorted(
        operator_rows + curated_rows + auto_rows,
        key=lambda r: (0 if r["entity_type"] == "operator" else 1, r["id"]),
    )

    return {
        "entities": entities,
        "operator_count": len(operator_rows),
        "curated_count": len(curated_rows),
        "auto_seeded_count": len(auto_rows),
        "curated_errors": curated_errors,
        "curated_warnings": curated_warnings,
        "unresolved_summary_name_count": len(unresolved),
    }


def write_entities_jsonl(path: Path, entities: list[dict]) -> None:
    """Atomic JSONL write — one row per line, trailing newline."""
    body = "\n".join(json.dumps(e, ensure_ascii=False) for e in entities) + "\n"
    atomic_write_text(path, body)


def load_entities(path: Path) -> list[dict]:
    """Read a JSONL entities file. Returns `[]` if missing (rather than
    raising) so a `kb_query` invocation against a pre-P-D build degrades
    cleanly to the empty list."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            out.append(json.loads(s))
    return out


def build_entity_alias_index(entities: list[dict]) -> dict[str, list[str]]:
    """`alias -> [entity_id, ...]`. Multi-target rows encode ambiguity
    (`暮落` → both operators; same shape as the operator alias index).
    Used by `resolve_entity` to return Resolved | Ambiguous | Missing."""
    return invert_alias_lists(entities, id_field="id")
