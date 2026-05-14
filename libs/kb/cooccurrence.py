"""Deterministic char-pair co-occurrence (no LLM).

Aggregates the three WS-0 char↔stage edge layers
(`event_to_chars` merged index, ``deterministic`` ∪ ``participant`` ∪
``summary``) into unordered char pairs with two cardinalities:

- ``co_stage_count`` — distinct ``(event_id, stage_idx)`` cells where
  both chars hold an edge at the same stage. The high-signal metric:
  shared scene presence at chapter granularity.
- ``co_event_count`` — distinct ``event_id``s where both chars appear
  somewhere in the event, including via event-scoped summary edges
  (``stage_idx is None``). Superset of ``co_stage_count``.

The min-tier gate (default ``named``) excludes lone ``mentioned``
participant edges before pairing, matching :func:`query.char_appearances`'s
default. Deterministic storyset edges always pass (ground truth, no
tier).

Output is ``data/kb/cooccurrence.jsonl`` — sorted by ``(a, b)``,
gitignored, regenerable from the WS-0 layers in seconds. Consumed by
``kb_query relations cooccur …`` and (next) the typed-relation bake's
candidate list. The typed-relation table lives in
:mod:`libs.kb.relations` and is empty until a future LLM pass populates
it.

Identity note: today this is strictly char-pair (operator entities).
When the relation bake adds non-operator entity nodes, the same shape
extends — operator rows already carry ``id == char_id``, so a future
cooccurrence pass over entity-resolved edges would emit
``ent_<6hex>`` ids without schema change.
"""

from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from libs.kb._io import atomic_write_text
from libs.kb.participants import Tier, tier_at_least


def _passes(row: dict, min_tier: Tier) -> bool:
    """Mirror :func:`query._passes` for a raw ``event_to_chars`` row.
    Deterministic edges always clear ``--min-tier`` (no tier on them;
    they are ground truth)."""
    if row["source"] == "deterministic":
        return True
    return tier_at_least(row.get("tier"), min_tier)


def build_cooccurrence(
    event_to_chars: dict[str, list[dict]],
    *,
    min_tier: Tier = "named",
    sample_event_limit: int = 3,
) -> list[dict]:
    """Build the deterministic co-occurrence rows.

    A pair `(a, b)` appears if both chars hold an edge in any of the
    same events (event-level), and ``co_stage_count`` counts the subset
    where they share an explicit ``stage_idx``. ``a < b`` lexicographically;
    rows sorted by ``(a, b)``.

    ``sample_event_limit`` caps ``sample_events`` so the JSONL line
    stays bounded for pairs that co-star in dozens of events; the agent
    follows the hook back into ``event chars`` for the full list.
    """
    cell_chars: dict[tuple[str, int], set[str]] = defaultdict(set)
    event_chars: dict[str, set[str]] = defaultdict(set)
    for eid, rows in event_to_chars.items():
        for r in rows:
            if not _passes(r, min_tier):
                continue
            cid = r["char_id"]
            event_chars[eid].add(cid)
            sidx = r.get("stage_idx")
            if sidx is not None:
                cell_chars[(eid, sidx)].add(cid)

    pair_stage_cells: dict[tuple[str, str], set[tuple[str, int]]] = defaultdict(set)
    for (eid, sidx), chars in cell_chars.items():
        for a, b in combinations(sorted(chars), 2):
            pair_stage_cells[(a, b)].add((eid, sidx))

    pair_events: dict[tuple[str, str], set[str]] = defaultdict(set)
    for eid, chars in event_chars.items():
        for a, b in combinations(sorted(chars), 2):
            pair_events[(a, b)].add(eid)

    out: list[dict] = []
    for (a, b) in sorted(pair_events):
        events = pair_events[(a, b)]
        out.append(
            {
                "a": a,
                "b": b,
                "co_stage_count": len(pair_stage_cells.get((a, b), set())),
                "co_event_count": len(events),
                "sample_events": sorted(events)[:sample_event_limit],
            }
        )
    return out


# --- I/O ---------------------------------------------------------------


def write_cooccurrence_jsonl(path: Path, rows: list[dict]) -> None:
    """Atomic JSONL write — one row per line, trailing newline. Same
    pattern as :func:`entities.write_entities_jsonl`."""
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    atomic_write_text(path, body)


def load_cooccurrence(path: Path) -> list[dict]:
    """Read a JSONL cooccurrence file. Returns ``[]`` if missing — a
    pre-P-D-relations build loads cleanly with no cooccurrence data."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            out.append(json.loads(s))
    return out


# --- in-memory query helpers ------------------------------------------


def cooccurrence_for(rows: list[dict], char_id: str, limit: int | None = None) -> list[dict]:
    """All rows touching ``char_id``, sorted by ``co_stage_count`` then
    ``co_event_count`` descending (most-coupled first). ``limit`` caps
    the result; ``None`` returns the full list."""
    hits = [r for r in rows if r["a"] == char_id or r["b"] == char_id]
    hits.sort(key=lambda r: (-r["co_stage_count"], -r["co_event_count"], r["a"], r["b"]))
    if limit is not None:
        return hits[:limit]
    return hits


def cooccurrence_top(rows: list[dict], limit: int = 50) -> list[dict]:
    """Highest co-occurrence pairs across the whole corpus. Same sort
    key as :func:`cooccurrence_for`."""
    ordered = sorted(
        rows,
        key=lambda r: (-r["co_stage_count"], -r["co_event_count"], r["a"], r["b"]),
    )
    return ordered[:limit]


def cooccurrence_between(rows: list[dict], a: str, b: str) -> dict | None:
    """One row for the (a, b) pair, or ``None`` if they never co-occur.
    Normalises argument order so the caller need not sort."""
    lo, hi = (a, b) if a < b else (b, a)
    for r in rows:
        if r["a"] == lo and r["b"] == hi:
            return r
    return None
