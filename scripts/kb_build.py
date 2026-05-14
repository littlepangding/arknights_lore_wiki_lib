"""Build the deterministic KB layer (no LLM).

Reads raw game data from `ArknightsGameData/zh_CN/gamedata/`, writes
per-stage and per-character chunks under `data/kb/`, then builds every
JSON index under `data/kb/indexes/`. Optionally enriches the alias
index with `arknights_lore_wiki/data/char_alias.txt` when the wiki repo
is reachable.

Run from the lib repo root (so `keys.json` resolves):

    .venv/bin/python -m scripts.kb_build

Pruning is part of the build contract: any `events/<id>/` or
`chars/<id>/` directory left over from a previous build that is not in
the current upstream snapshot is removed unless `--no-prune` is set.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from libs import game_data
from libs.bases import try_get_value
from libs.kb import chunker, indexer, paths
from libs.kb._io import atomic_write_json
from libs.kb.paths import FAMILIES


def _read_data_version(game_data_path: str) -> str:
    p = Path(game_data_path) / "zh_CN" / "gamedata" / "excel" / "data_version.txt"
    if not p.is_file():
        return "unknown"
    return p.read_text(encoding="utf-8").strip()


def _clean_script_hash() -> str:
    """Short hash of `clean_script`'s source. Lets a future build detect
    that the parser changed and the cached chunks are stale."""
    src = inspect.getsource(game_data.clean_script)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]


def _resolve_curated_path(args: argparse.Namespace) -> Path | None:
    if args.curated_aliases:
        p = Path(args.curated_aliases).expanduser()
        if not p.is_file():
            print(
                f"warning: --curated-aliases {p} does not exist; "
                "building in raw-only mode",
                file=sys.stderr,
            )
            return None
        return p
    wiki_path = args.wiki_path or try_get_value("lore_wiki_path")
    if wiki_path:
        candidate = Path(wiki_path).expanduser() / "data" / "char_alias.txt"
        if candidate.is_file():
            return candidate
    return None


def _resolve_curated_entities_path(args: argparse.Namespace) -> Path | None:
    """Same lookup pattern as `_resolve_curated_path`, but for the
    optional `entities_curated.jsonl` non-operator entity overrides.
    Missing → only operator + auto-seeded rows land in entities.jsonl."""
    if args.curated_entities:
        p = Path(args.curated_entities).expanduser()
        if not p.is_file():
            print(
                f"warning: --curated-entities {p} does not exist; "
                "skipping curated non-operator entities",
                file=sys.stderr,
            )
            return None
        return p
    wiki_path = args.wiki_path or try_get_value("lore_wiki_path")
    if wiki_path:
        candidate = paths.curated_entities_path(Path(wiki_path).expanduser())
        if candidate.is_file():
            return candidate
    return None


def _prune_extra_dirs(parent: Path, keep: set[str]) -> list[str]:
    if not parent.is_dir():
        return []
    removed: list[str] = []
    for child in parent.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child)
            removed.append(child.name)
    return sorted(removed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-data-path", default="")
    parser.add_argument("--wiki-path", default="")
    parser.add_argument(
        "--curated-aliases",
        default="",
        help="Explicit path to char_alias.txt; overrides --wiki-path lookup.",
    )
    parser.add_argument(
        "--curated-entities",
        default="",
        help=(
            "Explicit path to entities_curated.jsonl (non-operator entity "
            "overrides); overrides --wiki-path lookup. Optional — when "
            "missing, entities.jsonl carries operator + auto-seeded rows only."
        ),
    )
    parser.add_argument(
        "--kb-root",
        default="",
        help=f"KB output root. Defaults to ./{paths.KB_DIRNAME}.",
    )
    parser.add_argument(
        "--summaries-root",
        default="",
        help=(
            f"Baked event summaries root. Defaults to ./{paths.SUMMARIES_DIRNAME} "
            "when it exists; the `summary` char↔event edge layer reads "
            "<关键人物> from there (no LLM call). Pass a non-dir / empty to skip."
        ),
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Do not remove events/chars dirs absent from the new build.",
    )
    args = parser.parse_args()

    game_data_path = args.game_data_path or try_get_value("game_data_path")
    if not game_data_path:
        parser.error(
            "game_data_path is not set: pass --game-data-path or fill keys.json"
        )
    kb_root = Path(args.kb_root) if args.kb_root else paths.default_kb_root()
    kb_root.mkdir(parents=True, exist_ok=True)

    if args.summaries_root:
        summaries_root: Path | None = Path(args.summaries_root)
    else:
        summaries_root = paths.default_summaries_root()
    if summaries_root is not None and not summaries_root.is_dir():
        summaries_root = None

    data_version = _read_data_version(game_data_path)
    clean_hash = _clean_script_hash()
    curated_path = _resolve_curated_path(args)
    curated_entities_path = _resolve_curated_entities_path(args)

    print(f"kb_build: game_data_path={game_data_path}")
    print(f"kb_build: kb_root={kb_root}")
    print(f"kb_build: data_version={data_version!r}")
    print(f"kb_build: clean_script_hash={clean_hash}")
    print(f"kb_build: curated_aliases={curated_path or '(none)'}")
    print(f"kb_build: curated_entities={curated_entities_path or '(none)'}")
    print(f"kb_build: summaries_root={summaries_root or '(none)'}")

    t0 = time.monotonic()

    story_review = game_data.extract_data_from_story_review_table(game_data_path)
    storytxt_index = chunker.build_storytxt_index(story_review)

    raw_chars, _ = game_data.get_all_char_info(game_data_path)
    # Pre-compute so chunker.write_char and the indexer see the same set;
    # both passes consistently exclude ambiguous canonicals from
    # curated-alias attachment.
    ambiguous_canonicals = indexer.compute_ambiguous_canonicals(raw_chars)

    curated = (
        indexer.parse_curated_alias_file(curated_path) if curated_path else None
    )

    written_event_ids = set(story_review)
    skipped_nameless = sorted(cid for cid, c in raw_chars.items() if not c.get("name"))
    written_char_ids = set(raw_chars) - set(skipped_nameless)

    for eid, ev in story_review.items():
        chunker.write_event(kb_root, game_data_path, eid, ev, data_version)
    print(f"kb_build: wrote {len(written_event_ids)} events")

    storyset_warnings: list[dict] = []
    for cid in written_char_ids:
        _, warns = chunker.write_char(
            kb_root,
            cid,
            raw_chars[cid],
            storytxt_index,
            curated_aliases=curated,
            ambiguous_canonicals=ambiguous_canonicals,
        )
        for w in warns:
            storyset_warnings.append({"char_id": cid, **w})
    print(
        f"kb_build: wrote {len(written_char_ids)} chars "
        f"(skipped {len(skipped_nameless)} nameless)"
    )

    pruned_events: list[str] = []
    pruned_chars: list[str] = []
    if not args.no_prune:
        # Run before the indexer so it doesn't index disappeared entities.
        pruned_events = _prune_extra_dirs(paths.events_root(kb_root), written_event_ids)
        pruned_chars = _prune_extra_dirs(paths.chars_root(kb_root), written_char_ids)
        if pruned_events:
            print(f"kb_build: pruned {len(pruned_events)} stale event dirs")
        if pruned_chars:
            print(f"kb_build: pruned {len(pruned_chars)} stale char dirs")

    print("kb_build: building indexes...")
    summary = indexer.build_all_indexes(
        kb_root,
        curated_aliases_path=curated_path,
        summaries_root=summaries_root,
        curated_entities_path=curated_entities_path,
    )

    family_counts = {f: len(summary["events_by_family"].get(f, [])) for f in FAMILIES}
    elapsed = time.monotonic() - t0

    unresolved_summary = summary["unresolved_summary_names"]
    unresolved_summary_total = sum(len(v) for v in unresolved_summary.values())
    manifest = {
        "version": 1,
        "build_timestamp": datetime.now(tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "source_data_version": data_version,
        "clean_script_hash": clean_hash,
        "curated_aliases_path": str(curated_path) if curated_path else None,
        "curated_entities_path": (
            str(curated_entities_path) if curated_entities_path else None
        ),
        "summaries_root": str(summaries_root) if summaries_root else None,
        "events": summary["events"],
        "chars": summary["chars"],
        "events_by_family": family_counts,
        "deterministic_link_count": summary["deterministic_link_count"],
        "deterministic_chars_with_edges": summary["deterministic_chars_with_edges"],
        "participant_chars_with_edges": summary["participant_chars_with_edges"],
        "participant_edge_count": summary["participant_edge_count"],
        "summary_chars_with_edges": summary["summary_chars_with_edges"],
        "summary_edge_count": summary["summary_edge_count"],
        "unresolved_summary_names": unresolved_summary,
        "ambiguous_canonicals": summary["ambiguous_canonicals"],
        "curated_alias_canonicals_loaded": summary["curated_alias_canonicals"],
        "entity_count": summary["entity_count"],
        "entity_operator_count": summary["entity_operator_count"],
        "entity_curated_count": summary["entity_curated_count"],
        "entity_auto_seeded_count": summary["entity_auto_seeded_count"],
        "entity_curated_errors": summary["entity_curated_errors"],
        "entity_curated_warnings": summary["entity_curated_warnings"],
        "cooccurrence_pair_count": summary["cooccurrence_pair_count"],
        "cooccurrence_stage_total": summary["cooccurrence_stage_total"],
        "skipped_nameless_char_ids": skipped_nameless,
        "storyset_warnings": storyset_warnings,
        "pruned_event_dirs": pruned_events,
        "pruned_char_dirs": pruned_chars,
    }
    atomic_write_json(paths.kb_manifest_path(kb_root), manifest)

    print()
    print(f"kb_build complete in {elapsed:.1f}s")
    print(f"  events: {summary['events']}")
    for fam in FAMILIES:
        print(f"    {fam}: {family_counts[fam]}")
    print(
        f"  chars: {summary['chars']} (skipped {len(skipped_nameless)} nameless)"
    )
    print(f"  deterministic edges: {summary['deterministic_link_count']}")
    print(
        f"  chars with deterministic edges: {summary['deterministic_chars_with_edges']}"
    )
    print(
        f"  participant edges: {summary['participant_edge_count']} "
        f"(over {summary['participant_chars_with_edges']} chars)"
    )
    print(
        f"  summary edges: {summary['summary_edge_count']} "
        f"(over {summary['summary_chars_with_edges']} chars)"
    )
    if unresolved_summary_total:
        print(
            f"  unresolved <关键人物> names: {unresolved_summary_total} "
            f"across {len(unresolved_summary)} events (see manifest)"
        )
    print(f"  ambiguous canonicals: {len(summary['ambiguous_canonicals'])}")
    print(f"  curated alias canonicals loaded: {summary['curated_alias_canonicals']}")
    print(
        f"  entities: {summary['entity_count']} "
        f"(operators={summary['entity_operator_count']}, "
        f"curated={summary['entity_curated_count']}, "
        f"auto-seeded={summary['entity_auto_seeded_count']})"
    )
    if summary["entity_curated_errors"]:
        print(
            f"  entity curated errors: {len(summary['entity_curated_errors'])} "
            "(see manifest)"
        )
    if summary["entity_curated_warnings"]:
        print(
            f"  entity curated warnings: {len(summary['entity_curated_warnings'])} "
            "(see manifest)"
        )
    print(
        f"  cooccurrence pairs: {summary['cooccurrence_pair_count']} "
        f"(stage co-appearances: {summary['cooccurrence_stage_total']})"
    )
    if storyset_warnings:
        print(f"  storyset warnings: {len(storyset_warnings)} (see manifest)")
    if pruned_events:
        print(f"  pruned events: {pruned_events}")
    if pruned_chars:
        print(f"  pruned chars: {pruned_chars}")
    print(f"  manifest: {paths.kb_manifest_path(kb_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
