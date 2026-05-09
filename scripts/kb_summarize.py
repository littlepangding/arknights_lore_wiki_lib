"""Bake LLM-derived event summaries into `kb_summaries/events/<id>.md`.

Reads the deterministic KB under `data/kb/events/`, runs the P1 prompt
against each event (single-pass or multi-pass per the M5 threshold) and
writes a small zh summary plus frontmatter. A manifest at
`kb_summaries/manifest.json` records source hashes so unchanged events
are skipped on the next run (no token re-spend).

Run from the lib repo root (so `keys.json` resolves):

    .venv/bin/python -m scripts.kb_summarize             # all events
    .venv/bin/python -m scripts.kb_summarize --event act46side
    .venv/bin/python -m scripts.kb_summarize --llm gai --model gemini-2.5-flash

Defaults to the Gemini CLI backend (`gemini`). `--llm claude` shells out
to the local `claude` binary; `--llm gai` uses the google-genai SDK
(needs `genai_api_key` in keys.json).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from libs import bases
from libs.kb import paths, summarize
from libs.llm_clients import make_client


def _try_get_value(key: str, default=None):
    try:
        return bases.get_value(key, default)
    except FileNotFoundError:
        return default


def _build_client(args: argparse.Namespace):
    backend = args.llm or _try_get_value("llm_backend", "cli")

    if backend == "cli":
        kwargs = {}
        cli_path = _try_get_value("gemini_cli_path")
        if cli_path:
            kwargs["cli_path"] = cli_path
        default_model = args.model or _try_get_value("llm_model")
        if default_model:
            kwargs["default_model"] = default_model
        return make_client("cli", **kwargs), backend

    if backend == "gai":
        from google import genai  # type: ignore[import-not-found]

        api_key = _try_get_value("genai_api_key")
        if not api_key:
            raise SystemExit("--llm gai requires `genai_api_key` in keys.json")
        gai_client = genai.Client(api_key=api_key)
        kwargs = {"gai_client": gai_client}
        default_model = args.model or _try_get_value("gai_model")
        if default_model:
            kwargs["default_model"] = default_model
        return make_client("gai", **kwargs), backend

    if backend == "claude":
        kwargs = {}
        cli_path = _try_get_value("claude_cli_path")
        if cli_path:
            kwargs["cli_path"] = cli_path
        default_model = args.model or _try_get_value("claude_model")
        if default_model:
            kwargs["default_model"] = default_model
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
        "--event",
        action="append",
        default=[],
        help="Restrict to this event_id (repeatable). Default: all events.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore source-hash cache and re-summarize every selected event.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Keep kb_summaries/events/<id>.md files for events absent from the current KB.",
    )
    parser.add_argument(
        "--kb-root",
        default="",
        help=f"KB input root. Defaults to ./{paths.KB_DIRNAME}.",
    )
    parser.add_argument(
        "--summaries-root",
        default="",
        help=f"Summaries output root. Defaults to ./{paths.SUMMARIES_DIRNAME}.",
    )
    args = parser.parse_args()

    kb_root = Path(args.kb_root) if args.kb_root else paths.default_kb_root()
    summaries_root = (
        Path(args.summaries_root) if args.summaries_root else paths.default_summaries_root()
    )
    if not kb_root.is_dir():
        parser.error(
            f"kb_root {kb_root} does not exist — run `python -m scripts.kb_build` first"
        )
    summaries_root.mkdir(parents=True, exist_ok=True)

    client, backend = _build_client(args)
    model = args.model or None

    only = args.event or None
    if only:
        print(f"summarizing {len(only)} event(s): {', '.join(only)}")
    else:
        print("summarizing all events under", paths.events_root(kb_root))
    print(f"backend={backend}  model={model or getattr(client, 'default_model', '?')}  force={args.force}  prune={not args.no_prune}")

    report = summarize.summarize_all(
        kb_root,
        summaries_root,
        client,
        only=only,
        force=args.force,
        prune=not args.no_prune,
        backend_label=backend,
        model=model,
    )

    print()
    print(f"wrote:   {len(report.wrote)}")
    if report.wrote:
        for eid in report.wrote:
            print(f"  + {eid}")
    print(f"skipped (unchanged): {len(report.skipped)}")
    if report.errors:
        print(f"errors: {len(report.errors)}")
        for eid, msg in report.errors:
            print(f"  ! {eid}: {msg}", file=sys.stderr)
    if report.pruned:
        print(f"pruned stale summaries: {len(report.pruned)}")
        for eid in report.pruned:
            print(f"  - {eid}")

    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
