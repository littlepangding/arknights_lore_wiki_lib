"""Bake LLM-derived summaries into `kb_summaries/`.

Two layers, both reading the deterministic KB under `data/kb/`:

* event summaries (default) → `kb_summaries/events/<id>.md` — one per event,
  single-pass or multi-pass per the M5 threshold.
* `--stages` → `kb_summaries/stages/<event_id>/<NN>.md` — one per `<章节>`,
  always single-pass. Catches the chapter-level retrieval need; its
  `<关键人物>` tags also feed the KB's `summary`-source char↔stage edges.

A manifest at `kb_summaries/manifest.json` records source hashes (separate
`events` / `stages` sections) so unchanged chunks are skipped on the next
run (no token re-spend). Persisted after every write, so a kill / quota
wall mid-bake never loses paid-for work — re-run to resume.

Run from the lib repo root (so `keys.json` resolves):

    .venv/bin/python -m scripts.kb_summarize             # all events
    .venv/bin/python -m scripts.kb_summarize --stages    # all stages
    .venv/bin/python -m scripts.kb_summarize --stages --event act46side
    .venv/bin/python -m scripts.kb_summarize --stages --llm cli --model gemini-3.1-pro-preview
    .venv/bin/python -m scripts.kb_summarize --stages --estimate   # dry-run cost

Defaults to the Gemini CLI backend (`gemini`). `--llm claude` shells out
to the local `claude` binary; `--llm gai` uses the google-genai SDK
(needs `genai_api_key` in keys.json).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from libs.bases import set_llm_archive_dir, try_get_value
from libs.kb import paths, summarize
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
            f"  done {ev.run_done}/{ev.run_total} ev"
            f"  ~{_fmt_count(ev.tokens_done)}/{_fmt_count(ev.tokens_total)} tok"
            f"  {_fmt_dur(ev.elapsed_s)} elapsed  ETA ~{_fmt_dur(ev.eta_s)}"
        )
        head = f"{head}  +{ev.passes}"
    elif ev.status == "skipped_unchanged":
        tail = "  · cached"
    elif ev.status == "terminal_error":
        tail = "  ✗ TERMINAL (quota / bad model / auth) — batch stopped"
    else:
        tail = f"  ! {ev.status}"
    print(head + tail, flush=True)


def _build_client(args: argparse.Namespace):
    """Backend precedence for default_model: --model > <backend>_model >
    the client's built-in default. The cli (gemini) backend's specific key
    *is* `llm_model`, so the legacy shared key still resolves there; other
    backends do not cross-fall back, to avoid leaking a gemini model name
    to claude or vice versa."""
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
        "--stages",
        action="store_true",
        help="Bake per-章节 summaries (kb_summaries/stages/<event_id>/<NN>.md) "
        "instead of per-event summaries.",
    )
    parser.add_argument(
        "--event",
        action="append",
        default=[],
        help="Restrict to this event_id (repeatable). With --stages, restricts "
        "to that event's stages. Default: all events.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore source-hash cache and re-summarize every selected unit.",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="Don't call any LLM — just print the projected cost (events, "
        "LLM calls, chars, ~tokens) of the run that would happen. Honors "
        "--event / --force / --kb-root / --summaries-root.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Keep kb_summaries/events/<id>.md files for events absent from the current KB.",
    )
    parser.add_argument(
        "--archive-dir",
        default="",
        help="Where to stash every raw LLM response (these cost tokens; the "
        "kept summary is only a canonicalized subset). Default: keys.json "
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

    only = args.event or None

    if args.estimate:
        est = (
            summarize.estimate_remaining_stages(kb_root, summaries_root, only=only, force=args.force)
            if args.stages
            else summarize.estimate_remaining(kb_root, summaries_root, only=only, force=args.force)
        )
        units = "stages" if args.stages else "events"
        scope = f"{len(only)} requested event(s)" if only else f"full corpus ({units})"
        print(f"cost estimate — {scope}  (force={args.force})")
        print(f"  {units} to run:  {est.n_to_run}  (single-pass: {est.n_single}, multi-pass: {est.n_multi})")
        print(f"  already done:    {len(est.already_done)}  (skipped — no token spend)")
        print(f"  LLM calls:       ~{est.llm_calls}")
        print(f"  input:           ~{est.in_chars:,} chars   ≈ ~{est.in_tokens:,} tokens")
        print(f"  output:          ~{est.out_chars:,} chars   ≈ ~{est.out_tokens:,} tokens")
        print(f"  total:           ~{est.total_chars:,} chars   ≈ ~{est.total_tokens:,} tokens")
        print(
            "  note: ~1 token/char for this CJK-heavy text; excludes retry "
            "re-tries and content-changed re-bills. Treat as a slight over-estimate."
        )
        return 0

    client, backend = _build_client(args)
    model = args.model or None

    if args.no_archive:
        archive_dir: Optional[str] = None
    elif args.archive_dir:
        archive_dir = args.archive_dir
    else:
        archive_dir = try_get_value("llm_archive_path", "llm_archive")
    set_llm_archive_dir(archive_dir)

    layer = "per-stage" if args.stages else "per-event"
    if only:
        print(f"summarizing {layer} for {len(only)} event(s): {', '.join(only)}")
    else:
        print(f"summarizing {layer} over all events under", paths.events_root(kb_root))
    print(f"backend={backend}  model={model or client.default_model}  force={args.force}  prune={not args.no_prune}")
    print(f"raw-output archive: {archive_dir or 'off'}")

    run = summarize.summarize_all_stages if args.stages else summarize.summarize_all
    report = run(
        kb_root,
        summaries_root,
        client,
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
        print(f"BATCH STOPPED — terminal error: {report.terminal_error}", file=sys.stderr)
        print("  (re-run to resume; the manifest is up to date through the last write)", file=sys.stderr)
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
