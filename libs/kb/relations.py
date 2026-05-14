"""Typed relation network — load / query / collation.

Three artifacts:

* ``kb_relations/chars/<char_id>.jsonl`` — per-char LLM bake output
  (tracked in git). Written by :mod:`libs.kb.relations_bake` via
  ``scripts/kb_relations.py``.
* ``<lore_wiki_path>/data/relations_curated.jsonl`` — optional curated
  override (same row shape; the curator pins assertions the bake
  missed or hallucinated).
* ``data/kb/relations.jsonl`` — collated view; gitignored. Built by
  ``kb_build`` from the two above; consumed by ``kb_query relations …``.

Row shape (one JSON object per line)::

    {
      "head": "char_002_amiya" | "ent_<6hex>",     # entity id, required
      "type": "member_of" | "ally_of" | ...,       # see RELATION_TYPES in relations_bake
      "tail": "char_xxx" | "ent_<6hex>" | null,    # entity id; null when ambiguous
      "tail_name": "<surface name>",               # the LLM's emitted surface
      "ambiguous_candidates": ["char_a", ...],     # only when tail is null
      "notes": "...",                              # short context (≤30字 by prompt)
      "source": "bake" | "curated"                 # added at collation
    }

``tail`` is allowed to be ``null`` so an ambiguous-tail assertion isn't
silently dropped — `tail_name` carries the surface and the curator can
disambiguate later by adding an entry to ``entities_curated.jsonl`` and
re-baking. Missing-tail assertions (no alias matches at all) are
dropped at bake time with a warning in the bake report instead of
landing here.

The ``type`` field at this layer is a free string. The bake's prompt
hints :data:`relations_bake.RELATION_TYPES` (9 starter types); novel
types from the LLM aren't rejected — they're flagged in bake warnings
so a curator can decide whether to extend the vocabulary, but the
assertion is kept.
"""

from __future__ import annotations

import json
from pathlib import Path

from libs.kb._io import atomic_write_text


def _ensure_row(row: dict) -> dict:
    """Light validation. `head` and `type` must be non-empty strings.
    `tail` is required as a key but may be `null` — that's how an
    ambiguous-tail assertion (`ambiguous_candidates` set) is preserved
    rather than silently dropped; `tail_name` carries the surface in
    that case. Callers (the bake, tests) get a useful error instead of
    a malformed line on disk."""
    for k in ("head", "type"):
        v = row.get(k)
        if not isinstance(v, str) or not v:
            raise ValueError(f"relations row missing/empty {k!r}: {row!r}")
    if "tail" not in row:
        raise ValueError(f"relations row missing key 'tail': {row!r}")
    tail = row["tail"]
    if tail is not None and (not isinstance(tail, str) or not tail):
        raise ValueError(f"relations row 'tail' must be str or null: {row!r}")
    return row


# --- I/O ---------------------------------------------------------------


def write_relations_jsonl(path: Path, rows: list[dict]) -> None:
    """Atomic JSONL write — same pattern as the entities + cooccurrence
    modules. Each row is validated; a malformed row aborts the write
    before anything lands on disk."""
    validated = [_ensure_row(r) for r in rows]
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in validated) + "\n"
    atomic_write_text(path, body)


def load_relations(path: Path) -> list[dict]:
    """Read a JSONL relations file. Returns ``[]`` if missing — the
    common case until the bake runs. Malformed lines raise so a corrupt
    file fails loud rather than silently truncating the graph."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            out.append(_ensure_row(json.loads(s)))
    return out


# --- in-memory query helpers ------------------------------------------


def relations_for(rows: list[dict], entity_id: str) -> list[dict]:
    """All relations touching ``entity_id`` (matched against either
    ``head`` or ``tail``). Order preserved from the source file so an
    agent sees the bake's emission order."""
    return [r for r in rows if r["head"] == entity_id or r["tail"] == entity_id]


def relations_between(
    rows: list[dict], a: str, b: str, *, directed: bool = False
) -> list[dict]:
    """Relations between two entities. By default *undirected* —
    matches ``head=a tail=b`` and ``head=b tail=a``. ``directed=True``
    keeps only ``head=a tail=b``. Multiple types between the same pair
    return as multiple rows (e.g. ``member_of`` + ``identifies_as``).
    """
    out: list[dict] = []
    for r in rows:
        if r["head"] == a and r["tail"] == b:
            out.append(r)
        elif not directed and r["head"] == b and r["tail"] == a:
            out.append(r)
    return out


def list_relation_types(rows: list[dict]) -> list[str]:
    """Distinct ``type`` values present in the table, sorted. Useful
    for `kb_query relations list --type` autocompletion and for the
    bake's curated-override review (knowing which types exist)."""
    return sorted({r["type"] for r in rows})


# --- per-char file I/O + collation -----------------------------------


def load_char_relations_file(path: Path) -> list[dict]:
    """Read a single ``kb_relations/chars/<char_id>.jsonl``. Same
    `_ensure_row` validation as the collated load — a malformed bake
    output fails loud rather than silently truncating one char's
    relations."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            out.append(_ensure_row(json.loads(s)))
    return out


def parse_curated_relations_file(
    path: Path,
) -> tuple[list[dict], list[dict]]:
    """Read ``<wiki>/data/relations_curated.jsonl``. Same posture as
    :func:`entities.parse_curated_entities_file` — broken lines land
    in `errors` instead of killing `kb_build`. `#` comment lines and
    blank lines are skipped."""
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
        try:
            entries.append(_ensure_row(row))
        except ValueError as e:
            errors.append({"line_no": i, "raw": raw, "reason": str(e)})
    return entries, errors


def _row_dedup_key(row: dict) -> tuple[str, str, str | None, str | None]:
    """Identity key for collation dedup: same (head, type, tail) — and
    `tail_name` when `tail` is null so two ambiguous-tail rows pointing
    at different surfaces aren't collapsed."""
    return (row["head"], row["type"], row.get("tail"), row.get("tail_name"))


def collate_relations(
    relations_root: Path,
    curated_path: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """Walk ``kb_relations/chars/*.jsonl``, append the curated override
    file, dedup by (head, type, tail, tail_name) with **curated winning**.
    Returns `(rows, curated_errors)`.

    A curated entry with the same key as a baked row replaces it — the
    curator's hand-edits override the LLM. Sort is stable by `(head,
    type, tail)` so the collated file diffs cleanly across rebuilds.
    """
    seen: dict[tuple, dict] = {}
    chars_root = relations_root / "chars"
    if chars_root.is_dir():
        for p in sorted(chars_root.glob("*.jsonl")):
            for row in load_char_relations_file(p):
                seen[_row_dedup_key(row)] = {**row, "source": "bake"}

    curated_errors: list[dict] = []
    if curated_path is not None:
        curated_entries, curated_errors = parse_curated_relations_file(curated_path)
        for row in curated_entries:
            seen[_row_dedup_key(row)] = {**row, "source": "curated"}

    rows = sorted(
        seen.values(),
        key=lambda r: (r["head"], r["type"], r.get("tail") or "", r.get("tail_name") or ""),
    )
    return rows, curated_errors
