"""CLI wrapper around `libs.kb.query`. JSON-printing by default; pass
`--text` on text-returning commands (`event stage`, `char get`, `summary
event`) to dump the raw chunk instead.

Run from the lib repo root, e.g.:

    .venv/bin/python -m scripts.kb_query event list --family activity
    .venv/bin/python -m scripts.kb_query char resolve 阿米娅
    .venv/bin/python -m scripts.kb_query grep 圣巡

Designed to be called by an agent: every JSON output is one line per
record-shaped command (or one object for "get"-shaped commands), and
errors go to stderr with a non-zero exit code.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from libs.kb import paths, query
from libs.kb.entities import ENTITY_TYPES
from libs.kb.paths import FAMILIES


def _serialize(obj: Any) -> Any:
    """Convert dataclasses + tuples to plain JSON-serializable values."""
    if dataclasses.is_dataclass(obj):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _print_json(obj: Any) -> None:
    print(json.dumps(_serialize(obj), ensure_ascii=False, indent=2))


def _load(args: argparse.Namespace) -> query.KB:
    kb_root = Path(args.kb_root) if args.kb_root else paths.default_kb_root()
    summaries: Path | None = (
        Path(args.summaries_root)
        if args.summaries_root
        else paths.default_summaries_root()
    )
    if summaries is not None and not summaries.is_dir():
        # Optional: Phase 5 has not produced summaries yet on this machine.
        summaries = None
    if not kb_root.is_dir():
        sys.exit(
            f"kb_query: kb root {kb_root} not found — run `python -m scripts.kb_build` first"
        )
    return query.load_kb(kb_root, summaries)


def cmd_event_list(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.list_events(kb, family=args.family))
    return 0


def cmd_event_get(args: argparse.Namespace) -> int:
    kb = _load(args)
    ev = query.get_event(kb, args.event_id)
    if ev is None:
        print(f"kb_query: event {args.event_id!r} not found", file=sys.stderr)
        return 1
    _print_json(ev)
    return 0


def cmd_event_chars(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(
        query.event_chars(
            kb, args.event_id, source=args.source, min_tier=args.min_tier
        )
    )
    return 0


def cmd_event_stage_chars(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(
        query.stage_chars(
            kb,
            args.event_id,
            args.stage_idx,
            source=args.source,
            min_tier=args.min_tier,
        )
    )
    return 0


def cmd_event_stages(args: argparse.Namespace) -> int:
    kb = _load(args)
    stages = query.event_stages(kb, args.event_id)
    if stages is None:
        print(f"kb_query: event {args.event_id!r} not found", file=sys.stderr)
        return 1
    _print_json(stages)
    return 0


def cmd_event_stage(args: argparse.Namespace) -> int:
    kb = _load(args)
    text = query.get_stage_text(kb, args.event_id, args.stage_idx)
    if text is None:
        print(
            f"kb_query: stage {args.event_id}/{args.stage_idx} not found",
            file=sys.stderr,
        )
        return 1
    if args.text:
        sys.stdout.write(text)
        return 0
    stage = query.get_stage_meta(kb, args.event_id, args.stage_idx) or {}
    _print_json(
        {
            "event_id": args.event_id,
            "stage_idx": args.stage_idx,
            "name": stage.get("name"),
            "avgTag": stage.get("avgTag"),
            "length": len(text),
            "text": text,
        }
    )
    return 0


def cmd_family_list(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.list_families(kb))
    return 0


def cmd_char_resolve(args: argparse.Namespace) -> int:
    kb = _load(args)
    res = query.resolve_operator_name(kb, args.name)
    _print_json(res)
    return 0 if res.kind != "missing" else 1


def _resolve_char_id_or_name(kb: query.KB, ident: str) -> str | None:
    """Accept a char_id, canonical name, or curated alias. Prints to
    stderr and returns None when the input is unknown or ambiguous, so
    the agent-facing `<char_id_or_name>` arg in AGENTS_GUIDE doesn't
    silently turn unresolved names into empty result lists."""
    if ident in kb.char_manifests:
        return ident
    res = query.resolve_operator_name(kb, ident)
    if res.kind == "resolved":
        return res.char_id
    if res.kind == "ambiguous":
        print(
            f"kb_query: {ident!r} is ambiguous — candidates: "
            f"{list(res.candidates)}; pass a char_id to disambiguate",
            file=sys.stderr,
        )
        return None
    print(
        f"kb_query: {ident!r} is not a known char_id or operator name; "
        "try `char resolve` or `grep`",
        file=sys.stderr,
    )
    return None


def cmd_char_get(args: argparse.Namespace) -> int:
    kb = _load(args)
    char_id = _resolve_char_id_or_name(kb, args.char_id)
    if char_id is None:
        return 1
    text = query.get_char_section(kb, char_id, args.section)
    if args.text:
        sys.stdout.write(text or "")
        return 0
    _print_json(
        {
            "char_id": char_id,
            "section": args.section,
            "manifest": kb.char_manifests[char_id],
            "text": text,
        }
    )
    return 0


def cmd_char_appearances(args: argparse.Namespace) -> int:
    kb = _load(args)
    char_id = _resolve_char_id_or_name(kb, args.char_id)
    if char_id is None:
        return 1
    _print_json(
        query.char_appearances(
            kb, char_id, source=args.source, min_tier=args.min_tier
        )
    )
    return 0


def cmd_char_storysets(args: argparse.Namespace) -> int:
    kb = _load(args)
    char_id = _resolve_char_id_or_name(kb, args.char_id)
    if char_id is None:
        return 1
    _print_json(query.char_storysets(kb, char_id))
    return 0


def cmd_char_card(args: argparse.Namespace) -> int:
    kb = _load(args)
    char_id = _resolve_char_id_or_name(kb, args.char_id)
    if char_id is None:
        return 1
    card = query.get_card(kb, char_id)
    if card is None:
        print(
            f"kb_query: no fact card for {char_id!r} "
            "(rebuild with `python -m scripts.kb_build`)",
            file=sys.stderr,
        )
        return 1
    _print_json(card)
    return 0


def cmd_grep(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.grep_text(kb, args.pattern, scope=args.scope, regex=args.regex))
    return 0


def cmd_entity_resolve(args: argparse.Namespace) -> int:
    kb = _load(args)
    res = query.resolve_entity(kb, args.name)
    _print_json(res)
    return 0 if res.kind != "missing" else 1


def cmd_entity_list(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.list_entities(kb, entity_type=args.type))
    return 0


def cmd_entity_get(args: argparse.Namespace) -> int:
    kb = _load(args)
    ent = query.get_entity(kb, args.entity_id)
    if ent is None:
        print(
            f"kb_query: no entity with id {args.entity_id!r} "
            "(try `entity resolve <name>` or `entity list`)",
            file=sys.stderr,
        )
        return 1
    # For non-operators a hand-curated dossier may exist under
    # `kb_curated/chars/<entity_id>/` (v1: ent_76be2e for 博士). Operators
    # have no entries there — their section data is on the char side.
    section = getattr(args, "section", None)
    if section is None:
        _print_json(ent)
        return 0
    text = query.get_entity_section(args.entity_id, section)
    if args.text:
        sys.stdout.write(text or "")
        return 0
    _print_json({"entity_id": args.entity_id, "section": section, "row": ent, "text": text})
    return 0


def cmd_entity_appearances(args: argparse.Namespace) -> int:
    kb = _load(args)
    if kb.entities_by_id.get(args.entity_id) is None:
        print(
            f"kb_query: no entity with id {args.entity_id!r} "
            "(try `entity resolve <name>` or `entity list`)",
            file=sys.stderr,
        )
        return 1
    _print_json(query.entity_appearances(kb, args.entity_id))
    return 0


def cmd_relations_cooccur_for(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.cooccurrence_for_char(kb, args.char_id, limit=args.limit))
    return 0


def cmd_relations_cooccur_top(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.cooccurrence_top(kb, limit=args.limit))
    return 0


def cmd_relations_cooccur_between(args: argparse.Namespace) -> int:
    kb = _load(args)
    row = query.cooccurrence_between(kb, args.a, args.b)
    if row is None:
        print(
            f"kb_query: no co-occurrence between {args.a!r} and {args.b!r}",
            file=sys.stderr,
        )
        return 1
    _print_json(row)
    return 0


def cmd_relations_for(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.relations_for_entity(kb, args.entity_id))
    return 0


def cmd_relations_between(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(
        query.relations_between_entities(kb, args.a, args.b, directed=args.directed)
    )
    return 0


def cmd_relations_list(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(query.list_relations(kb, type_filter=args.type))
    return 0


def cmd_summary_event(args: argparse.Namespace) -> int:
    kb = _load(args)
    text = query.get_event_summary(kb, args.event_id)
    if text is None:
        print(
            f"kb_query: no summary for event {args.event_id!r} "
            "(Phase 5 produces these; if you ran kb_summarize, "
            "check kb_summaries/events/)",
            file=sys.stderr,
        )
        return 1
    if args.text:
        sys.stdout.write(text)
        return 0
    _print_json({"event_id": args.event_id, "text": text})
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--kb-root",
        default="",
        help=f"KB root. Defaults to ./{paths.KB_DIRNAME}.",
    )
    p.add_argument(
        "--summaries-root",
        default="",
        help=f"LLM summaries root. Defaults to ./{paths.SUMMARIES_DIRNAME}.",
    )


def _add_edge_filters(p: argparse.ArgumentParser) -> None:
    """`--source` (which char↔stage edge layer) + `--min-tier` (how
    strongly a participant edge must hold). `deterministic`/storyset
    edges always pass `--min-tier`. Default `--min-tier named` keeps
    speaker + named, drops lone `mentioned` hits."""
    p.add_argument(
        "--source",
        choices=list(query.SOURCE_FILTERS),
        default="all",
        help="deterministic | participant | summary | all (default all).",
    )
    p.add_argument(
        "--min-tier",
        choices=list(query.TIERS),
        default=query.DEFAULT_MIN_TIER,
        help="speaker | named | mentioned (default named).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb_query", description=__doc__)
    sub = parser.add_subparsers(dest="group", required=True)

    g_event = sub.add_parser("event", help="Event browsing.")
    g_event_sub = g_event.add_subparsers(dest="cmd", required=True)

    p = g_event_sub.add_parser("list", help="List events; optional --family filter.")
    _add_common(p)
    p.add_argument("--family", choices=list(FAMILIES), default=None)
    p.set_defaults(fn=cmd_event_list)

    p = g_event_sub.add_parser("get", help="Get one event's metadata.")
    _add_common(p)
    p.add_argument("event_id")
    p.set_defaults(fn=cmd_event_get)

    p = g_event_sub.add_parser(
        "chars", help="List char appearances across all stages of an event."
    )
    _add_common(p)
    p.add_argument("event_id")
    _add_edge_filters(p)
    p.set_defaults(fn=cmd_event_chars)

    p = g_event_sub.add_parser(
        "stage_chars", help="List char appearances for one stage of an event."
    )
    _add_common(p)
    p.add_argument("event_id")
    p.add_argument("stage_idx", type=int)
    _add_edge_filters(p)
    p.set_defaults(fn=cmd_event_stage_chars)

    p = g_event_sub.add_parser(
        "stages", help="List one event's chapters (idx / name / avgTag / length)."
    )
    _add_common(p)
    p.add_argument("event_id")
    p.set_defaults(fn=cmd_event_stages)

    p = g_event_sub.add_parser(
        "stage", help="Read one stage chunk; --text prints raw, JSON otherwise."
    )
    _add_common(p)
    p.add_argument("event_id")
    p.add_argument("stage_idx", type=int)
    p.add_argument("--text", action="store_true")
    p.set_defaults(fn=cmd_event_stage)

    g_family = sub.add_parser("family", help="Family-level summaries.")
    g_family_sub = g_family.add_subparsers(dest="cmd", required=True)
    p = g_family_sub.add_parser("list", help="Family -> event count.")
    _add_common(p)
    p.set_defaults(fn=cmd_family_list)

    g_char = sub.add_parser("char", help="Operator data + cross-references.")
    g_char_sub = g_char.add_subparsers(dest="cmd", required=True)

    p = g_char_sub.add_parser("resolve", help="Resolve a name/alias.")
    _add_common(p)
    p.add_argument("name")
    p.set_defaults(fn=cmd_char_resolve)

    p = g_char_sub.add_parser("get", help="Read a section file (or all).")
    _add_common(p)
    p.add_argument("char_id")
    p.add_argument("--section", choices=list(query.SECTIONS_OR_ALL), default="all")
    p.add_argument("--text", action="store_true")
    p.set_defaults(fn=cmd_char_get)

    p = g_char_sub.add_parser(
        "appearances", help="List a char's appearances across the corpus."
    )
    _add_common(p)
    p.add_argument("char_id")
    _add_edge_filters(p)
    p.set_defaults(fn=cmd_char_appearances)

    p = g_char_sub.add_parser(
        "storysets", help="Deterministic handbook-storyset links for a char."
    )
    _add_common(p)
    p.add_argument("char_id")
    p.set_defaults(fn=cmd_char_storysets)

    p = g_char_sub.add_parser(
        "card", help="Deterministic fact card (basics / 客观履历 / skins / modules)."
    )
    _add_common(p)
    p.add_argument("char_id")
    p.set_defaults(fn=cmd_char_card)

    p = sub.add_parser("grep", help="Literal substring search; --regex opts in.")
    _add_common(p)
    p.add_argument("pattern")
    p.add_argument(
        "--in", dest="scope", choices=list(query.GREP_SCOPES), default="all"
    )
    p.add_argument("--regex", action="store_true")
    p.set_defaults(fn=cmd_grep)

    g_entity = sub.add_parser(
        "entity",
        help="Entity layer (operators + curated NPCs + auto-seeded unknowns).",
    )
    g_entity_sub = g_entity.add_subparsers(dest="cmd", required=True)

    p = g_entity_sub.add_parser(
        "resolve",
        help="Resolve a name/alias across every entity (broader than `char resolve`).",
    )
    _add_common(p)
    p.add_argument("name")
    p.set_defaults(fn=cmd_entity_resolve)

    p = g_entity_sub.add_parser(
        "list", help="Every entity row, optional --type filter."
    )
    _add_common(p)
    p.add_argument(
        "--type", choices=list(ENTITY_TYPES), default=None,
        help="Filter by entity_type (default: every type).",
    )
    p.set_defaults(fn=cmd_entity_list)

    p = g_entity_sub.add_parser(
        "get", help="Get one entity row by id (char_id for operators, ent_<6hex> else)."
    )
    _add_common(p)
    p.add_argument("entity_id")
    p.add_argument(
        "--section", choices=list(paths.ENTITY_SECTIONS_OR_ALL), default=None,
        help="Read the hand-curated dossier section (non-operator entities only; "
             "v1: ent_76be2e/博士). Omit for the bare entity row.",
    )
    p.add_argument("--text", action="store_true",
                   help="With --section, print raw section text instead of JSON.")
    p.set_defaults(fn=cmd_entity_get)

    p = g_entity_sub.add_parser(
        "appearances",
        help="Summary-source appearances for a non-operator entity "
             "(the only edge layer non-operators have in v1).",
    )
    _add_common(p)
    p.add_argument("entity_id")
    p.set_defaults(fn=cmd_entity_appearances)

    g_relations = sub.add_parser(
        "relations",
        help="Relation network: deterministic cooccur substrate + (later) typed assertions.",
    )
    g_rel_sub = g_relations.add_subparsers(dest="cmd", required=True)

    # Cooccurrence sub-tree — always populated by kb_build.
    p = g_rel_sub.add_parser(
        "cooccur",
        help="Deterministic char-pair co-occurrence (from WS-0 edges).",
    )
    p_cooccur_sub = p.add_subparsers(dest="cooccur_cmd", required=True)

    p2 = p_cooccur_sub.add_parser(
        "for", help="Pairs touching one char, most-coupled first."
    )
    _add_common(p2)
    p2.add_argument("char_id")
    p2.add_argument("--limit", type=int, default=20, help="Default 20.")
    p2.set_defaults(fn=cmd_relations_cooccur_for)

    p2 = p_cooccur_sub.add_parser("top", help="Top co-occurring pairs corpus-wide.")
    _add_common(p2)
    p2.add_argument("--limit", type=int, default=50, help="Default 50.")
    p2.set_defaults(fn=cmd_relations_cooccur_top)

    p2 = p_cooccur_sub.add_parser(
        "between", help="One pair's co-occurrence row, or non-zero exit if absent."
    )
    _add_common(p2)
    p2.add_argument("a")
    p2.add_argument("b")
    p2.set_defaults(fn=cmd_relations_cooccur_between)

    # Typed-relation sub-tree — empty list until the LLM bake runs.
    p = g_rel_sub.add_parser(
        "for", help="Typed relations touching one entity (empty pre-bake)."
    )
    _add_common(p)
    p.add_argument("entity_id")
    p.set_defaults(fn=cmd_relations_for)

    p = g_rel_sub.add_parser(
        "between", help="Typed relations between two entities (empty pre-bake)."
    )
    _add_common(p)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument(
        "--directed",
        action="store_true",
        help="Only `head=a, tail=b` (default matches both directions).",
    )
    p.set_defaults(fn=cmd_relations_between)

    p = g_rel_sub.add_parser(
        "list", help="Every typed relation, optional --type filter (empty pre-bake)."
    )
    _add_common(p)
    p.add_argument(
        "--type",
        default=None,
        help="Filter by relation type (free string; bake will define vocabulary).",
    )
    p.set_defaults(fn=cmd_relations_list)

    g_summary = sub.add_parser("summary", help="LLM summaries (Phase 5).")
    g_summary_sub = g_summary.add_subparsers(dest="cmd", required=True)
    p = g_summary_sub.add_parser("event", help="Read an event summary.")
    _add_common(p)
    p.add_argument("event_id")
    p.add_argument("--text", action="store_true")
    p.set_defaults(fn=cmd_summary_event)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
