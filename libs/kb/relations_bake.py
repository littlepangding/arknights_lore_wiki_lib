"""LLM relation-extraction bake — typed assertions from each operator's handbook.

Per char, one LLM call reads `profile.txt` + `archive.txt` + `voice.txt`
and emits a controlled set of typed relations (member_of, mentor_of,
identifies_as, …). Output is one JSONL file per char under
``kb_relations/chars/<char_id>.jsonl``; ``kb_build`` then collates
those plus the curated override into ``data/kb/relations.jsonl`` for
``kb_query relations …``.

Architecture mirrors :mod:`libs.kb.summarize`: hash-gated, resume-safe,
manifest-persisted-after-every-write, terminal-LLM-error-stops-the-batch.
Re-uses :func:`summarize._run_batch` for the loop and :class:`_BatchUnit`
for the per-unit shape — the batch driver doesn't care whether the
written body is Markdown or JSONL.

Tail resolution: the LLM emits surface names; this module looks them
up against ``KB.entity_alias_to_ids`` (built at indexer time from the
entity layer). A `Resolved` tail keeps `tail=entity_id` *and* the
surface in `tail_name`; an `Ambiguous` tail emits `tail=null` plus
`ambiguous_candidates`; a `Missing` tail is dropped with a warning so
the curator can add it to ``entities_curated.jsonl`` and re-bake.

Token posture: ≈one-call-per-char × ~444 chars. Handbook sections sum
to ~5-30KB each, so ~5-15M total tokens — about half the stage-summary
bake's spend. Single-pass by construction (no handbook is anywhere near
the multi-pass threshold).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from libs.bases import LLMError, LLMTerminalError, extract_tagged_contents
from libs.kb import _io, paths
from libs.kb.summarize import (
    EST_CHARS_PER_TOKEN,
    EventCostEstimate,
    ProgressEvent,
    SummarizeReport,
    _BatchUnit,
    _run_batch,
    _would_run,
    est_tokens,
    hash_stage_texts,
)
from libs.llm_clients import LLMClient, query_with_validated_tags


# --- controlled vocabulary -------------------------------------------

# The 9 starter relation types. Novel types from the LLM aren't rejected
# — they're flagged in `parse_warnings` so the curator can see whether
# they're real signal that deserves a new vocabulary entry or just LLM
# drift. Keeping `type` a free string at the JSONL layer (per the
# relations.py docstring) means we don't have to migrate stored rows
# when the vocabulary grows.
RELATION_TYPES: tuple[str, ...] = (
    "member_of",        # head 属于 tail (组织/团体)
    "ally_of",          # 同伴/盟友 (mutual)
    "rival_of",         # 对手 (mutual)
    "family_of",        # 血缘 / 家族 (qualifier 写到 notes)
    "mentor_of",        # head 教导 tail
    "subordinate_of",   # head 隶属 tail (上下级)
    "creator_of",       # head 创建 / 创造 tail
    "identifies_as",    # head 的另一身份是 tail
    "origin_from",      # head 来自 tail (地点 / 国家)
)

RELATIONS_REQUIRED_TAGS: list[str] = ["关系"]


# --- prompts (zh; mirrors docs/PROMPTS.md style) ----------------------

SYSTEM_PROMPT_RELATIONS = (
    "你是一个明日方舟剧情资料编写助手。"
    "你的任务是阅读一名干员的档案文本，提取该干员与其他实体（组织、地点、其他角色）之间的"
    "类型化关系，仅供索引和检索。你严格遵守输出格式，使用简体中文，不引申、不评价、"
    "不揣测原作未交代的内容。"
)

USER_PROMPT_RELATIONS = """以下是明日方舟干员 **{char_name}** 的档案文本（含基础档案、客观履历、语音、回忆等）。请基于这些文本，提取该干员与其他实体之间的 **类型化关系**。

输出格式：
<关系>
type;tail;notes
</关系>

每行一条关系。字段含义：

- `type`（关系类型，必填）：仅使用以下 9 个之一。novel 类型会被记录为警告，请尽量使用预设值。
{relation_types_list}
- `tail`（关系对象，必填）：该关系指向的实体名称（人物名 / 组织名 / 地点名）。请使用档案文本中出现的最规范的名称（如「罗德岛」而不是「岛」；如「凯尔希」而不是「医生」）。**`head` 自动设为本干员（{char_name}），不需要写出。**
- `notes`（说明，选填）：简短中文说明（≤30 字），可省略，但 `family_of` / `identifies_as` 这类需要语境的关系建议写明（例如 `family_of;<姐姐名>;长姐`）。

允许多条同一类型的关系（如多位同伴）。每行只写一条。

【硬性要求】
- 严格使用简体中文，不要使用繁体或日文汉字。
- **只提取档案中明确陈述或强烈暗示的关系**，不要凭剧情常识或泛文化背景补全。
- 不要列出"博士"作为 tail —— 博士与每位干员都有关系，没有索引价值。
- 不要把"罗德岛"列为 `member_of` 之外的类型（如 `ally_of;罗德岛` 没有意义）。
- 如果该干员档案中没有可提取的明确关系，输出空的 `<关系>` 标签即可：

<关系>
</关系>

干员档案：
{handbook_text}
"""


# --- handbook loading ------------------------------------------------

_HANDBOOK_SECTIONS: tuple[str, ...] = ("profile", "archive", "voice")


def read_char_handbook(kb_root: Path, char_id: str) -> list[tuple[str, str]]:
    """Load the bake's input — `profile.txt` + `archive.txt` + `voice.txt`
    when present. Each tuple is `(filename, text)` so the same hash
    function the summary bake uses (`hash_stage_texts`) keys this off
    the same shape. Missing sections are simply omitted (a char with
    no archive yet still gets baked over whatever it has)."""
    out: list[tuple[str, str]] = []
    cdir = paths.char_dir(kb_root, char_id)
    for sec in _HANDBOOK_SECTIONS:
        p = cdir / f"{sec}.txt"
        if p.is_file():
            out.append((f"{sec}.txt", p.read_text(encoding="utf-8")))
    return out


def handbook_total_length(handbook: list[tuple[str, str]]) -> int:
    return sum(len(t) for _, t in handbook)


# --- prompt building -------------------------------------------------


def _format_relation_types_list() -> str:
    """Bullet list of vocabulary items for the prompt. Built once."""
    bullets = {
        "member_of": "head 属于 tail（组织或团体），例：`member_of;罗德岛`",
        "ally_of": "head 与 tail 互为同伴 / 盟友（双向），例：`ally_of;凯尔希`",
        "rival_of": "head 与 tail 互为对手（双向），例：`rival_of;某某`",
        "family_of": "head 与 tail 有血缘 / 家族关系（具体关系写到 notes），例：`family_of;<姐姐名>;长姐`",
        "mentor_of": "head 教导过 tail，例：`mentor_of;<徒弟名>`",
        "subordinate_of": "head 在工作 / 阶层上隶属于 tail（上下级），例：`subordinate_of;凯尔希`",
        "creator_of": "head 创建 / 创造了 tail，例：`creator_of;某社团`",
        "identifies_as": "head 的另一个身份是 tail（同一人物的不同名号 / 化身 / 过去身份）",
        "origin_from": "head 来自 tail（出身地、国籍、阵营出身），例：`origin_from;炎国`",
    }
    return "\n".join(f"  - `{t}` — {bullets[t]}" for t in RELATION_TYPES)


_RELATION_TYPES_LIST_RENDERED = _format_relation_types_list()


def build_user_prompt(char_name: str, handbook: list[tuple[str, str]]) -> str:
    """The full per-char user prompt. The handbook sections are joined
    with section-name headers so the LLM can tell them apart."""
    body_chunks = []
    for fname, text in handbook:
        body_chunks.append(f"=== {fname} ===\n{text.strip()}")
    return USER_PROMPT_RELATIONS.format(
        char_name=char_name,
        relation_types_list=_RELATION_TYPES_LIST_RENDERED,
        handbook_text="\n\n".join(body_chunks),
    )


_PROMPT_OVERHEAD = len(SYSTEM_PROMPT_RELATIONS) + len(
    USER_PROMPT_RELATIONS.format(
        char_name="X" * 4,
        relation_types_list=_RELATION_TYPES_LIST_RENDERED,
        handbook_text="",
    )
)


# --- response parsing ------------------------------------------------

# A bake line is `type;tail[;notes]` — semicolons inside `notes` are
# tolerated by capping the split to 3. Empty lines, all-whitespace
# lines, and `# comment` lines are ignored.
_LINE_RE = re.compile(r"^\s*([^;]+?)\s*;\s*([^;]+?)\s*(?:;\s*(.+?))?\s*$")


@dataclass
class _ParsedLine:
    type: str
    tail_surface: str
    notes: Optional[str]
    line_no: int  # 1-based, within the <关系> block


def parse_relations_block(body: str) -> tuple[list[_ParsedLine], list[dict]]:
    """Pull the `<关系>` block, parse each non-comment non-blank line.
    Returns `(rows, parse_warnings)`. An entirely empty / `无` block is
    valid (the char genuinely asserts no typed relations) — returns
    `([], [])`."""
    blocks = extract_tagged_contents(body, "关系")
    warnings: list[dict] = []
    rows: list[_ParsedLine] = []
    if not blocks:
        warnings.append({"reason": "missing <关系> tag"})
        return rows, warnings
    raw = blocks[0].strip()
    if not raw or raw == "无":
        return rows, warnings
    for i, line in enumerate(raw.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE_RE.match(s)
        if not m:
            warnings.append({"line_no": i, "raw": line, "reason": "unparseable line"})
            continue
        rtype, tail, notes = m.group(1).strip(), m.group(2).strip(), m.group(3)
        if not rtype or not tail:
            warnings.append({"line_no": i, "raw": line, "reason": "empty type or tail"})
            continue
        rows.append(
            _ParsedLine(
                type=rtype,
                tail_surface=tail,
                notes=(notes.strip() if notes else None),
                line_no=i,
            )
        )
    return rows, warnings


# --- tail resolution -------------------------------------------------


def resolve_tail(
    surface: str, entity_alias_to_ids: dict[str, list[str]]
) -> tuple[Optional[str], list[str], str]:
    """`(entity_id, ambiguous_candidates, status)`. `status` ∈
    `{"resolved", "ambiguous", "missing"}`. The caller decides what to do
    with each — `Resolved` keeps the row, `Ambiguous` keeps it with
    `tail=null`+`ambiguous_candidates`, `Missing` drops it with a warning."""
    ids = entity_alias_to_ids.get(surface, [])
    if not ids:
        return None, [], "missing"
    if len(ids) == 1:
        return ids[0], [], "resolved"
    return None, list(ids), "ambiguous"


# --- per-char row assembly -------------------------------------------


def assemble_char_rows(
    char_id: str,
    parsed: list[_ParsedLine],
    entity_alias_to_ids: dict[str, list[str]],
) -> tuple[list[dict], list[dict]]:
    """Convert parsed LLM lines into final JSONL rows + resolution
    warnings. `Missing` tails do not produce rows — they land in
    `warnings` so the curator can add an `entities_curated.jsonl`
    entry and re-bake. `Ambiguous` tails produce rows with
    `tail: null` and `ambiguous_candidates` so the assertion isn't
    silently lost — the curator narrows the alias later."""
    rows: list[dict] = []
    warnings: list[dict] = []
    for p in parsed:
        tail_id, ambig, status = resolve_tail(p.tail_surface, entity_alias_to_ids)
        if status == "missing":
            warnings.append(
                {
                    "line_no": p.line_no,
                    "type": p.type,
                    "tail_surface": p.tail_surface,
                    "reason": "unresolved tail — no entity alias matches",
                }
            )
            continue
        row: dict = {
            "head": char_id,
            "type": p.type,
            "tail": tail_id,  # entity_id when resolved; null when ambiguous
            "tail_name": p.tail_surface,
        }
        if status == "ambiguous":
            row["ambiguous_candidates"] = ambig
        if p.notes:
            row["notes"] = p.notes
        if p.type not in RELATION_TYPES:
            warnings.append(
                {
                    "line_no": p.line_no,
                    "type": p.type,
                    "tail_surface": p.tail_surface,
                    "reason": f"novel relation type {p.type!r} (not in starter vocabulary)",
                }
            )
        rows.append(row)
    return rows, warnings


def rows_to_jsonl(rows: list[dict]) -> str:
    """JSONL serialisation — atomic-write-safe (caller hands the result
    to `_io.atomic_write_text`)."""
    if not rows:
        return ""  # an empty file is a valid "no relations" outcome
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


# --- per-char bake top-level ----------------------------------------


def produce_char_relations(
    char_id: str,
    char_name: str,
    handbook: list[tuple[str, str]],
    src_hash: str,
    client: LLMClient,
    entity_alias_to_ids: dict[str, list[str]],
    *,
    model: Optional[str] = None,
    backend_label: str = "",
) -> tuple[str, list[dict], list[dict]]:
    """Run one LLM call, parse, resolve. Returns `(jsonl_body, rows,
    warnings)`. Raises `LLMError`/`LLMTerminalError`/`ValueError` —
    caller (batch runner) decides whether to retry or surface the error.

    `src_hash` is recorded in the manifest by the caller; it is not
    embedded in the JSONL output (the per-char file is small enough
    that the manifest is the right place for metadata)."""
    prompt = build_user_prompt(char_name, handbook)
    body = query_with_validated_tags(
        client,
        SYSTEM_PROMPT_RELATIONS,
        prompt,
        RELATIONS_REQUIRED_TAGS,
        model=model,
        archive_label=f"relations__{char_id}",
    )
    parsed, parse_warnings = parse_relations_block(body)
    rows, resolve_warnings = assemble_char_rows(char_id, parsed, entity_alias_to_ids)
    return rows_to_jsonl(rows), rows, parse_warnings + resolve_warnings


# --- manifest -------------------------------------------------------


def load_relations_manifest(relations_root: Path) -> dict:
    """Mirrors :func:`summarize.load_summaries_manifest` — same shape,
    own file at ``kb_relations/manifest.json`` so the two bakes never
    contend on disk."""
    return _io.read_json_or(
        paths.relations_manifest_path(relations_root),
        {"version": 1, "chars": {}},
    )


def save_relations_manifest(relations_root: Path, data: dict) -> None:
    data["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _io.atomic_write_json(paths.relations_manifest_path(relations_root), data)


# --- cost estimation (no LLM, no token spend) -----------------------

# Output side: ~9 relation types max, ~5-15 rows per char, ~80 chars
# per row (type + tail + notes + JSON markup). 1000 chars is a slightly
# generous ceiling.
EST_RELATIONS_OUT_CHARS = 1000


def estimate_char_cost(
    char_id: str, handbook_chars: int
) -> EventCostEstimate:
    """Projected one-run cost for one char's relation bake — always one
    LLM call. Mirrors :func:`summarize.estimate_event_cost` shape so the
    same display code works."""
    return EventCostEstimate(
        event_id=char_id,
        passes="single",
        stage_count=len(_HANDBOOK_SECTIONS),
        total_length=handbook_chars,
        llm_calls=1,
        in_chars=_PROMPT_OVERHEAD + handbook_chars,
        out_chars=EST_RELATIONS_OUT_CHARS,
    )


# --- char selection -------------------------------------------------


def _select_chars(
    char_manifests: dict[str, dict],
    only: Optional[Iterable[str]],
) -> dict[str, dict]:
    """Restrict to the requested char_ids when `only` is set. Drops
    nameless chars (no display name → no useful bake target)."""
    selected = {
        cid: mf for cid, mf in char_manifests.items() if mf.get("name")
    }
    if only:
        only_set = set(only)
        selected = {cid: mf for cid, mf in selected.items() if cid in only_set}
    return selected


# --- public batch entry points --------------------------------------


def _char_units(
    chars: dict[str, dict],
    kb_root: Path,
    relations_root: Path,
    client: LLMClient,
    entity_alias_to_ids: dict[str, list[str]],
    *,
    model: Optional[str],
    backend_label: str,
) -> list[_BatchUnit]:
    model_label = model or client.default_model
    units: list[_BatchUnit] = []
    for cid in sorted(chars):
        mf = chars[cid]
        out_path = paths.char_relations_path(relations_root, cid)
        # Pre-read handbook once so `load()` returns the same texts the
        # `summarize()` closure consumes (and the same hash is keyed on).
        # The closures below capture `cid` / `mf` / `client` etc by
        # default-arg trick to avoid late-binding bugs across the loop.
        def _load(cid=cid):
            return read_char_handbook(kb_root, cid)

        def _summarize(
            source_texts,
            src_hash,
            cid=cid,
            mf=mf,
            client=client,
            model=model,
            backend_label=backend_label,
        ):
            jsonl_body, _rows, _warns = produce_char_relations(
                cid, mf["name"], source_texts, src_hash, client,
                entity_alias_to_ids, model=model, backend_label=backend_label,
            )
            return jsonl_body

        # Handbook length is unknown until load(); for estimation we use
        # whatever the manifest already saw, or 0 if first-pass — the
        # `--estimate` path uses `estimate_remaining_relations` instead
        # of this, so this number is only the in-batch ETA approximation.
        rough_len = sum(
            (paths.char_dir(kb_root, cid) / f"{s}.txt").stat().st_size
            for s in _HANDBOOK_SECTIONS
            if (paths.char_dir(kb_root, cid) / f"{s}.txt").is_file()
        )
        units.append(
            _BatchUnit(
                key=cid,
                out_path=out_path,
                summary_rel_path=str(out_path.relative_to(relations_root)),
                est=estimate_char_cost(cid, rough_len),
                manifest_entry={"backend": backend_label, "model": model_label},
                load=_load,
                summarize=_summarize,
            )
        )
    return units


def bake_relations_all(
    kb_root: Path,
    relations_root: Path,
    client: LLMClient,
    entity_alias_to_ids: dict[str, list[str]],
    char_manifests: dict[str, dict],
    *,
    only: Optional[Iterable[str]] = None,
    force: bool = False,
    prune: bool = True,
    backend_label: str = "",
    model: Optional[str] = None,
    progress: Optional[Callable[[ProgressEvent], None]] = None,
) -> SummarizeReport:
    """Bake one LLM call per selected char, hash-gated and resume-safe.
    Mirrors :func:`summarize.summarize_all`'s contract."""
    selected = _select_chars(char_manifests, only)
    manifest = load_relations_manifest(relations_root)
    manifest.setdefault("chars", {})
    section = manifest["chars"]

    paths.relations_chars_root(relations_root).mkdir(parents=True, exist_ok=True)

    units = _char_units(
        selected, kb_root, relations_root, client, entity_alias_to_ids,
        model=model, backend_label=backend_label,
    )

    def _persist() -> None:
        save_relations_manifest(relations_root, manifest)

    report = _run_batch(
        units, section,
        force=force, persist=_persist, progress=progress,
    )

    if prune and not report.terminal_error:
        report.pruned = prune_stale_char_relations(relations_root, set(selected))
        # Strip manifest entries for pruned chars so a future run sees
        # them as truly absent rather than "cached but file deleted".
        for cid in report.pruned:
            section.pop(cid, None)

    _persist()
    return report


def prune_stale_char_relations(
    relations_root: Path, current_char_ids: set[str]
) -> list[str]:
    """Drop `kb_relations/chars/<cid>.jsonl` for chars not in the current
    selection. Returns char_ids removed, sorted."""
    keep = {f"{cid}.jsonl" for cid in current_char_ids}
    removed = _io.prune_stale_files(
        paths.relations_chars_root(relations_root), "*.jsonl", keep
    )
    return [Path(name).stem for name in removed]


# --- cost estimation (matches the summarize `CostEstimate` shape) ----


@dataclass
class RelationsCostEstimate:
    to_run: list[EventCostEstimate] = field(default_factory=list)
    already_done: list[EventCostEstimate] = field(default_factory=list)

    @property
    def n_to_run(self) -> int:
        return len(self.to_run)

    @property
    def n_single(self) -> int:
        return len(self.to_run)

    @property
    def n_multi(self) -> int:
        return 0  # the relations bake is always single-pass

    @property
    def llm_calls(self) -> int:
        return sum(r.llm_calls for r in self.to_run)

    @property
    def in_chars(self) -> int:
        return sum(r.in_chars for r in self.to_run)

    @property
    def out_chars(self) -> int:
        return sum(r.out_chars for r in self.to_run)

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


def estimate_remaining_relations(
    kb_root: Path,
    relations_root: Path,
    char_manifests: dict[str, dict],
    *,
    only: Optional[Iterable[str]] = None,
    force: bool = False,
) -> RelationsCostEstimate:
    """Dry-run cost — no LLM, no token spend. Reads the on-disk handbook
    file sizes (via `stat`, not `read_text`, so even thousands of chars
    scan in milliseconds) and classifies each char as run-now-vs-cached
    using the manifest. Mirrors :func:`summarize.estimate_remaining_stages`."""
    selected = _select_chars(char_manifests, only)
    manifest = load_relations_manifest(relations_root)
    section = manifest.get("chars", {})
    est = RelationsCostEstimate()
    for cid in sorted(selected):
        handbook_chars = 0
        for sec in _HANDBOOK_SECTIONS:
            p = paths.char_dir(kb_root, cid) / f"{sec}.txt"
            if p.is_file():
                handbook_chars += p.stat().st_size
        if handbook_chars == 0:
            # No handbook → nothing to bake. Skip silently — these chars
            # also won't show up in run output.
            continue
        row = estimate_char_cost(cid, handbook_chars)
        out_path = paths.char_relations_path(relations_root, cid)
        (est.to_run if _would_run(cid, out_path, section, force) else est.already_done).append(row)
    return est
