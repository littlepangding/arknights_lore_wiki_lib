"""Bake typed relations into `kb_relations/chars/<char_id>.jsonl`.

One LLM call per operator reads `profile.txt` + `archive.txt` +
`voice.txt` and emits typed assertions (`member_of`, `mentor_of`,
`identifies_as`, …) against the entity layer. The collated view at
`data/kb/relations.jsonl` is built by `kb_build` from these per-char
files plus the curated override.

A manifest at `kb_relations/manifest.json` records source hashes so
unchanged handbooks are skipped on the next run (no token re-spend).
Persisted after every write — a kill / quota wall mid-bake never
loses paid-for work; re-run to resume.

Run from the lib repo root (so `keys.json` resolves):

    .venv/bin/python -m scripts.kb_relations                 # all chars
    .venv/bin/python -m scripts.kb_relations --char char_002_amiya
    .venv/bin/python -m scripts.kb_relations --estimate      # dry-run cost
    .venv/bin/python -m scripts.kb_relations --llm cli --model gemini-3.1-pro-preview

After the bake, re-run `kb_build` so the new per-char files collate
into `data/kb/relations.jsonl` and become visible to
`kb_query relations …`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from libs.bases import set_llm_archive_dir, try_get_value
from libs.kb import indexer, paths, relations_bake
from libs.kb.summarize import ProgressEvent
from libs.llm_clients import make_client


def _fmt_dur(s: Optional[float]) -> str:
    if s is None:
        return "?"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _print_progress(ev: ProgressEvent) -> None:
    head = f"[{ev.index}/{ev.total}] {ev.event_id}"
    if ev.status == "wrote":
        tail = (
            f"  done {ev.run_done}/{ev.run_total} chars"
            f"  ~{_fmt_count(ev.tokens_done)}/{_fmt_count(ev.tokens_total)} tok"
            f"  {_fmt_dur(ev.elapsed_s)} elapsed  ETA ~{_fmt_dur(ev.eta_s)}"
        )
    elif ev.status == "skipped_unchanged":
        tail = "  · cached"
    elif ev.status == "terminal_error":
        tail = "  ✗ TERMINAL (quota / bad model / auth) — batch stopped"
    else:
        tail = f"  ! {ev.status}"
    print(head + tail, flush=True)


def _build_client(args: argparse.Namespace):
    """Same precedence rules as kb_summarize: --model > <backend>_model >
    client default. Kept duplicate (vs imported) so the two bake scripts
    stay independently rotatable as we iterate on LLM-config UX."""
    backend = args.llm or try_get_value("llm_backend", "cli")

    def _resolve_model(specific_key: str) -> Optional[str]:
        return args.model or try_get_value(specific_key)

    if backend == "cli":
        kwargs = {}
        cli_path = try_get_value("gemini_cli_path")
        if cli_path:
            kwargs["cli_path"] = cli_path
        model = _resolve_model("llm_model")
        if model:
            kwargs["default_model"] = model
        return make_client("cli", **kwargs), backend

    if backend == "gai":
        from google import genai  # type: ignore[import-not-found]

        api_key = try_get_value("genai_api_key")
        if not api_key:
            raise SystemExit("--llm gai requires `genai_api_key` in keys.json")
        kwargs = {"gai_client": genai.Client(api_key=api_key)}
        model = _resolve_model("gai_model")
        if model:
            kwargs["default_model"] = model
        return make_client("gai", **kwargs), backend

    if backend == "claude":
        kwargs = {}
        cli_path = try_get_value("claude_cli_path")
        if cli_path:
            kwargs["cli_path"] = cli_path
        model = _resolve_model("claude_model")
        if model:
            kwargs["default_model"] = model
        return make_client("claude", **kwargs), backend

    raise SystemExit(f"unknown --llm backend: {backend!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm",
        choices=["cli", "gai", "claude"],
        default="",
        help="LLM backend; default reads keys.json llm_backend or 'cli'.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Override default model for the chosen backend.",
    )
    parser.add_argument(
        "--char",
        action="append",
        default=[],
        help="Restrict to this char_id (repeatable). Default: every char with a "
        "handbook on disk.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore source-hash cache and re-bake every selected char.",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="Don't call any LLM — just print the projected cost (chars, LLM "
        "calls, ~tokens) of the run that would happen. Honors --char / --force.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Keep kb_relations/chars/<id>.jsonl for chars absent from the current KB.",
    )
    parser.add_argument(
        "--archive-dir",
        default="",
        help="Where to stash every raw LLM response. Default: keys.json "
        "`llm_archive_path`, else ./llm_archive. Gitignored.",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Don't archive raw LLM responses.",
    )
    parser.add_argument(
        "--kb-root",
        default="",
        help=f"KB input root. Defaults to ./{paths.KB_DIRNAME}.",
    )
    parser.add_argument(
        "--relations-root",
        default="",
        help=f"Relations output root. Defaults to ./{paths.RELATIONS_DIRNAME}.",
    )
    args = parser.parse_args()

    kb_root = Path(args.kb_root) if args.kb_root else paths.default_kb_root()
    relations_root = (
        Path(args.relations_root)
        if args.relations_root
        else paths.default_relations_root()
    )
    if not kb_root.is_dir():
        parser.error(
            f"kb_root {kb_root} does not exist — run `python -m scripts.kb_build` first"
        )
    relations_root.mkdir(parents=True, exist_ok=True)

    only = args.char or None

    # Load chars + entity alias index once. The alias index is needed for
    # tail resolution during the bake; loading it here keeps the bake's
    # per-call hot path free of disk reads beyond the handbook.
    char_manifests = indexer.load_char_manifests(kb_root)
    if not char_manifests:
        parser.error(
            f"no char manifests under {kb_root}/chars — run `kb_build` first"
        )

    if args.estimate:
        est = relations_bake.estimate_remaining_relations(
            kb_root, relations_root, char_manifests,
            only=only, force=args.force,
        )
        scope = f"{len(only)} requested char(s)" if only else "full corpus"
        print(f"cost estimate — {scope}  (force={args.force})")
        print(f"  chars to bake:   {est.n_to_run}")
        print(f"  already done:    {len(est.already_done)}  (skipped — no token spend)")
        print(f"  LLM calls:       ~{est.llm_calls}")
        print(f"  input:           ~{est.in_chars:,} chars   ≈ ~{est.in_tokens:,} tokens")
        print(f"  output:          ~{est.out_chars:,} chars   ≈ ~{est.out_tokens:,} tokens")
        print(f"  total:           ~{est.total_chars:,} chars   ≈ ~{est.total_tokens:,} tokens")
        print(
            "  note: ~1 token/char for this CJK-heavy text; excludes retry "
            "re-tries. Treat as a slight over-estimate."
        )
        return 0

    # Build entity alias index from the on-disk entities.jsonl (kb_build wrote it).
    from libs.kb import entities, query
    ent_list = entities.load_entities(paths.entities_jsonl_path(kb_root))
    if not ent_list:
        parser.error(
            f"no entities.jsonl under {kb_root} — re-run `kb_build` to materialise "
            "the entity layer before baking relations"
        )
    entity_alias_to_ids = entities.build_entity_alias_index(ent_list)

    client, backend = _build_client(args)
    model = args.model or None

    if args.no_archive:
        archive_dir: Optional[str] = None
    elif args.archive_dir:
        archive_dir = args.archive_dir
    else:
        archive_dir = try_get_value("llm_archive_path", "llm_archive")
    set_llm_archive_dir(archive_dir)

    if only:
        print(f"baking relations for {len(only)} char(s): {', '.join(only)}")
    else:
        print(f"baking relations over all chars under {paths.chars_root(kb_root)}")
    print(
        f"backend={backend}  model={model or client.default_model}  "
        f"force={args.force}  prune={not args.no_prune}"
    )
    print(f"raw-output archive: {archive_dir or 'off'}")

    report = relations_bake.bake_relations_all(
        kb_root,
        relations_root,
        client,
        entity_alias_to_ids,
        char_manifests,
        only=only,
        force=args.force,
        prune=not args.no_prune,
        backend_label=backend,
        model=model,
        progress=_print_progress,
    )

    print()
    print(f"wrote:   {len(report.wrote)}")
    print(f"skipped (unchanged): {len(report.skipped)}")
    if report.terminal_error:
        print(
            f"BATCH STOPPED — terminal error: {report.terminal_error}",
            file=sys.stderr,
        )
        print(
            "  (re-run to resume; the manifest is up to date through the last write)",
            file=sys.stderr,
        )
    if report.errors:
        print(f"errors: {len(report.errors)}")
        for cid, msg in report.errors:
            print(f"  ! {cid}: {msg}", file=sys.stderr)
    if report.pruned:
        print(f"pruned stale char files: {len(report.pruned)}")
        for cid in report.pruned:
            print(f"  - {cid}")

    print()
    print(
        "next: re-run `python -m scripts.kb_build` to collate the new per-char "
        "files into data/kb/relations.jsonl (visible via `kb_query relations …`)."
    )
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
