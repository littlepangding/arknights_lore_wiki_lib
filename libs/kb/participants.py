"""WS-0 — tiered char↔stage participant edges (replaces the flat
substring-count inferred pass).

For each stage, classify every char by how the stage *uses* them:

- ``speaker``   — the cleaned chunk text has ≥1 line-leading ``名字:台词``
  dialogue line for one of the char's aliases. The speaker list is
  already materialized by ``game_data.clean_script``'s
  ``[name="X"]Y`` / ``[multiline(name="X")]Y`` → ``X:Y`` rewrite, so we
  just read it back. Highest precision — this is what "appears in"
  should mean by default.
- ``named``     — an alias appears in narration: a multi-char *canonical*
  name (substring is safe at ≥2 zh chars), an ASCII canonical name with
  a real word boundary (so ``W`` ⊄ ``World``), a single-zh-char
  canonical seen ≥2× (or also listed in the event summary), or any
  combination of aliases summing to ≥2 mentions.
- ``mentioned`` — a lone short / curated / single-zh-char hit and
  nothing stronger. Kept as a recall floor but tier-marked so a
  consumer can drop it.

Plus :func:`build_char_to_events_summary` — deterministic edges from the
``<关键人物>`` tag of the baked ``kb_summaries``:

* ``kb_summaries/events/<id>.md`` → *event-scoped* edges (``stage_idx``
  is ``None``).
* ``kb_summaries/stages/<event_id>/<NN>.md`` → *stage-scoped* edges
  (``stage_idx`` is the chapter index). When a char is named in a
  baked stage summary of an event, the stage breakdown subsumes the
  event-scoped edge for that ``(char, event)`` — only chars whose stage
  isn't baked yet keep their event-scoped edge (partial bakes stay
  honest).

Each surface name is resolved through the alias index. Hash-gated free
(no LLM call — it just reads the already-baked ``.md``).

Subtraction rule (tightened to per-stage where the edge has a stage): a
participant or summary edge is suppressed when the deterministic
storyset pass already covers it — per ``(char_id, event_id, stage_idx)``
for participant edges and stage-scoped summary edges, per
``(char_id, event_id)`` for the event-scoped summary edges.

Cost still scales with stages, not stages × chars: each stage file is
read once and all aliases are greped against it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Literal

from libs.bases import extract_tagged_contents
from libs.kb import paths
from libs.kb.indexer import (
    build_alias_inputs,
    compute_ambiguous_canonicals,
    load_event_manifests,
)
from libs.kb.paths import MatchClass

Tier = Literal["speaker", "named", "mentioned"]
TIERS: tuple[Tier, ...] = ("speaker", "named", "mentioned")

# Higher = stronger. Used when one char picks up edges of several tiers
# in the same stage (it shouldn't, but the precedence makes the merge
# total). `--min-tier named` keeps `speaker` + `named`, drops `mentioned`.
_TIER_RANK: dict[Tier, int] = {"speaker": 3, "named": 2, "mentioned": 1}


def tier_at_least(tier: Tier | None, minimum: Tier) -> bool:
    """`tier` clears the `--min-tier` bar. `None` (e.g. a storyset edge
    that records no participant tier) is treated as always-passing — the
    caller is expected to special-case the deterministic source anyway,
    but this keeps the predicate total."""
    if tier is None:
        return True
    return _TIER_RANK[tier] >= _TIER_RANK[minimum]


# --- speaker-line extraction ------------------------------------------

# A speaker line is `名字:台词` (ASCII colon, produced by clean_script) or
# the full-width `名字：台词` variant just in case. The name part is
# non-empty, may contain spaces ("Mr. Nothing"), but not `<`/`>` (chunk
# wrapper tags) or a colon (we split on the *first* colon — dialogue may
# contain more). `[Decision]`/`[Subtitle]`/`[Sticker]` are rewritten to
# `博士（多个选择）:` / `旁白:` by clean_script; those "names" are harmless
# (no char alias matches them) so we don't bother filtering them out.
_SPEAKER_RE = re.compile(r"^([^\s<>:：][^<>:：]*?)\s*[:：]")


def extract_speaker_names(stage_text: str) -> dict[str, int]:
    """`{speaker_name: dialogue_line_count}` over a cleaned stage chunk.

    Exact surface names as they appear before the colon — callers match
    a char's aliases against these by *equality*, never substring, so
    `年` matching `今年的事:…` is impossible (that line's speaker name
    would be `今年的事`, not `年`)."""
    counts: dict[str, int] = {}
    for line in stage_text.splitlines():
        # Cheap guard: a speaker line *must* contain a colon. Skipping
        # the regex on colon-less narration lines (the vast majority)
        # avoids non-greedy backtracking across long prose lines.
        if ":" not in line and "：" not in line:
            continue
        m = _SPEAKER_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


# --- narration matching -----------------------------------------------

AliasMode = Literal["ascii", "cjk_single", "cjk_multi"]
_ASCII_BOUNDARY = "(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"


def alias_mode(text: str) -> AliasMode:
    """How a given alias surface should be matched in narration.

    - `ascii`      — all-ASCII (`W`, `Pith`, `THRM-EX`, `Mr. Nothing`):
      match with a real word boundary so `W` ⊄ `World` but `W` ⊂ `W走`.
    - `cjk_single` — exactly one non-ASCII char (`年`, `陈`, `夕`):
      substring-count, but the *noisy* path — a lone hit never clears
      `mentioned` on its own (Python `\\b` is useless here: CJK chars
      are `\\w`, so `\\b年\\b` rejects both `今年` *and* `年走进门`).
    - `cjk_multi`  — ≥2 chars with at least one non-ASCII (`阿米娅`,
      `玛嘉烈`): plain substring-count is safe.
    """
    if all(ord(c) < 128 for c in text):
        return "ascii"
    if len(text) == 1:
        return "cjk_single"
    return "cjk_multi"


def _compile_alias(text: str) -> re.Pattern[str] | None:
    """An ASCII alias gets a word-boundary regex; CJK aliases count by
    plain `str.count` (no regex)."""
    if alias_mode(text) == "ascii":
        return re.compile(_ASCII_BOUNDARY.format(re.escape(text)))
    return None


def _count_in_body(body: str, text: str, rx: re.Pattern[str] | None) -> int:
    if rx is None:
        return body.count(text)
    # Cheap substring pre-filter before the (much slower) boundary
    # regex: most chars' ASCII appellation never appears in most stages.
    if text not in body:
        return 0
    return len(rx.findall(body))


# An alias ready for matching: surface, its `MatchClass`, its `AliasMode`,
# and a precompiled boundary regex when ASCII (else `None`).
_AliasSpec = tuple[str, MatchClass, AliasMode, "re.Pattern[str] | None"]


def _prepare_aliases(
    per_char: dict[str, list[tuple[str, MatchClass]]],
) -> dict[str, list[_AliasSpec]]:
    out: dict[str, list[_AliasSpec]] = {}
    for cid, aliases in per_char.items():
        out[cid] = [(a, mc, alias_mode(a), _compile_alias(a)) for a, mc in aliases]
    return out


# --- the participant builder ------------------------------------------


def _alias_is_strong(mc: MatchClass, mode: AliasMode, n: int, in_summary: bool) -> bool:
    """Does this alias hit, on its own, justify `named` (vs `mentioned`)?
    A multi-char canonical zh name, or a boundary-matched ASCII canonical
    name, counts at a single hit; a single-zh-char canonical needs ≥2
    hits or an event-summary hit; curated/fuzzy aliases never do (they
    only contribute to the ≥2-total fallback)."""
    if mode == "cjk_multi":
        return mc == "canonical"
    if mode == "ascii":
        return mc in ("canonical", "canonical_short")
    return n >= 2 or in_summary  # cjk_single


def _classify_stage(
    body: str,
    speaker_names: dict[str, int],
    aliases: list[_AliasSpec],
    cid: str,
    summary_char_ids: frozenset[str],
) -> dict | None:
    """One participant row for one (char, stage), or `None` if the char
    doesn't appear in this stage at all."""
    spoke_lines = 0
    mention_count = 0
    strong = False
    matched: set[str] = set()
    for alias, mc, mode, rx in aliases:
        spoke = speaker_names.get(alias, 0)
        n = _count_in_body(body, alias, rx)
        if not spoke and not n:
            continue
        matched.add(alias)
        spoke_lines += spoke
        mention_count += n
        if n and _alias_is_strong(mc, mode, n, cid in summary_char_ids):
            strong = True
    if not matched:
        return None
    if spoke_lines:
        tier: Tier = "speaker"
    elif strong or mention_count >= 2:
        tier = "named"
    else:
        tier = "mentioned"
    return {
        "tier": tier,
        "spoke_lines": spoke_lines,
        "mention_count": mention_count,
        "matched_aliases": sorted(matched),
    }


def build_stage_participants(
    kb_root: Path,
    char_manifests: dict[str, dict],
    deterministic: dict[str, list[dict]],
    *,
    curated: dict[str, list[str]] | None = None,
    ambiguous_canonicals: set[str] | None = None,
    event_manifests: dict[str, dict] | None = None,
    summary_char_ids_by_event: dict[str, frozenset[str]] | None = None,
) -> dict[str, list[dict]]:
    """`char_id -> [{event_id, stage_idx, source:"participant", tier,
    spoke_lines, mention_count, matched_aliases}, ...]`, sorted by
    `(event_id, stage_idx)`.

    Subtraction is per `(char_id, event_id, stage_idx)`: a stage that
    already has a deterministic storyset edge for the char yields no
    participant edge for that stage (but *other* stages of the same
    event still do — unlike the old event-level subtraction)."""
    if ambiguous_canonicals is None:
        ambiguous_canonicals = compute_ambiguous_canonicals(char_manifests)
    if event_manifests is None:
        event_manifests = load_event_manifests(kb_root)
    summary_char_ids_by_event = summary_char_ids_by_event or {}

    det_stage_pairs: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for cid, links in deterministic.items():
        for link in links:
            det_stage_pairs[link["event_id"]].add((cid, link["stage_idx"]))

    per_char = _prepare_aliases(
        build_alias_inputs(char_manifests, curated, ambiguous_canonicals)
    )

    agg: dict[str, dict[str, dict[int, dict]]] = defaultdict(dict)
    for eid, ev in event_manifests.items():
        event_dir = paths.event_dir(kb_root, eid)
        det_here = det_stage_pairs.get(eid, frozenset())
        summary_here = summary_char_ids_by_event.get(eid, frozenset())
        for stage in ev["stages"]:
            sidx = stage["idx"]
            try:
                body = (event_dir / stage["file"]).read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            speaker_names = extract_speaker_names(body)
            for cid, aliases in per_char.items():
                if (cid, sidx) in det_here:
                    continue
                row = _classify_stage(body, speaker_names, aliases, cid, summary_here)
                if row is not None:
                    agg[cid].setdefault(eid, {})[sidx] = row

    out: dict[str, list[dict]] = {}
    for cid in sorted(agg):
        flat: list[dict] = []
        for eid in sorted(agg[cid]):
            for sidx in sorted(agg[cid][eid]):
                flat.append(
                    {
                        "event_id": eid,
                        "stage_idx": sidx,
                        "source": "participant",
                        **agg[cid][eid][sidx],
                    }
                )
        out[cid] = flat
    return out


# --- summary-derived edges (event-scoped) -----------------------------

# Names in <关键人物> sometimes carry decorative quotes ("上尉" / 「上尉」)
# or trailing punctuation; strip a small set so they resolve.
_KEYCHAR_STRIP = "“”\"'‘’「」『』 \t"


def parse_key_chars(md_text: str) -> list[str]:
    """Surface names from a baked event summary's `<关键人物>` tag,
    semicolon-separated, de-quoted, de-duped (order preserved)."""
    blocks = extract_tagged_contents(md_text, "关键人物")
    if not blocks:
        return []
    raw = blocks[0]
    out: list[str] = []
    seen: set[str] = set()
    for piece in re.split(r"[;；]", raw):
        name = piece.strip().strip(_KEYCHAR_STRIP).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _summary_row(event_id: str, stage_idx: int | None, names: list[str]) -> dict:
    return {
        "event_id": event_id,
        "stage_idx": stage_idx,
        "source": "summary",
        "tier": "named",
        "matched_aliases": names,
    }


def build_char_to_events_summary(
    summaries_root: Path | None,
    alias_to_char_ids: dict[str, list[str]],
    deterministic: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    """Returns `(edges, unresolved)`.

    `edges`: `char_id -> [{event_id, stage_idx, source:"summary",
    tier:"named", matched_aliases:[surface names]}, ...]`, sorted by
    `(event_id, stage_idx)` with the event-scoped (`stage_idx is None`)
    row first. Built from two layers:

    - `kb_summaries/stages/<event_id>/<NN>.md` → stage-scoped rows
      (`stage_idx` = the chapter index). Suppressed when a deterministic
      storyset edge already links `(char_id, event_id, stage_idx)`.
    - `kb_summaries/events/<event_id>.md` → an event-scoped row
      (`stage_idx is None`), but only for `(char_id, event_id)` pairs
      *not* already covered by a stage-scoped row (the stage breakdown
      subsumes the event-level edge) and not covered by a deterministic
      `(char_id, event_id)` link.

    `unresolved`: `event_id -> [surface names that no alias matched]`
    (sorted, deduped, merged across the event summary and its stage
    summaries) — surfaced in the build report so a curator can decide
    whether they deserve a `char_alias.txt` line (or, later, an
    `entities.jsonl` entry). Ambiguous surface names (alias → >1 char_id)
    are dropped and *not* reported as unresolved (the `Ambiguous` case)."""
    edges: dict[str, list[dict]] = {}
    unresolved_acc: dict[str, set[str]] = defaultdict(set)
    if summaries_root is None:
        return edges, dict(unresolved_acc)

    det_event_pairs: set[tuple[str, str]] = set()
    det_stage_triples: set[tuple[str, str, int]] = set()
    for cid, links in deterministic.items():
        for link in links:
            det_event_pairs.add((cid, link["event_id"]))
            det_stage_triples.add((cid, link["event_id"], link["stage_idx"]))

    def _resolve(name: str, eid: str) -> str | None:
        """Single matching char_id, or None (ambiguous → silent; missing →
        recorded against `eid` in `unresolved_acc`)."""
        ids = alias_to_char_ids.get(name, [])
        if len(ids) == 1:
            return ids[0]
        if not ids:
            unresolved_acc[eid].add(name)
        return None

    # --- stage-scoped, from kb_summaries/stages/<eid>/<NN>.md ---
    # cid -> eid -> sidx -> [surface names]
    stage_acc: dict[str, dict[str, dict[int, list[str]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    # (cid, eid) pairs that have ≥1 stage-summary hit → drop the event-scoped row
    cids_with_stage_hit: set[tuple[str, str]] = set()
    stages_root = paths.stages_summary_root(summaries_root)
    if stages_root.is_dir():
        for ev_dir in sorted(stages_root.iterdir()):
            if not ev_dir.is_dir():
                continue
            eid = ev_dir.name
            for md in sorted(ev_dir.glob("*.md")):
                try:
                    sidx = int(md.stem)
                except ValueError:
                    continue
                for name in parse_key_chars(md.read_text(encoding="utf-8")):
                    cid = _resolve(name, eid)
                    if cid is None or (cid, eid, sidx) in det_stage_triples:
                        continue
                    stage_acc[cid][eid].setdefault(sidx, []).append(name)
                    cids_with_stage_hit.add((cid, eid))

    # --- event-scoped, from kb_summaries/events/<eid>.md ---
    # cid -> eid -> [surface names]
    event_acc: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    events_dir = summaries_root / "events"
    if events_dir.is_dir():
        for md in sorted(events_dir.glob("*.md")):
            eid = md.stem
            for name in parse_key_chars(md.read_text(encoding="utf-8")):
                cid = _resolve(name, eid)
                if (
                    cid is None
                    or (cid, eid) in det_event_pairs
                    or (cid, eid) in cids_with_stage_hit
                ):
                    continue
                event_acc[cid][eid].append(name)

    for cid in sorted(set(stage_acc) | set(event_acc)):
        rows: list[dict] = []
        for eid in sorted(set(stage_acc.get(cid, {})) | set(event_acc.get(cid, {}))):
            if eid in event_acc.get(cid, {}):
                rows.append(_summary_row(eid, None, event_acc[cid][eid]))
            for sidx in sorted(stage_acc.get(cid, {}).get(eid, {})):
                rows.append(_summary_row(eid, sidx, stage_acc[cid][eid][sidx]))
        edges[cid] = rows

    unresolved = {eid: sorted(names) for eid, names in sorted(unresolved_acc.items())}
    return edges, unresolved


def summary_char_ids_by_event(
    char_to_events_summary: dict[str, list[dict]],
) -> dict[str, frozenset[str]]:
    """Invert the summary edges into `event_id -> {char_id, ...}` so the
    participant builder can use a summary hit to promote a single-zh-char
    narration mention above `mentioned`."""
    by_event: dict[str, set[str]] = defaultdict(set)
    for cid, rows in char_to_events_summary.items():
        for r in rows:
            by_event[r["event_id"]].add(cid)
    return {eid: frozenset(s) for eid, s in by_event.items()}
