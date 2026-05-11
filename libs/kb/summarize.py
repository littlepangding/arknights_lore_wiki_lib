"""Per-event LLM summarization.

Reads `data/kb/events/<id>/` chunks, runs the P1 prompt (single-pass or
multi-pass per the M5-derived threshold), validates required zh tags,
and writes `kb_summaries/events/<id>.md` with frontmatter + canonical tags.

A `summaries_manifest.json` records source hashes so re-runs over
unchanged chunks are no-ops (no token re-spend).

The only LLM-using module in the KB layer. Char summaries are out of
scope per design (DESIGN.md "summarize.py rationale").
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from libs.bases import LLMError, LLMTerminalError, validate_and_rebuild
from libs.kb import _io, paths
from libs.llm_clients import LLMClient, query_with_validated_tags


# --- thresholds (DESIGN.md §summarize.py multi-pass trigger) ----------------

MULTI_PASS_LENGTH_THRESHOLD = 80_000
MULTI_PASS_STAGE_THRESHOLD = 10

EVENT_REQUIRED_TAGS: list[str] = ["一句话概要", "核心剧情", "关键人物", "场景标签"]
STAGE_REDUCE_REQUIRED_TAGS: list[str] = ["章节概要", "本章人物"]


# --- prompts (zh; mirrored from docs/PROMPTS.md §P1) ------------------------

SYSTEM_PROMPT = (
    "你是一个明日方舟剧情资料编写助手。"
    "你的任务是阅读活动剧情原文，输出结构化的导航摘要，仅供索引和检索，不替代原文。"
    "你严格遵守输出格式，使用简体中文，不引申、不评价、不揣测原作未交代的内容。"
)

USER_PROMPT_SINGLE_PASS = """以下是明日方舟某次活动的全部剧情原文（按章节组织）。请基于原文输出以下内容：

<一句话概要>
不超过40字，概括活动主题。
</一句话概要>

<核心剧情>
约300字的剧情梗概，按时间顺序，不引申、不评价、不揣测原作未交代的内容。
</核心剧情>

<关键人物>
用分号分隔的人物名单。仅限在剧情中实质出场或被关键提及的角色，不收录"博士"、"罗德岛"等非角色实体。
</关键人物>

<场景标签>
3-6个简短词组（用分号分隔），覆盖主要场景、地点或事件类型。
</场景标签>

【硬性要求】
- 严格使用简体中文，不要使用繁体或日文汉字。
- 不要在输出标签之外添加解释或对白。
- 如果某一项无法从原文中得出，写"无"，不要编造。
- 摘要的总长度控制在 600 字以内（不含标签）。

剧情原文：
{event_text}
"""

USER_PROMPT_STAGE_REDUCE = """以下是明日方舟某次活动一个章节的剧情原文。请基于原文输出该章节的精简摘要：

<章节概要>
不超过200字，按时间顺序概括本章节剧情，不引申、不评价。
</章节概要>

<本章人物>
用分号分隔的人物名单。仅限本章节中实质出场或被关键提及的角色，不收录"博士"、"罗德岛"等非角色实体。
</本章人物>

【硬性要求】
- 严格使用简体中文。
- 不要在输出标签之外添加解释。
- 不要编造原文中未出现的内容。

章节原文：
{stage_text}
"""

USER_PROMPT_MERGE = """以下输入已是同一活动各章节的精简摘要。请基于它们重写整体摘要，不要逐章罗列。请输出：

<一句话概要>
不超过40字，概括活动主题。
</一句话概要>

<核心剧情>
约300字的剧情梗概，按时间顺序整合各章节，不引申、不评价。
</核心剧情>

<关键人物>
用分号分隔的人物名单（汇总各章节）。仅限实质出场或被关键提及的角色，不收录"博士"、"罗德岛"等非角色实体。
</关键人物>

<场景标签>
3-6个简短词组（分号分隔），覆盖主要场景、地点或事件类型。
</场景标签>

【硬性要求】
- 严格使用简体中文。
- 不要在输出标签之外添加内容。
- 不要复述各章节标题，整合成连贯的整体摘要。
- 摘要总长度控制在 600 字以内（不含标签）。

分章摘要：
{stage_summaries}
"""


# --- result + manifest types ------------------------------------------------


@dataclass
class SummaryResult:
    event_id: str
    status: str  # "wrote" | "skipped_unchanged" | "error"
    summary_path: Optional[Path] = None
    passes: str = ""  # "single" | "multi" | ""
    source_hash: str = ""
    error: Optional[str] = None


@dataclass
class SummarizeReport:
    wrote: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)


# --- core helpers -----------------------------------------------------------


def _read_stage_texts(event_dir: Path, stages: list[dict]) -> list[tuple[str, str]]:
    """Read each stage file once. Returns [(filename, text), ...] in stage order."""
    return [
        (s["file"], (event_dir / s["file"]).read_text(encoding="utf-8"))
        for s in stages
    ]


def hash_stage_texts(stage_texts: Iterable[tuple[str, str]]) -> str:
    """Stable hash over (filename, text) pairs sorted by filename. Used for
    skip-on-unchanged detection."""
    h = hashlib.sha256()
    for name, text in sorted(stage_texts, key=lambda x: x[0]):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(text.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def should_multi_pass(total_length: int, stage_count: int) -> bool:
    return (
        total_length > MULTI_PASS_LENGTH_THRESHOLD
        or stage_count > MULTI_PASS_STAGE_THRESHOLD
    )


def _format_summary_md(
    event_meta: dict,
    src_hash: str,
    passes: str,
    validated_body: str,
    *,
    backend_label: str = "",
    model_label: str = "",
) -> str:
    fm = [
        "---",
        f"event_id: {event_meta['event_id']}",
        f"event_name: {event_meta['name']}",
        f"source_family: {event_meta['source_family']}",
        f"source_hash: {src_hash}",
        f"generated_at: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"passes: {passes}",
        f"total_length: {event_meta['total_length']}",
        f"stage_count: {len(event_meta['stages'])}",
    ]
    if backend_label:
        fm.append(f"backend: {backend_label}")
    if model_label:
        fm.append(f"model: {model_label}")
    fm.append("---")
    return "\n".join(fm) + "\n\n" + validated_body.rstrip() + "\n"


def _summarize_single_pass(
    stage_texts: list[tuple[str, str]],
    client: LLMClient,
    *,
    model: Optional[str] = None,
) -> str:
    event_text = "\n\n".join(text for _, text in stage_texts)
    prompt = USER_PROMPT_SINGLE_PASS.format(event_text=event_text)
    return query_with_validated_tags(
        client, SYSTEM_PROMPT, prompt, EVENT_REQUIRED_TAGS, model=model
    )


def _summarize_multi_pass(
    stage_texts: list[tuple[str, str]],
    client: LLMClient,
    *,
    model: Optional[str] = None,
) -> str:
    stage_blocks: list[str] = []
    for _, stage_text in stage_texts:
        stage_prompt = USER_PROMPT_STAGE_REDUCE.format(stage_text=stage_text)
        out = query_with_validated_tags(
            client, SYSTEM_PROMPT, stage_prompt, STAGE_REDUCE_REQUIRED_TAGS, model=model
        )
        stage_blocks.append(validate_and_rebuild(out, STAGE_REDUCE_REQUIRED_TAGS))
    merged = "\n\n---\n\n".join(stage_blocks)
    merge_prompt = USER_PROMPT_MERGE.format(stage_summaries=merged)
    return query_with_validated_tags(
        client, SYSTEM_PROMPT, merge_prompt, EVENT_REQUIRED_TAGS, model=model
    )


# --- public entry points ----------------------------------------------------


def summarize_event(
    event_meta: dict,
    event_dir: Path,
    summaries_root: Path,
    client: LLMClient,
    *,
    force: bool = False,
    prior_manifest_entry: Optional[dict] = None,
    backend_label: str = "",
    model: Optional[str] = None,
) -> SummaryResult:
    """Summarize a single event. Returns a SummaryResult either way;
    failures (LLM, missing/malformed stage files, manifest schema gaps,
    write errors) land in `status='error'` with the message in `.error`
    so `summarize_all` can keep going on the next event."""
    event_id = event_meta.get("event_id", "<unknown>")
    try:
        stages = event_meta["stages"]
        stage_texts = _read_stage_texts(event_dir, stages)
        src_hash = hash_stage_texts(stage_texts)

        out_path = paths.event_summary_path(summaries_root, event_id)

        if (
            not force
            and prior_manifest_entry
            and prior_manifest_entry.get("source_hash") == src_hash
            and out_path.exists()
        ):
            return SummaryResult(
                event_id=event_id,
                status="skipped_unchanged",
                summary_path=out_path,
                passes=prior_manifest_entry.get("passes", ""),
                source_hash=src_hash,
            )

        multi = should_multi_pass(event_meta["total_length"], len(stages))
        passes = "multi" if multi else "single"
        body = (
            _summarize_multi_pass(stage_texts, client, model=model)
            if multi
            else _summarize_single_pass(stage_texts, client, model=model)
        )
        validated = validate_and_rebuild(body, EVENT_REQUIRED_TAGS)
        md = _format_summary_md(
            event_meta,
            src_hash,
            passes,
            validated,
            backend_label=backend_label,
            model_label=model or client.default_model,
        )
        _io.atomic_write_text(out_path, md)
        return SummaryResult(
            event_id=event_id,
            status="wrote",
            summary_path=out_path,
            passes=passes,
            source_hash=src_hash,
        )
    except LLMTerminalError:
        # Re-raise terminal LLM errors (quota / bad model / auth). The batch
        # caller bails the whole loop on these — retrying every remaining
        # event against the same wall is pure waste.
        raise
    except (LLMError, OSError, KeyError, ValueError) as e:
        return SummaryResult(
            event_id=event_id, status="error", error=f"{type(e).__name__}: {e}"
        )


def load_summaries_manifest(summaries_root: Path) -> dict:
    return _io.read_json_or(
        paths.summaries_manifest_path(summaries_root), {"version": 1, "events": {}}
    )


def save_summaries_manifest(summaries_root: Path, data: dict) -> None:
    data["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _io.atomic_write_json(paths.summaries_manifest_path(summaries_root), data)


def prune_stale_summaries(
    summaries_root: Path, current_event_ids: set[str]
) -> list[str]:
    """Remove kb_summaries/events/<id>.md files for events not in
    current_event_ids. Returns the list of removed event_ids."""
    keep = {f"{eid}.md" for eid in current_event_ids}
    removed = _io.prune_stale_files(summaries_root / "events", "*.md", keep)
    return [Path(name).stem for name in removed]


def _load_events_meta(kb_root: Path, only: Optional[Iterable[str]]) -> dict[str, dict]:
    """Load event.json manifests. With `only`, read just those subdirs
    (saves ~460 disk reads when filtering to one event)."""
    events_root = paths.events_root(kb_root)
    if only:
        out: dict[str, dict] = {}
        for eid in only:
            path = paths.event_json_path(kb_root, eid)
            if path.is_file():
                out[eid] = _io.read_json(path)
        return out
    return _io.load_dir_manifests(events_root, "event.json")


# --- cost estimation (no LLM, no token spend) -------------------------------

# Projected response sizes in chars, incl. tag markup. The prompts cap bodies
# at 600字 / 200字; these add tag overhead + slack. Deliberately a little high.
EST_SINGLE_PASS_OUT_CHARS = 900
EST_STAGE_REDUCE_OUT_CHARS = 400
EST_MERGE_OUT_CHARS = 900

# Rough chars→tokens divisor for the CJK-dominant KB text. Gemini/Claude both
# tokenize common Chinese chars at roughly 1 token each, English IDs/markup at
# ~4 chars/token — net effect on this corpus is a hair under 1:1, so 1.0 is a
# safe slight-overestimate. Tune if real usage numbers say otherwise.
EST_CHARS_PER_TOKEN = 1.0

_PROMPT_OVERHEAD_SINGLE = len(SYSTEM_PROMPT) + len(USER_PROMPT_SINGLE_PASS)
_PROMPT_OVERHEAD_STAGE = len(SYSTEM_PROMPT) + len(USER_PROMPT_STAGE_REDUCE)
_PROMPT_OVERHEAD_MERGE = len(SYSTEM_PROMPT) + len(USER_PROMPT_MERGE)


@dataclass
class EventCostEstimate:
    event_id: str
    passes: str  # "single" | "multi"
    stage_count: int
    total_length: int
    llm_calls: int
    in_chars: int
    out_chars: int

    @property
    def total_chars(self) -> int:
        return self.in_chars + self.out_chars


@dataclass
class CostEstimate:
    to_run: list[EventCostEstimate] = field(default_factory=list)
    already_done: list[EventCostEstimate] = field(default_factory=list)

    def _sum(self, rows: list[EventCostEstimate], attr: str) -> int:
        return sum(getattr(r, attr) for r in rows)

    @property
    def n_to_run(self) -> int:
        return len(self.to_run)

    @property
    def n_single(self) -> int:
        return sum(1 for e in self.to_run if e.passes == "single")

    @property
    def n_multi(self) -> int:
        return sum(1 for e in self.to_run if e.passes == "multi")

    @property
    def llm_calls(self) -> int:
        return self._sum(self.to_run, "llm_calls")

    @property
    def in_chars(self) -> int:
        return self._sum(self.to_run, "in_chars")

    @property
    def out_chars(self) -> int:
        return self._sum(self.to_run, "out_chars")

    @property
    def total_chars(self) -> int:
        return self.in_chars + self.out_chars

    @property
    def in_tokens(self) -> int:
        return round(self.in_chars / EST_CHARS_PER_TOKEN)

    @property
    def out_tokens(self) -> int:
        return round(self.out_chars / EST_CHARS_PER_TOKEN)

    @property
    def total_tokens(self) -> int:
        return self.in_tokens + self.out_tokens

    @property
    def done_in_chars(self) -> int:
        return self._sum(self.already_done, "in_chars")


def estimate_event_cost(
    event_id: str, total_length: int, stage_count: int
) -> EventCostEstimate:
    """Projected one-run cost for a single event. Mirrors `summarize_event`'s
    branch: single-pass = 1 call; multi-pass = stage_count reduce calls + 1
    merge call. Input chars = prompt overhead + the actual story text;
    output chars are the EST_* guesses above. No tag-revalidation retries
    are modeled (rare) — add headroom yourself if you want a worst case."""
    if not should_multi_pass(total_length, stage_count):
        return EventCostEstimate(
            event_id=event_id,
            passes="single",
            stage_count=stage_count,
            total_length=total_length,
            llm_calls=1,
            in_chars=_PROMPT_OVERHEAD_SINGLE + total_length,
            out_chars=EST_SINGLE_PASS_OUT_CHARS,
        )
    # multi-pass: per-stage reduce calls (their inputs sum to total_length plus
    # per-call prompt overhead), then one merge over the stage summaries.
    n = max(stage_count, 1)
    stage_out = EST_STAGE_REDUCE_OUT_CHARS * n
    stage_in = _PROMPT_OVERHEAD_STAGE * n + total_length
    merge_in = _PROMPT_OVERHEAD_MERGE + stage_out
    return EventCostEstimate(
        event_id=event_id,
        passes="multi",
        stage_count=stage_count,
        total_length=total_length,
        llm_calls=n + 1,
        in_chars=stage_in + merge_in,
        out_chars=stage_out + EST_MERGE_OUT_CHARS,
    )


def est_tokens(row: EventCostEstimate) -> int:
    return round(row.total_chars / EST_CHARS_PER_TOKEN)


def _classify_run(
    events_meta: dict[str, dict],
    manifest_events: dict,
    summaries_root: Path,
    force: bool,
) -> tuple[dict[str, EventCostEstimate], dict[str, EventCostEstimate]]:
    """Split events into (to_run, already_done) keyed by event_id, each value
    an `EventCostEstimate`. "Would run" iff `force`, or no manifest entry, or
    the summary `.md` is missing. Does NOT re-hash stage text — a content
    change in an already-summarized event is invisible here and gets caught
    (and re-billed) inside `summarize_event` at run time. Shared by
    `estimate_remaining` (the dry-run) and `summarize_all` (live progress)."""
    to_run: dict[str, EventCostEstimate] = {}
    done: dict[str, EventCostEstimate] = {}
    for event_id, meta in sorted(events_meta.items()):
        row = estimate_event_cost(event_id, meta["total_length"], len(meta["stages"]))
        prior = manifest_events.get(event_id)
        out_path = paths.event_summary_path(summaries_root, event_id)
        if force or prior is None or not out_path.exists():
            to_run[event_id] = row
        else:
            done[event_id] = row
    return to_run, done


@dataclass
class ProgressEvent:
    """One per event as `summarize_all` works through the batch — fed to the
    optional `progress` callback so the caller can print a live status line.
    Token counts are estimates (see `_classify_run`); `eta_s` is None until at
    least one event has actually been written this run."""
    index: int            # 1-based position in the iteration
    total: int            # total events iterated this run
    event_id: str
    status: str           # "wrote" | "skipped_unchanged" | "error" | "terminal_error"
    passes: str           # "single" | "multi" | ""
    run_done: int         # events written so far this run
    run_total: int        # events that will be written this run (est.)
    tokens_done: int      # est. tokens spent so far this run
    tokens_total: int     # est. tokens this run will spend
    elapsed_s: float
    eta_s: Optional[float]


def estimate_remaining(
    kb_root: Path,
    summaries_root: Path,
    *,
    only: Optional[Iterable[str]] = None,
    force: bool = False,
) -> CostEstimate:
    """Estimate the token/char cost of the next `summarize_all` run without
    touching an LLM. Selection mirrors `summarize_all`: an event runs if
    `force`, or it has no manifest entry, or its summary file is missing.

    Cheap deliberate inaccuracy: this does NOT re-hash stage text, so a
    *content change* in an already-summarized event won't show here — it
    gets caught (and re-billed) inside `kb_summarize` at run time. Good
    enough for the common "how much is left to bake" question."""
    only_list = list(only) if only else None
    events_meta = _load_events_meta(kb_root, only_list)
    manifest_events = load_summaries_manifest(summaries_root).get("events", {})
    to_run, done = _classify_run(events_meta, manifest_events, summaries_root, force)
    return CostEstimate(to_run=list(to_run.values()), already_done=list(done.values()))


def summarize_all(
    kb_root: Path,
    summaries_root: Path,
    client: LLMClient,
    *,
    only: Optional[Iterable[str]] = None,
    force: bool = False,
    prune: bool = True,
    backend_label: str = "",
    model: Optional[str] = None,
    progress: Optional[Callable[[ProgressEvent], None]] = None,
) -> SummarizeReport:
    """Summarize every event under `events_root(kb_root)`.

    `only`: restrict to these event_ids (otherwise: all).
    `force`: ignore source-hash cache and re-run.
    `prune`: drop kb_summaries/events/<id>.md not in the current build.
    `progress`: optional callback invoked once per event with a `ProgressEvent`
        (position, status, running token estimate, ETA) — for live CLI output.
        Token figures are estimates; ETA is token-rate extrapolation from the
        events written so far this run.

    `prune` only runs when iterating the full corpus (`only is None`); a
    filtered run shouldn't be able to remove summaries it didn't consider.
    """
    only_list = list(only) if only else None
    events_meta = _load_events_meta(kb_root, only_list)
    if not events_meta:
        if only_list is None:
            raise FileNotFoundError(
                f"no events under {paths.events_root(kb_root)} — run kb_build first"
            )
        raise FileNotFoundError(f"none of the requested events exist: {only_list}")

    manifest = load_summaries_manifest(summaries_root)
    manifest_events = manifest.setdefault("events", {})
    model_label = model or client.default_model
    report = SummarizeReport()

    run_plan, _ = _classify_run(events_meta, manifest_events, summaries_root, force)
    run_total = len(run_plan)
    tokens_total = sum(est_tokens(r) for r in run_plan.values())
    total_iter = len(events_meta)
    run_done = 0
    tokens_done = 0
    t0 = time.monotonic()

    def _emit(idx: int, eid: str, status: str, passes: str) -> None:
        if progress is None:
            return
        elapsed = time.monotonic() - t0
        eta: Optional[float] = None
        if tokens_done > 0 and tokens_total > tokens_done:
            eta = (elapsed / tokens_done) * (tokens_total - tokens_done)
        progress(ProgressEvent(
            index=idx, total=total_iter, event_id=eid, status=status, passes=passes,
            run_done=run_done, run_total=run_total,
            tokens_done=tokens_done, tokens_total=tokens_total,
            elapsed_s=elapsed, eta_s=eta,
        ))

    terminal_error: Optional[str] = None
    for idx, (event_id, event_meta) in enumerate(sorted(events_meta.items()), 1):
        try:
            result = summarize_event(
                event_meta,
                paths.event_dir(kb_root, event_id),
                summaries_root,
                client,
                force=force,
                prior_manifest_entry=manifest_events.get(event_id),
                backend_label=backend_label,
                model=model,
            )
        except LLMTerminalError as e:
            # Quota / wrong model / auth — bail the batch. Manifest is
            # already up-to-date through the prior event (we persist on
            # every write, see below).
            terminal_error = f"{type(e).__name__}: {e}"
            report.errors.append((event_id, terminal_error))
            _emit(idx, event_id, "terminal_error", "")
            break

        if result.status == "wrote":
            manifest_events[event_id] = {
                "source_hash": result.source_hash,
                "summary_path": str(result.summary_path.relative_to(summaries_root)),
                "passes": result.passes,
                "total_length": event_meta["total_length"],
                "stage_count": len(event_meta["stages"]),
                "backend": backend_label,
                "model": model_label,
                "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            # Persist after every successful event so a kill / quota wall /
            # crash mid-batch never loses what we already paid the LLM for.
            save_summaries_manifest(summaries_root, manifest)
            report.wrote.append(event_id)
            run_done += 1
            tokens_done += est_tokens(
                run_plan.get(event_id)
                or estimate_event_cost(
                    event_id, event_meta["total_length"], len(event_meta["stages"])
                )
            )
        elif result.status == "skipped_unchanged":
            report.skipped.append(event_id)
        else:
            report.errors.append((event_id, result.error or "unknown error"))

        _emit(idx, event_id, result.status, result.passes)

    if terminal_error:
        # Skip pruning on a terminal bail — `only_list is None` runs prune
        # against the in-memory events_meta, but we didn't actually finish
        # the batch, so removing "stale" entries would be premature.
        return report

    manifest_changed = bool(report.wrote or report.errors)
    if prune and only_list is None:
        report.pruned = prune_stale_summaries(summaries_root, set(events_meta.keys()))
        stale_in_manifest = [eid for eid in manifest_events if eid not in events_meta]
        for stale in stale_in_manifest:
            del manifest_events[stale]
        if report.pruned or stale_in_manifest:
            manifest_changed = True

    if manifest_changed:
        save_summaries_manifest(summaries_root, manifest)
    return report
