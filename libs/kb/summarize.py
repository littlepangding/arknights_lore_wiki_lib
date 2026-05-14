"""LLM summarization for the KB layer — event-level and per-stage.

Two bakes, both reading the deterministic chunks under `data/kb/`:

* **event summaries** — `summarize_all` → `kb_summaries/events/<id>.md`
  (`<一句话概要> <核心剧情> <关键人物> <场景标签>` for a whole event;
  single-pass or multi-pass per the M5 threshold).
* **per-stage summaries** — `summarize_all_stages` → `kb_summaries/stages/<event_id>/<NN>.md`
  (the same 4-tag shape, scoped to one `<章节>`; always single-pass —
  no stage chunk is anywhere near the multi-pass threshold).

Both share `_run_batch`: a hash-gated, resume-safe loop that persists the
manifest after every write (so a kill / quota wall mid-bake never loses
paid-for work) and bails the whole batch on a terminal LLM error.

`kb_summaries/manifest.json` records source hashes (`events` and `stages`
sections) so re-runs over unchanged chunks are no-ops (no token re-spend).

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

# Event summaries and per-stage summaries use the same 4-tag shape — only the
# scope (whole event vs. one chapter) and the prompt differ.
EVENT_REQUIRED_TAGS: list[str] = ["一句话概要", "核心剧情", "关键人物", "场景标签"]
STAGE_SUMMARY_REQUIRED_TAGS: list[str] = EVENT_REQUIRED_TAGS
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

USER_PROMPT_STAGE_SUMMARY = """以下是明日方舟某次活动一个章节的剧情原文。请基于原文输出该章节的导航摘要，仅供索引检索，不替代原文：

<一句话概要>
不超过30字，概括本章节的核心事件。
</一句话概要>

<核心剧情>
不超过200字的本章节剧情梗概，按时间顺序，不引申、不评价、不揣测原作未交代的内容。
</核心剧情>

<关键人物>
用分号分隔的人物名单。仅限本章节中实质出场或被关键提及的角色，不收录"博士"、"罗德岛"等非角色实体。如本章节无明确人物，写"无"。
</关键人物>

<场景标签>
2-4个简短词组（用分号分隔），覆盖本章节的主要场景、地点或事件类型。
</场景标签>

【硬性要求】
- 严格使用简体中文，不要使用繁体或日文汉字。
- 不要在输出标签之外添加解释或对白。
- 如果某一项无法从原文中得出，写"无"，不要编造。

章节原文：
{stage_text}
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
    # Set when the batch bailed early on a terminal LLM error (quota / bad
    # model / auth). The caller skips pruning in that case — the run is
    # incomplete, so "stale" entries may just be the unprocessed tail.
    terminal_error: Optional[str] = None


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


def _format_stage_summary_md(
    event_meta: dict,
    stage: dict,
    src_hash: str,
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
        f"stage_idx: {stage['idx']}",
        f"stage_name: {stage['name']}",
        f"avg_tag: {stage.get('avgTag') or ''}",
        f"source_hash: {src_hash}",
        f"generated_at: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"total_length: {stage['length']}",
    ]
    if backend_label:
        fm.append(f"backend: {backend_label}")
    if model_label:
        fm.append(f"model: {model_label}")
    fm.append("---")
    return "\n".join(fm) + "\n\n" + validated_body.rstrip() + "\n"


def _sub_label(base: Optional[str], suffix: str) -> Optional[str]:
    """Per-call archive label for a sub-step of an event (`act46side__merge`),
    or None when archiving for this event is off."""
    return f"{base}__{suffix}" if base else None


def _summarize_single_pass(
    stage_texts: list[tuple[str, str]],
    client: LLMClient,
    *,
    model: Optional[str] = None,
    archive_label: Optional[str] = None,
) -> str:
    event_text = "\n\n".join(text for _, text in stage_texts)
    prompt = USER_PROMPT_SINGLE_PASS.format(event_text=event_text)
    return query_with_validated_tags(
        client, SYSTEM_PROMPT, prompt, EVENT_REQUIRED_TAGS,
        model=model, archive_label=archive_label,
    )


def _summarize_multi_pass(
    stage_texts: list[tuple[str, str]],
    client: LLMClient,
    *,
    model: Optional[str] = None,
    archive_label: Optional[str] = None,
) -> str:
    stage_blocks: list[str] = []
    for i, (_, stage_text) in enumerate(stage_texts, 1):
        stage_prompt = USER_PROMPT_STAGE_REDUCE.format(stage_text=stage_text)
        out = query_with_validated_tags(
            client, SYSTEM_PROMPT, stage_prompt, STAGE_REDUCE_REQUIRED_TAGS,
            model=model, archive_label=_sub_label(archive_label, f"stage{i:02d}"),
        )
        stage_blocks.append(validate_and_rebuild(out, STAGE_REDUCE_REQUIRED_TAGS))
    merged = "\n\n---\n\n".join(stage_blocks)
    merge_prompt = USER_PROMPT_MERGE.format(stage_summaries=merged)
    return query_with_validated_tags(
        client, SYSTEM_PROMPT, merge_prompt, EVENT_REQUIRED_TAGS,
        model=model, archive_label=_sub_label(archive_label, "merge"),
    )


def _produce_event_summary(
    event_meta: dict,
    stage_texts: list[tuple[str, str]],
    src_hash: str,
    client: LLMClient,
    *,
    model: Optional[str] = None,
    backend_label: str = "",
) -> tuple[str, str]:
    """Run single/multi-pass over an event's already-read stage texts, validate
    tags, format the `.md`. Returns (passes_label, md_text). Raises LLMError /
    LLMTerminalError / KeyError / ValueError — callers decide how to surface
    those. Does NOT read files, do the skip check, or write the output."""
    multi = should_multi_pass(event_meta["total_length"], len(stage_texts))
    passes = "multi" if multi else "single"
    archive_label = event_meta["event_id"]
    body = (
        _summarize_multi_pass(stage_texts, client, model=model, archive_label=archive_label)
        if multi
        else _summarize_single_pass(stage_texts, client, model=model, archive_label=archive_label)
    )
    validated = validate_and_rebuild(body, EVENT_REQUIRED_TAGS)
    md = _format_summary_md(
        event_meta, src_hash, passes, validated,
        backend_label=backend_label, model_label=model or client.default_model,
    )
    return passes, md


def _produce_stage_summary(
    event_meta: dict,
    stage: dict,
    stage_text: str,
    src_hash: str,
    client: LLMClient,
    *,
    model: Optional[str] = None,
    backend_label: str = "",
) -> str:
    """Summarize one `<章节>` — always single-pass (no stage chunk approaches
    the multi-pass threshold). Returns the `.md` text. Raises like
    `_produce_event_summary`."""
    prompt = USER_PROMPT_STAGE_SUMMARY.format(stage_text=stage_text)
    body = query_with_validated_tags(
        client, SYSTEM_PROMPT, prompt, STAGE_SUMMARY_REQUIRED_TAGS,
        model=model, archive_label=f"{event_meta['event_id']}__stage{stage['idx']:02d}",
    )
    validated = validate_and_rebuild(body, STAGE_SUMMARY_REQUIRED_TAGS)
    return _format_stage_summary_md(
        event_meta, stage, src_hash, validated,
        backend_label=backend_label, model_label=model or client.default_model,
    )


# --- public single-event entry point ----------------------------------------


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
    out_path = paths.event_summary_path(summaries_root, event_id)
    try:
        stage_texts = _read_stage_texts(event_dir, event_meta["stages"])
        src_hash = hash_stage_texts(stage_texts)
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
        passes, md = _produce_event_summary(
            event_meta, stage_texts, src_hash, client,
            model=model, backend_label=backend_label,
        )
        _io.atomic_write_text(out_path, md)
        return SummaryResult(
            event_id=event_id, status="wrote", summary_path=out_path,
            passes=passes, source_hash=src_hash,
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


# --- manifest + prune -------------------------------------------------------


def load_summaries_manifest(summaries_root: Path) -> dict:
    return _io.read_json_or(
        paths.summaries_manifest_path(summaries_root),
        {"version": 1, "events": {}, "stages": {}},
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


def prune_stale_stage_summaries(
    summaries_root: Path, current_stage_keys: set[str]
) -> list[str]:
    """Remove kb_summaries/stages/<event_id>/<NN>.md files whose
    `"<event_id>/<NN>"` key isn't in `current_stage_keys`, plus any event
    directory left empty. Returns the removed keys, sorted."""
    root = paths.stages_summary_root(summaries_root)
    if not root.is_dir():
        return []
    removed: list[str] = []
    for ev_dir in sorted(root.iterdir()):
        if not ev_dir.is_dir():
            continue
        for md in sorted(ev_dir.glob("*.md")):
            key = f"{ev_dir.name}/{md.stem}"
            if key not in current_stage_keys:
                md.unlink()
                removed.append(key)
        if not any(ev_dir.iterdir()):
            ev_dir.rmdir()
    return sorted(removed)


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
EST_STAGE_SUMMARY_OUT_CHARS = 500

# Rough chars→tokens divisor for the CJK-dominant KB text. Gemini/Claude both
# tokenize common Chinese chars at roughly 1 token each, English IDs/markup at
# ~4 chars/token — net effect on this corpus is a hair under 1:1, so 1.0 is a
# safe slight-overestimate. Tune if real usage numbers say otherwise.
EST_CHARS_PER_TOKEN = 1.0

_PROMPT_OVERHEAD_SINGLE = len(SYSTEM_PROMPT) + len(USER_PROMPT_SINGLE_PASS)
_PROMPT_OVERHEAD_STAGE = len(SYSTEM_PROMPT) + len(USER_PROMPT_STAGE_REDUCE)
_PROMPT_OVERHEAD_MERGE = len(SYSTEM_PROMPT) + len(USER_PROMPT_MERGE)
_PROMPT_OVERHEAD_STAGE_SUMMARY = len(SYSTEM_PROMPT) + len(USER_PROMPT_STAGE_SUMMARY)


@dataclass
class EventCostEstimate:
    event_id: str  # event_id, or "<event_id>/<NN>" for a stage row
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
    """Projected one-run cost for a single event. Mirrors `_produce_event_summary`'s
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


def estimate_stage_cost(key: str, total_length: int) -> EventCostEstimate:
    """Projected one-run cost for one `<章节>` summary — always one call."""
    return EventCostEstimate(
        event_id=key,
        passes="single",
        stage_count=1,
        total_length=total_length,
        llm_calls=1,
        in_chars=_PROMPT_OVERHEAD_STAGE_SUMMARY + total_length,
        out_chars=EST_STAGE_SUMMARY_OUT_CHARS,
    )


def est_tokens(row: EventCostEstimate) -> int:
    return round(row.total_chars / EST_CHARS_PER_TOKEN)


def _would_run(key: str, out_path: Path, manifest_section: dict, force: bool) -> bool:
    """A unit "would run" iff `force`, or it has no manifest entry, or its
    output `.md` is missing. Does NOT re-hash source text — a content change
    in an already-summarized unit is invisible here and gets caught (and
    re-billed) inside the run loop. Shared by the dry-run estimate and the
    live batch's run-plan."""
    return force or manifest_section.get(key) is None or not out_path.exists()


def _classify_event_run(
    events_meta: dict[str, dict],
    manifest_events: dict,
    summaries_root: Path,
    force: bool,
) -> tuple[list[EventCostEstimate], list[EventCostEstimate]]:
    to_run: list[EventCostEstimate] = []
    done: list[EventCostEstimate] = []
    for event_id, meta in sorted(events_meta.items()):
        row = estimate_event_cost(event_id, meta["total_length"], len(meta["stages"]))
        out_path = paths.event_summary_path(summaries_root, event_id)
        (to_run if _would_run(event_id, out_path, manifest_events, force) else done).append(row)
    return to_run, done


def _classify_stage_run(
    events_meta: dict[str, dict],
    manifest_stages: dict,
    summaries_root: Path,
    force: bool,
) -> tuple[list[EventCostEstimate], list[EventCostEstimate]]:
    to_run: list[EventCostEstimate] = []
    done: list[EventCostEstimate] = []
    for event_id, meta in sorted(events_meta.items()):
        for stage in meta["stages"]:
            key = f"{event_id}/{stage['idx']:02d}"
            row = estimate_stage_cost(key, stage["length"])
            out_path = paths.stage_summary_path(summaries_root, event_id, stage["idx"])
            (to_run if _would_run(key, out_path, manifest_stages, force) else done).append(row)
    return to_run, done


@dataclass
class ProgressEvent:
    """One per unit as a batch works through — fed to the optional `progress`
    callback so the caller can print a live status line. Token counts are
    estimates (see `_would_run`); `eta_s` is None until at least one unit has
    actually been written this run. `event_id` is the event_id for an event
    bake, or `"<event_id>/<NN>"` for a stage bake."""
    index: int            # 1-based position in the iteration
    total: int            # total units iterated this run
    event_id: str
    status: str           # "wrote" | "skipped_unchanged" | "error" | "terminal_error"
    passes: str           # "single" | "multi" | ""
    run_done: int         # units written so far this run
    run_total: int        # units that will be written this run (est.)
    tokens_done: int      # est. tokens spent so far this run
    tokens_total: int     # est. tokens this run will spend
    elapsed_s: float
    eta_s: Optional[float]


# --- generic hash-gated batch runner ---------------------------------------


@dataclass
class _BatchUnit:
    """One thing to summarize within a batch — a whole event, or one stage.

    `load()` reads the source text(s) and returns (name, text) pairs (its hash
    is the skip-on-unchanged key); it may raise OSError/KeyError. `summarize()`
    takes those texts plus the precomputed hash and returns the final `.md`
    body to write; it may raise LLMError/LLMTerminalError/ValueError.
    `manifest_entry` is the per-unit dict stored under the batch's manifest
    section once the write lands — the runner adds `source_hash`,
    `summary_path`, `generated_at`."""
    key: str
    out_path: Path
    summary_rel_path: str          # relative to summaries_root, for the manifest
    est: EventCostEstimate
    manifest_entry: dict
    load: Callable[[], list[tuple[str, str]]]
    summarize: Callable[[list[tuple[str, str]], str], str]


def _run_batch(
    units: list[_BatchUnit],
    manifest_section: dict,
    *,
    force: bool,
    persist: Callable[[], None],
    progress: Optional[Callable[[ProgressEvent], None]],
) -> SummarizeReport:
    """Iterate `units` in order. For each: read source, hash, skip if the
    manifest already has that hash and the `.md` exists; otherwise summarize,
    write, record the manifest entry, and `persist()` immediately (so a kill /
    quota wall mid-batch never loses paid-for work). A terminal LLM error
    stops the whole batch (`report.terminal_error` set); a per-unit error is
    recorded and the batch continues."""
    report = SummarizeReport()
    run_plan = [u for u in units if _would_run(u.key, u.out_path, manifest_section, force)]
    run_total = len(run_plan)
    tokens_total = sum(est_tokens(u.est) for u in run_plan)
    total_iter = len(units)
    run_done = 0
    tokens_done = 0
    t0 = time.monotonic()

    def _emit(idx: int, key: str, status: str, passes: str) -> None:
        if progress is None:
            return
        elapsed = time.monotonic() - t0
        eta: Optional[float] = None
        if tokens_done > 0 and tokens_total > tokens_done:
            eta = (elapsed / tokens_done) * (tokens_total - tokens_done)
        progress(ProgressEvent(
            index=idx, total=total_iter, event_id=key, status=status, passes=passes,
            run_done=run_done, run_total=run_total,
            tokens_done=tokens_done, tokens_total=tokens_total,
            elapsed_s=elapsed, eta_s=eta,
        ))

    for idx, u in enumerate(units, 1):
        try:
            source_texts = u.load()
        except (OSError, KeyError) as e:
            report.errors.append((u.key, f"{type(e).__name__}: {e}"))
            _emit(idx, u.key, "error", u.est.passes)
            continue
        src_hash = hash_stage_texts(source_texts)

        prior = manifest_section.get(u.key)
        if not force and prior and prior.get("source_hash") == src_hash and u.out_path.exists():
            report.skipped.append(u.key)
            _emit(idx, u.key, "skipped_unchanged", u.est.passes)
            continue

        try:
            md = u.summarize(source_texts, src_hash)
        except LLMTerminalError as e:
            report.terminal_error = f"{type(e).__name__}: {e}"
            report.errors.append((u.key, report.terminal_error))
            _emit(idx, u.key, "terminal_error", "")
            return report
        except (LLMError, OSError, KeyError, ValueError) as e:
            report.errors.append((u.key, f"{type(e).__name__}: {e}"))
            _emit(idx, u.key, "error", u.est.passes)
            continue

        try:
            _io.atomic_write_text(u.out_path, md)
        except OSError as e:
            report.errors.append((u.key, f"{type(e).__name__}: {e}"))
            _emit(idx, u.key, "error", u.est.passes)
            continue

        manifest_section[u.key] = {
            "source_hash": src_hash,
            "summary_path": u.summary_rel_path,
            **u.manifest_entry,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        persist()
        report.wrote.append(u.key)
        run_done += 1
        tokens_done += est_tokens(u.est)
        _emit(idx, u.key, "wrote", u.est.passes)

    return report


def _common_unit_fields(backend_label: str, model_label: str) -> dict:
    return {"backend": backend_label, "model": model_label}


# --- event bake -------------------------------------------------------------


def _event_units(
    events_meta: dict[str, dict],
    kb_root: Path,
    summaries_root: Path,
    client: LLMClient,
    *,
    model: Optional[str],
    backend_label: str,
) -> list[_BatchUnit]:
    model_label = model or client.default_model
    units: list[_BatchUnit] = []
    for event_id, meta in sorted(events_meta.items()):
        ev_dir = paths.event_dir(kb_root, event_id)
        out_path = paths.event_summary_path(summaries_root, event_id)
        # estimate_event_cost can KeyError on a malformed event.json; that's a
        # build bug, not a per-event hiccup — let it raise rather than swallow.
        est = estimate_event_cost(event_id, meta["total_length"], len(meta["stages"]))
        manifest_entry = {
            "passes": est.passes,
            "total_length": meta["total_length"],
            "stage_count": len(meta["stages"]),
            **_common_unit_fields(backend_label, model_label),
        }

        def _load(meta=meta, ev_dir=ev_dir) -> list[tuple[str, str]]:
            return _read_stage_texts(ev_dir, meta["stages"])

        def _summarize(texts, src_hash, meta=meta) -> str:
            _, md = _produce_event_summary(
                meta, texts, src_hash, client, model=model, backend_label=backend_label
            )
            return md

        units.append(_BatchUnit(
            key=event_id,
            out_path=out_path,
            summary_rel_path=str(out_path.relative_to(summaries_root)),
            est=est,
            manifest_entry=manifest_entry,
            load=_load,
            summarize=_summarize,
        ))
    return units


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
    """Summarize every event under `events_root(kb_root)` (or just `only`).

    `force`: ignore source-hash cache and re-run.
    `prune`: drop kb_summaries/events/<id>.md not in the current build — only
        when iterating the full corpus (`only is None`) and the batch ran to
        completion (no terminal LLM error).
    `progress`: per-event callback for a live status line (see `_run_batch`).
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
    units = _event_units(
        events_meta, kb_root, summaries_root, client,
        model=model, backend_label=backend_label,
    )
    report = _run_batch(
        units, manifest_events, force=force,
        persist=lambda: save_summaries_manifest(summaries_root, manifest),
        progress=progress,
    )

    if report.terminal_error:
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


def estimate_remaining(
    kb_root: Path,
    summaries_root: Path,
    *,
    only: Optional[Iterable[str]] = None,
    force: bool = False,
) -> CostEstimate:
    """Estimate the token/char cost of the next event `summarize_all` run
    without touching an LLM. Selection mirrors `summarize_all`. Deliberately
    does NOT re-hash stage text — a *content change* in an already-summarized
    event won't show here; it gets caught (and re-billed) at run time."""
    only_list = list(only) if only else None
    events_meta = _load_events_meta(kb_root, only_list)
    manifest_events = load_summaries_manifest(summaries_root).get("events", {})
    to_run, done = _classify_event_run(events_meta, manifest_events, summaries_root, force)
    return CostEstimate(to_run=to_run, already_done=done)


# --- per-stage bake ---------------------------------------------------------


def _stage_units(
    events_meta: dict[str, dict],
    kb_root: Path,
    summaries_root: Path,
    client: LLMClient,
    *,
    model: Optional[str],
    backend_label: str,
) -> list[_BatchUnit]:
    model_label = model or client.default_model
    units: list[_BatchUnit] = []
    for event_id, meta in sorted(events_meta.items()):
        ev_dir = paths.event_dir(kb_root, event_id)
        for stage in meta["stages"]:
            idx = stage["idx"]
            key = f"{event_id}/{idx:02d}"
            out_path = paths.stage_summary_path(summaries_root, event_id, idx)
            est = estimate_stage_cost(key, stage["length"])
            manifest_entry = {
                "event_id": event_id,
                "stage_idx": idx,
                "stage_name": stage["name"],
                "total_length": stage["length"],
                **_common_unit_fields(backend_label, model_label),
            }

            def _load(ev_dir=ev_dir, stage=stage) -> list[tuple[str, str]]:
                return [(stage["file"], (ev_dir / stage["file"]).read_text(encoding="utf-8"))]

            def _summarize(texts, src_hash, meta=meta, stage=stage) -> str:
                return _produce_stage_summary(
                    meta, stage, texts[0][1], src_hash, client,
                    model=model, backend_label=backend_label,
                )

            units.append(_BatchUnit(
                key=key,
                out_path=out_path,
                summary_rel_path=str(out_path.relative_to(summaries_root)),
                est=est,
                manifest_entry=manifest_entry,
                load=_load,
                summarize=_summarize,
            ))
    return units


def summarize_all_stages(
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
    """Bake one summary per `<章节>` → `kb_summaries/stages/<event_id>/<NN>.md`.

    `only`: restrict to these event_ids' stages (otherwise: every stage).
    `force` / `progress`: as in `summarize_all`.
    `prune`: drop kb_summaries/stages/<event_id>/<NN>.md (and empty event dirs)
        not in the current build — only on a full, completed run.
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
    manifest_stages = manifest.setdefault("stages", {})
    units = _stage_units(
        events_meta, kb_root, summaries_root, client,
        model=model, backend_label=backend_label,
    )
    report = _run_batch(
        units, manifest_stages, force=force,
        persist=lambda: save_summaries_manifest(summaries_root, manifest),
        progress=progress,
    )

    if report.terminal_error:
        return report

    manifest_changed = bool(report.wrote or report.errors)
    if prune and only_list is None:
        current_keys = {u.key for u in units}
        report.pruned = prune_stale_stage_summaries(summaries_root, current_keys)
        stale_in_manifest = [k for k in manifest_stages if k not in current_keys]
        for stale in stale_in_manifest:
            del manifest_stages[stale]
        if report.pruned or stale_in_manifest:
            manifest_changed = True

    if manifest_changed:
        save_summaries_manifest(summaries_root, manifest)
    return report


def estimate_remaining_stages(
    kb_root: Path,
    summaries_root: Path,
    *,
    only: Optional[Iterable[str]] = None,
    force: bool = False,
) -> CostEstimate:
    """Estimate the cost of the next `summarize_all_stages` run — no LLM.
    Like `estimate_remaining`, does not re-hash chunk text."""
    only_list = list(only) if only else None
    events_meta = _load_events_meta(kb_root, only_list)
    manifest_stages = load_summaries_manifest(summaries_root).get("stages", {})
    to_run, done = _classify_stage_run(events_meta, manifest_stages, summaries_root, force)
    return CostEstimate(to_run=to_run, already_done=done)
