"""Unit tests for `libs.kb.cooccurrence` — the deterministic char-pair
co-occurrence layer over the merged WS-0 `event_to_chars` index.

Load-bearing properties:

- **Stage cells, not event cells, drive `co_stage_count`**. Two chars
  with edges to different stages of the same event must not be counted
  as a stage co-appearance — only an event one.
- **Event-scoped summary edges (`stage_idx is None`) contribute to
  events, never stages**. They model "appears somewhere in event X" so
  they widen `co_event_count` without inflating `co_stage_count`.
- **`min_tier` gating mirrors `query.char_appearances`**. A
  `mentioned`-tier participant edge must not bring a pair into the
  table at the default `named` floor — otherwise the deterministic
  layer drowns in noise and the relation bake gets the wrong
  candidate list.
- **Deterministic edges always pass `min_tier`** (no tier on them;
  they're ground truth). Two deterministic edges with the same stage
  are a co-stage hit regardless of `--min-tier`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.kb import cooccurrence


# --- helpers ----------------------------------------------------------


def _row(char_id: str, source: str, stage_idx: int | None, tier=None, **extra) -> dict:
    out = {"char_id": char_id, "source": source, "stage_idx": stage_idx}
    if tier is not None:
        out["tier"] = tier
    out.update(extra)
    return out


# --- build_cooccurrence ------------------------------------------------


def test_build_cooccurrence_basic_pair():
    e2c = {
        "evt1": [
            _row("char_a", "deterministic", 0),
            _row("char_b", "deterministic", 0),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c)
    assert rows == [
        {
            "a": "char_a",
            "b": "char_b",
            "co_stage_count": 1,
            "co_event_count": 1,
            "sample_events": ["evt1"],
        }
    ]


def test_build_cooccurrence_pair_order_is_lexicographic():
    """`a` must come before `b` lex — so the pair (b, a) and (a, b) map
    to the same row regardless of input order."""
    e2c = {
        "evt1": [
            _row("zeta", "deterministic", 0),
            _row("alpha", "deterministic", 0),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c)
    assert rows[0]["a"] == "alpha"
    assert rows[0]["b"] == "zeta"


def test_build_cooccurrence_separate_stages_in_same_event():
    """Two chars in *different* stages of the same event co-occur at the
    event level but not at the stage level."""
    e2c = {
        "evt1": [
            _row("char_a", "deterministic", 0),
            _row("char_b", "deterministic", 1),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c)
    assert len(rows) == 1
    assert rows[0]["co_stage_count"] == 0
    assert rows[0]["co_event_count"] == 1


def test_build_cooccurrence_multiple_stages_accumulate():
    """Sharing two stages in the same event counts as 2 co-stages
    inside 1 co-event."""
    e2c = {
        "evt1": [
            _row("char_a", "deterministic", 0),
            _row("char_a", "deterministic", 1),
            _row("char_b", "deterministic", 0),
            _row("char_b", "deterministic", 1),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c)
    assert rows[0]["co_stage_count"] == 2
    assert rows[0]["co_event_count"] == 1


def test_build_cooccurrence_event_scoped_summary_does_not_inflate_stage():
    """Event-scoped summary edges (`stage_idx=None`) contribute to the
    event count only; the pair has `co_stage_count=0`."""
    e2c = {
        "evt1": [
            _row("char_a", "summary", None, tier="named", matched_aliases=["A"]),
            _row("char_b", "summary", None, tier="named", matched_aliases=["B"]),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c)
    assert rows[0]["co_event_count"] == 1
    assert rows[0]["co_stage_count"] == 0


def test_build_cooccurrence_default_min_tier_drops_mentioned():
    """A pair held together only by `mentioned` participant edges must
    not appear at the default `--min-tier=named` floor."""
    e2c = {
        "evt1": [
            _row("char_a", "participant", 0, tier="mentioned", mention_count=1),
            _row("char_b", "participant", 0, tier="mentioned", mention_count=1),
        ]
    }
    assert cooccurrence.build_cooccurrence(e2c) == []


def test_build_cooccurrence_min_tier_mentioned_admits_pair():
    e2c = {
        "evt1": [
            _row("char_a", "participant", 0, tier="mentioned", mention_count=1),
            _row("char_b", "participant", 0, tier="mentioned", mention_count=1),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c, min_tier="mentioned")
    assert len(rows) == 1
    assert rows[0]["co_stage_count"] == 1


def test_build_cooccurrence_deterministic_always_passes_min_tier():
    """A deterministic edge has no tier; it must pass even
    `--min-tier=speaker`."""
    e2c = {
        "evt1": [
            _row("char_a", "deterministic", 0),
            _row("char_b", "deterministic", 0),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c, min_tier="speaker")
    assert len(rows) == 1


def test_build_cooccurrence_sample_events_caps_to_limit():
    """A pair co-starring across many events emits at most
    `sample_event_limit` event ids — the JSONL row stays bounded."""
    e2c = {}
    for n in range(7):
        eid = f"evt{n:02d}"
        e2c[eid] = [
            _row("char_a", "deterministic", 0),
            _row("char_b", "deterministic", 0),
        ]
    rows = cooccurrence.build_cooccurrence(e2c, sample_event_limit=3)
    assert rows[0]["co_event_count"] == 7
    assert len(rows[0]["sample_events"]) == 3
    # Sorted ascending — first 3 by event id.
    assert rows[0]["sample_events"] == ["evt00", "evt01", "evt02"]


def test_build_cooccurrence_one_char_is_not_a_pair():
    """A stage with a single char produces no pair row."""
    e2c = {"evt1": [_row("solo", "deterministic", 0)]}
    assert cooccurrence.build_cooccurrence(e2c) == []


def test_build_cooccurrence_three_chars_in_a_stage_emit_three_pairs():
    e2c = {
        "evt1": [
            _row("a", "deterministic", 0),
            _row("b", "deterministic", 0),
            _row("c", "deterministic", 0),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c)
    assert {(r["a"], r["b"]) for r in rows} == {("a", "b"), ("a", "c"), ("b", "c")}


def test_build_cooccurrence_rows_sorted_by_pair():
    e2c = {
        "evt1": [
            _row("z", "deterministic", 0),
            _row("a", "deterministic", 0),
            _row("m", "deterministic", 0),
        ]
    }
    rows = cooccurrence.build_cooccurrence(e2c)
    pairs = [(r["a"], r["b"]) for r in rows]
    assert pairs == sorted(pairs)


# --- I/O --------------------------------------------------------------


def test_write_and_load_round_trip(tmp_path):
    rows = [
        {
            "a": "char_002_amiya",
            "b": "char_2025_shu",
            "co_stage_count": 3,
            "co_event_count": 2,
            "sample_events": ["evt1", "evt2"],
        }
    ]
    p = tmp_path / "cooccurrence.jsonl"
    cooccurrence.write_cooccurrence_jsonl(p, rows)
    loaded = cooccurrence.load_cooccurrence(p)
    assert loaded == rows


def test_load_cooccurrence_missing_file_is_empty(tmp_path):
    assert cooccurrence.load_cooccurrence(tmp_path / "absent.jsonl") == []


# --- in-memory query helpers -----------------------------------------


def _pair(a, b, stage=1, event=1, sample=None):
    return {
        "a": a,
        "b": b,
        "co_stage_count": stage,
        "co_event_count": event,
        "sample_events": sample or ["evt0"],
    }


def test_cooccurrence_for_returns_only_matching_pairs():
    rows = [_pair("a", "b"), _pair("a", "c"), _pair("b", "c")]
    assert {r["b"] for r in cooccurrence.cooccurrence_for(rows, "a")} == {"b", "c"}
    assert cooccurrence.cooccurrence_for(rows, "absent") == []


def test_cooccurrence_for_sorts_by_stage_count_desc():
    rows = [
        _pair("a", "weak", stage=1),
        _pair("a", "strong", stage=10),
        _pair("a", "mid", stage=5),
    ]
    out = cooccurrence.cooccurrence_for(rows, "a")
    assert [r["b"] for r in out] == ["strong", "mid", "weak"]


def test_cooccurrence_for_honors_limit():
    rows = [_pair("a", f"b{i}", stage=i) for i in range(10)]
    assert len(cooccurrence.cooccurrence_for(rows, "a", limit=3)) == 3


def test_cooccurrence_for_matches_pair_either_side():
    """`a` may sit on either side of an unordered pair — the helper
    must find it regardless."""
    rows = [_pair("alpha", "char_x"), _pair("char_x", "zeta")]
    out = cooccurrence.cooccurrence_for(rows, "char_x")
    assert {(r["a"], r["b"]) for r in out} == {("alpha", "char_x"), ("char_x", "zeta")}


def test_cooccurrence_top_sorts_and_caps():
    rows = [
        _pair("a", "b", stage=1),
        _pair("c", "d", stage=5),
        _pair("e", "f", stage=3),
    ]
    out = cooccurrence.cooccurrence_top(rows, limit=2)
    assert [r["b"] for r in out] == ["d", "f"]


def test_cooccurrence_between_normalises_argument_order():
    rows = [_pair("a", "z", stage=3)]
    # Caller passes (z, a) — must still find the (a, z) row.
    assert cooccurrence.cooccurrence_between(rows, "z", "a") == rows[0]


def test_cooccurrence_between_returns_none_for_absent_pair():
    rows = [_pair("a", "b")]
    assert cooccurrence.cooccurrence_between(rows, "a", "c") is None
