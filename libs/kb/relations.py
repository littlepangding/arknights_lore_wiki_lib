"""Typed relation network (skeleton — no LLM bake yet).

This module is the *load/query* half of the P-D relation network. The
*build* half — an LLM pass that reads each char's handbook plus the
chapters they appear in and emits typed assertions — will land in a
follow-up commit (``scripts/kb_relations.py``) once the stage-summary
bake has finished. Keeping the load/query surface ready now lets
:func:`query.load_kb` and the ``kb_query relations …`` CLI degrade
cleanly while the file is absent: queries return ``[]`` rather than
crashing.

Row shape (one JSON object per line in ``data/kb/relations.jsonl``)::

    {
      "head": "char_002_amiya" | "ent_<6hex>",   # entity id, required
      "type": "<relation_type>",                   # free string for now
      "tail": "char_xxx" | "ent_<6hex>",           # entity id, required
      "source_event_ids": ["main_12", ...],        # provenance, optional
      "notes": "...",                              # human context, optional
      "confidence": "high" | "medium" | "low"      # set by the bake, optional
    }

The ``type`` vocabulary is intentionally unfrozen at this layer — when
the bake lands it will commit a controlled list (probably ``member_of``,
``ally_of``, ``creator_of``, ``identifies_as``, ``aka``, ...). Until
then, the module accepts any string so it doesn't pre-commit to a
typology that hasn't faced live data.

Provenance pointing at ``source_event_ids`` (not stage ids) keeps the
relation table stable across the bake's incremental progress: an
assertion mined from one chapter remains valid when more chapters get
baked. The originating stage indices live in the relation bake's
working file, not in ``relations.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path

from libs.kb._io import atomic_write_text


def _ensure_row(row: dict) -> dict:
    """Light validation. Hard-required keys produce a ``ValueError`` —
    callers (the future bake script, tests) get a useful error instead
    of a malformed line on disk. Optional keys are passed through."""
    for k in ("head", "type", "tail"):
        v = row.get(k)
        if not isinstance(v, str) or not v:
            raise ValueError(f"relations row missing/empty {k!r}: {row!r}")
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
