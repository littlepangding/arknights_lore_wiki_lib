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
    _print_json(query.event_chars(kb, args.event_id, source=args.source))
    return 0


def cmd_event_stage_chars(args: argparse.Namespace) -> int:
    kb = _load(args)
    _print_json(
        query.stage_chars(kb, args.event_id, args.stage_idx, source=args.source)
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
    _print_json(query.char_appearances(kb, char_id, source=args.source))
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
    p.add_argument("--source", choices=list(query.SOURCE_FILTERS), default="both")
    p.set_defaults(fn=cmd_event_chars)

    p = g_event_sub.add_parser(
        "stage_chars", help="List char appearances for one stage of an event."
    )
    _add_common(p)
    p.add_argument("event_id")
    p.add_argument("stage_idx", type=int)
    p.add_argument("--source", choices=list(query.SOURCE_FILTERS), default="both")
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
    p.add_argument("--source", choices=list(query.SOURCE_FILTERS), default="both")
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
