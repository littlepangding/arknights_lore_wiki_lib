"""Mock-based unit tests for libs/kb/summarize.py.

No real LLM calls — every test uses FakeClient or wraps subprocess.run
through libs.llm_clients (which is itself covered by test_llm_clients).

Coverage:
- multi-pass routing thresholds
- hash stability + content-sensitivity
- single-pass + multi-pass call shapes
- skip-on-unchanged hash
- write-on-changed-or-missing
- one-shot retry-with-reminder when first response misses a tag
- error path when tags still missing after retry
- summarize_all end-to-end against a synthetic KB
- prune_stale_summaries
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from libs.kb import paths, summarize
from libs.kb.summarize import (
    EVENT_REQUIRED_TAGS,
    MULTI_PASS_LENGTH_THRESHOLD,
    MULTI_PASS_STAGE_THRESHOLD,
    STAGE_REDUCE_REQUIRED_TAGS,
    STAGE_SUMMARY_REQUIRED_TAGS,
    SummaryResult,
    estimate_remaining_stages,
    hash_stage_texts,
    prune_stale_stage_summaries,
    prune_stale_summaries,
    should_multi_pass,
    summarize_all,
    summarize_all_stages,
    summarize_event,
)


# -------- FakeClient ---------------------------------------------------------


@dataclass
class FakeClient:
    """Returns canned responses in order. Records every call's
    (system, prompt, model) tuple."""

    responses: list  # each entry: str (text) or BaseException to raise
    calls: list = field(default_factory=list)
    default_model: str = "fake-model"

    def query(self, system, prompt, *, model=None):
        self.calls.append((system, prompt, model))
        if not self.responses:
            raise AssertionError("FakeClient ran out of responses")
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _full_event_body() -> str:
    return (
        "<一句话概要>\n试运行摘要\n</一句话概要>\n"
        "<核心剧情>\n这是一段示例核心剧情。\n</核心剧情>\n"
        "<关键人物>\n甲;乙\n</关键人物>\n"
        "<场景标签>\n沙漠;追逐\n</场景标签>\n"
    )


def _stage_reduce_body(label: str = "甲场") -> str:
    return (
        f"<章节概要>\n{label}章节摘要\n</章节概要>\n"
        f"<本章人物>\n{label}人物\n</本章人物>\n"
    )


# -------- thresholds + hashing ----------------------------------------------


def test_should_multi_pass_length_threshold():
    assert not should_multi_pass(MULTI_PASS_LENGTH_THRESHOLD, 1)
    assert should_multi_pass(MULTI_PASS_LENGTH_THRESHOLD + 1, 1)


def test_should_multi_pass_stage_threshold():
    assert not should_multi_pass(0, MULTI_PASS_STAGE_THRESHOLD)
    assert should_multi_pass(0, MULTI_PASS_STAGE_THRESHOLD + 1)


def test_should_multi_pass_either_triggers():
    assert should_multi_pass(MULTI_PASS_LENGTH_THRESHOLD + 1, 1)
    assert should_multi_pass(0, MULTI_PASS_STAGE_THRESHOLD + 1)
    assert not should_multi_pass(0, 0)


def test_hash_stage_texts_stable():
    h1 = hash_stage_texts([("a.txt", "alpha"), ("b.txt", "beta")])
    h2 = hash_stage_texts([("b.txt", "beta"), ("a.txt", "alpha")])
    assert h1 == h2


def test_hash_stage_texts_changes_on_content():
    h1 = hash_stage_texts([("a.txt", "alpha")])
    h2 = hash_stage_texts([("a.txt", "alpha2")])
    assert h1 != h2


def test_hash_stage_texts_changes_on_filename():
    assert hash_stage_texts([("a.txt", "same")]) != hash_stage_texts([("b.txt", "same")])


# -------- single-pass --------------------------------------------------------


def test_summarize_event_single_pass_writes_file(tmp_path, make_event):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    meta = make_event(
        kb_root, "evt_small", [("s00", "原文一"), ("s01", "原文二")]
    )
    client = FakeClient(responses=[_full_event_body()])

    result = summarize_event(
        meta, paths.event_dir(kb_root, "evt_small"), summaries_root, client
    )

    assert result.status == "wrote"
    assert result.passes == "single"
    assert result.summary_path is not None and result.summary_path.exists()
    assert len(client.calls) == 1
    md = result.summary_path.read_text(encoding="utf-8")
    assert "event_id: evt_small" in md
    for tag in EVENT_REQUIRED_TAGS:
        assert f"<{tag}>" in md and f"</{tag}>" in md


def test_summarize_event_single_pass_passes_full_text(tmp_path, make_event):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    make_event(
        kb_root, "evt_small", [("s00", "AAA"), ("s01", "BBB")]
    )
    meta = json.loads((paths.event_json_path(kb_root, "evt_small")).read_text("utf-8"))
    client = FakeClient(responses=[_full_event_body()])

    summarize_event(
        meta, paths.event_dir(kb_root, "evt_small"), summaries_root, client
    )
    sys_prompt, user_prompt, _ = client.calls[0]
    # Single-pass prompt embeds every stage body.
    assert "AAA" in user_prompt and "BBB" in user_prompt
    assert "<一句话概要>" in user_prompt  # template tags present


# -------- multi-pass ---------------------------------------------------------


def _multi_pass_event(make_event, kb_root: Path, eid: str, n_stages: int) -> dict:
    bodies = [(f"s{i:02d}", f"章节{i}原文") for i in range(n_stages)]
    return make_event(kb_root, eid, bodies)


def test_summarize_event_multi_pass_via_stage_count(tmp_path, make_event):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    n = MULTI_PASS_STAGE_THRESHOLD + 1  # 11 stages
    meta = _multi_pass_event(make_event, kb_root, "evt_big", n)

    # n stage-reduce calls + 1 merge call
    canned = [_stage_reduce_body(f"s{i}") for i in range(n)] + [_full_event_body()]
    client = FakeClient(responses=list(canned))

    result = summarize_event(
        meta, paths.event_dir(kb_root, "evt_big"), summaries_root, client
    )

    assert result.status == "wrote"
    assert result.passes == "multi"
    assert len(client.calls) == n + 1


def test_summarize_event_multi_pass_via_length(tmp_path, make_event):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    big_body = "x" * (MULTI_PASS_LENGTH_THRESHOLD + 100)
    meta = make_event(kb_root, "evt_long", [("s00", big_body)])

    client = FakeClient(responses=[_stage_reduce_body(), _full_event_body()])
    result = summarize_event(
        meta, paths.event_dir(kb_root, "evt_long"), summaries_root, client
    )
    assert result.status == "wrote"
    assert result.passes == "multi"
    assert len(client.calls) == 2  # 1 reduce + 1 merge


def test_summarize_event_multi_pass_merge_input_carries_stage_blocks(
    tmp_path, make_event
):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    n = MULTI_PASS_STAGE_THRESHOLD + 1
    meta = _multi_pass_event(make_event, kb_root, "evt_merge", n)

    stage_bodies = [_stage_reduce_body(f"s{i}") for i in range(n)]
    client = FakeClient(responses=stage_bodies + [_full_event_body()])
    summarize_event(
        meta, paths.event_dir(kb_root, "evt_merge"), summaries_root, client
    )

    # Last call is the merge — its prompt must include each per-stage 章节概要
    merge_prompt = client.calls[-1][1]
    for i in range(n):
        assert f"s{i}章节摘要" in merge_prompt
    assert "整合各章节" in merge_prompt or "整合" in merge_prompt


# -------- skip on unchanged ---------------------------------------------------


def test_summarize_event_skips_unchanged(tmp_path, make_event):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    meta = make_event(kb_root, "evt", [("s00", "body")])
    client = FakeClient(responses=[_full_event_body()])

    first = summarize_event(
        meta, paths.event_dir(kb_root, "evt"), summaries_root, client
    )
    assert first.status == "wrote"

    # Re-run with prior manifest entry pointing at the same hash.
    prior = {"source_hash": first.source_hash, "passes": "single"}
    client2 = FakeClient(responses=[])  # would error if called
    second = summarize_event(
        meta,
        paths.event_dir(kb_root, "evt"),
        summaries_root,
        client2,
        prior_manifest_entry=prior,
    )
    assert second.status == "skipped_unchanged"
    assert second.source_hash == first.source_hash
    assert client2.calls == []


def test_summarize_event_force_overrides_skip(tmp_path, make_event):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    meta = make_event(kb_root, "evt", [("s00", "body")])

    client = FakeClient(responses=[_full_event_body()])
    first = summarize_event(meta, paths.event_dir(kb_root, "evt"), summaries_root, client)
    prior = {"source_hash": first.source_hash}

    client2 = FakeClient(responses=[_full_event_body()])
    again = summarize_event(
        meta,
        paths.event_dir(kb_root, "evt"),
        summaries_root,
        client2,
        prior_manifest_entry=prior,
        force=True,
    )
    assert again.status == "wrote"
    assert len(client2.calls) == 1


def test_summarize_event_skips_only_when_summary_file_present(
    tmp_path, make_event
):
    """If manifest hash matches but the .md was deleted, we must rewrite."""
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    meta = make_event(kb_root, "evt", [("s00", "body")])
    client = FakeClient(responses=[_full_event_body()])
    first = summarize_event(meta, paths.event_dir(kb_root, "evt"), summaries_root, client)
    first.summary_path.unlink()  # drop the file but keep the hash

    prior = {"source_hash": first.source_hash}
    client2 = FakeClient(responses=[_full_event_body()])
    second = summarize_event(
        meta,
        paths.event_dir(kb_root, "evt"),
        summaries_root,
        client2,
        prior_manifest_entry=prior,
    )
    assert second.status == "wrote"


# -------- retry-once-with-reminder -------------------------------------------


def test_summarize_event_retries_once_with_reminder(tmp_path, make_event):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    meta = make_event(kb_root, "evt", [("s00", "body")])
    # First response missing 场景标签; second is complete.
    incomplete = (
        "<一句话概要>\nhi\n</一句话概要>\n"
        "<核心剧情>\nbody\n</核心剧情>\n"
        "<关键人物>\n甲\n</关键人物>\n"
    )
    client = FakeClient(responses=[incomplete, _full_event_body()])

    result = summarize_event(meta, paths.event_dir(kb_root, "evt"), summaries_root, client)
    assert result.status == "wrote"
    assert len(client.calls) == 2
    second_prompt = client.calls[1][1]
    assert "缺少必须的标签" in second_prompt
    assert "场景标签" in second_prompt


def test_summarize_event_returns_error_when_tags_still_missing(
    tmp_path, make_event
):
    kb_root = tmp_path / "kb"
    summaries_root = tmp_path / "kb_summaries"
    meta = make_event(kb_root, "evt", [("s00", "body")])
    incomplete = "<一句话概要>\nx\n</一句话概要>\n"
    client = FakeClient(responses=[incomplete, incomplete])
    result = summarize_event(meta, paths.event_dir(kb_root, "evt"), summaries_root, client)
    assert result.status == "error"
    assert "missing required tags" in (result.error or "")
    assert not paths.event_summary_path(summaries_root, "evt").exists()


# -------- summarize_all integration ------------------------------------------


def _stub_full_response(_call_idx: int) -> str:
    return _full_event_body()


def test_summarize_all_writes_one_per_event(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"

    # mini_gamedata has 3 events and they're all under-threshold = single-pass.
    canned = [_full_event_body()] * 10
    client = FakeClient(responses=canned)

    report = summarize_all(kb_root, summaries_root, client, backend_label="cli")

    assert len(report.wrote) == 3
    assert report.errors == []
    assert report.skipped == []

    manifest = json.loads(
        paths.summaries_manifest_path(summaries_root).read_text("utf-8")
    )
    assert set(manifest["events"].keys()) == set(report.wrote)
    for entry in manifest["events"].values():
        assert entry["passes"] == "single"
        assert "source_hash" in entry and len(entry["source_hash"]) > 16
        assert entry["backend"] == "cli"


def test_summarize_all_second_run_is_noop(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    client = FakeClient(responses=[_full_event_body()] * 10)
    summarize_all(kb_root, summaries_root, client, backend_label="cli")

    # Second pass: client raises if called.
    client2 = FakeClient(responses=[])
    report = summarize_all(kb_root, summaries_root, client2, backend_label="cli")
    assert report.wrote == []
    assert len(report.skipped) == 3
    assert client2.calls == []


def test_summarize_all_only_event_filter(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    client = FakeClient(responses=[_full_event_body()])

    # mini_gamedata events: act_test_01, mini_test_02, main_0 (or similar).
    # Pick whatever exists by reading one event_id off disk.
    event_ids = sorted(p.name for p in (kb_root / "events").iterdir() if p.is_dir())
    target = event_ids[0]

    report = summarize_all(kb_root, summaries_root, client, only=[target])
    assert report.wrote == [target]
    assert paths.event_summary_path(summaries_root, target).exists()
    other = (summaries_root / "events" / f"{event_ids[1]}.md")
    assert not other.exists()


def test_summarize_all_prune_drops_stale(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    client = FakeClient(responses=[_full_event_body()] * 10)
    summarize_all(kb_root, summaries_root, client, backend_label="cli")

    orphan = paths.event_summary_path(summaries_root, "ghost_event")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("---\n---\nstale\n", encoding="utf-8")

    client2 = FakeClient(responses=[])
    report = summarize_all(kb_root, summaries_root, client2, backend_label="cli")
    assert "ghost_event" in report.pruned
    assert not orphan.exists()


def test_summarize_all_no_prune_keeps_stale(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    client = FakeClient(responses=[_full_event_body()] * 10)
    summarize_all(kb_root, summaries_root, client, backend_label="cli")

    orphan = paths.event_summary_path(summaries_root, "ghost_event")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("---\n---\nstale\n", encoding="utf-8")

    client2 = FakeClient(responses=[])
    report = summarize_all(
        kb_root, summaries_root, client2, backend_label="cli", prune=False
    )
    assert report.pruned == []
    assert orphan.exists()


def test_summarize_all_continues_after_per_event_error(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    # First event: missing-tag forever (one normal try + one retry-with-reminder)
    incomplete = "<一句话概要>\nx\n</一句话概要>\n"
    # Then succeed for the next two events (1 call each).
    client = FakeClient(
        responses=[incomplete, incomplete, _full_event_body(), _full_event_body()]
    )
    report = summarize_all(kb_root, summaries_root, client, backend_label="cli")
    assert len(report.errors) == 1
    assert len(report.wrote) == 2


def test_summarize_all_continues_when_stage_file_missing(tmp_path, build_real_kb):
    """Regression for P2: a missing stage file (event.json references a
    file that was deleted) used to abort the batch with FileNotFoundError.
    Should now record an error and keep going."""
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    event_ids = sorted(p.name for p in (kb_root / "events").iterdir() if p.is_dir())
    victim = event_ids[0]
    victim_meta = json.loads(paths.event_json_path(kb_root, victim).read_text("utf-8"))
    (paths.event_dir(kb_root, victim) / victim_meta["stages"][0]["file"]).unlink()

    client = FakeClient(responses=[_full_event_body()] * 10)
    report = summarize_all(kb_root, summaries_root, client, backend_label="cli")
    assert len(report.errors) == 1
    assert report.errors[0][0] == victim
    assert "FileNotFoundError" in report.errors[0][1]
    assert len(report.wrote) == len(event_ids) - 1


def test_summarize_event_returns_error_on_malformed_event_meta(tmp_path):
    """event_meta with a missing required key should surface as an error,
    not propagate KeyError up to the caller."""
    summaries_root = tmp_path / "kb_summaries"
    bogus = {"event_id": "bad"}
    client = FakeClient(responses=[])
    result = summarize_event(bogus, tmp_path / "nonexistent", summaries_root, client)
    assert result.status == "error"
    assert "KeyError" in (result.error or "")


def test_summarize_all_raises_when_kb_is_empty(tmp_path):
    summaries_root = tmp_path / "kb_summaries"
    kb_root = tmp_path / "kb"
    (kb_root / "events").mkdir(parents=True)
    client = FakeClient(responses=[])
    with pytest.raises(FileNotFoundError):
        summarize_all(kb_root, summaries_root, client)


def test_summarize_all_no_manifest_write_when_nothing_changed(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    summarize_all(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * 10),
        backend_label="cli",
    )
    manifest_path = paths.summaries_manifest_path(summaries_root)
    mtime_before = manifest_path.stat().st_mtime_ns

    summarize_all(
        kb_root, summaries_root, FakeClient(responses=[]), backend_label="cli"
    )
    assert manifest_path.stat().st_mtime_ns == mtime_before


def test_summarize_all_only_filter_lazy_loads(tmp_path, build_real_kb, monkeypatch):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    event_ids = sorted(p.name for p in (kb_root / "events").iterdir() if p.is_dir())
    target = event_ids[0]

    from libs.kb import _io
    calls: list = []
    real_load = _io.load_dir_manifests
    def spy(*a, **kw):
        calls.append(a)
        return real_load(*a, **kw)
    monkeypatch.setattr(_io, "load_dir_manifests", spy)

    summarize_all(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()]),
        only=[target],
    )
    assert calls == []  # full-corpus loader bypassed when filter is set


def test_summarize_all_only_filter_skips_prune(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    summarize_all(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * 10),
        backend_label="cli",
    )
    orphan = paths.event_summary_path(summaries_root, "ghost_event")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("---\n---\n", encoding="utf-8")

    target = sorted(p.name for p in (kb_root / "events").iterdir() if p.is_dir())[0]
    report = summarize_all(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()]),
        only=[target], force=True,
    )
    assert report.pruned == []
    assert orphan.exists()


def test_summarize_all_raises_when_filter_matches_no_event(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    with pytest.raises(FileNotFoundError, match="none of the requested events"):
        summarize_all(
            kb_root, summaries_root, FakeClient(responses=[]), only=["does_not_exist"]
        )


# -------- prune_stale_summaries direct ---------------------------------------


def test_prune_stale_summaries_removes_only_orphans(tmp_path):
    summaries_root = tmp_path / "kb_summaries"
    events = summaries_root / "events"
    events.mkdir(parents=True)
    (events / "keep.md").write_text("k", encoding="utf-8")
    (events / "drop.md").write_text("d", encoding="utf-8")

    removed = prune_stale_summaries(summaries_root, current_event_ids={"keep"})
    assert removed == ["drop"]
    assert (events / "keep.md").exists()
    assert not (events / "drop.md").exists()


def test_prune_stale_summaries_handles_missing_dir(tmp_path):
    assert prune_stale_summaries(tmp_path / "missing", set()) == []


# -------- per-stage summaries (P-C) -----------------------------------------


def _stage_keys_on_disk(kb_root: Path) -> list[str]:
    keys: list[str] = []
    for ev_dir in sorted((kb_root / "events").iterdir()):
        if not ev_dir.is_dir():
            continue
        meta = json.loads((ev_dir / "event.json").read_text("utf-8"))
        for s in meta["stages"]:
            keys.append(f"{ev_dir.name}/{s['idx']:02d}")
    return keys


def test_stage_summary_required_tags_match_event_shape():
    assert STAGE_SUMMARY_REQUIRED_TAGS == EVENT_REQUIRED_TAGS


def test_summarize_all_stages_writes_one_per_stage(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    client = FakeClient(responses=[_full_event_body()] * (len(keys) + 5))

    report = summarize_all_stages(kb_root, summaries_root, client, backend_label="cli")

    assert sorted(report.wrote) == sorted(keys)
    assert report.errors == []
    assert len(client.calls) == len(keys)  # always single-pass: one call per stage

    manifest = json.loads(paths.summaries_manifest_path(summaries_root).read_text("utf-8"))
    assert set(manifest["stages"].keys()) == set(keys)
    for key, entry in manifest["stages"].items():
        eid, nn = key.split("/")
        assert entry["event_id"] == eid
        assert entry["stage_idx"] == int(nn)
        assert entry["backend"] == "cli"
        assert len(entry["source_hash"]) > 16
        md_path = summaries_root / "stages" / eid / f"{nn}.md"
        assert md_path.exists()
        md = md_path.read_text("utf-8")
        assert f"event_id: {eid}" in md and f"stage_idx: {int(nn)}" in md
        for tag in STAGE_SUMMARY_REQUIRED_TAGS:
            assert f"<{tag}>" in md and f"</{tag}>" in md


def test_summarize_all_stages_embeds_chapter_text(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    eid, nn = keys[0].split("/")
    stage_text = (
        paths.event_dir(kb_root, eid)
        / json.loads((paths.event_json_path(kb_root, eid)).read_text("utf-8"))["stages"][int(nn)]["file"]
    ).read_text("utf-8")

    client = FakeClient(responses=[_full_event_body()] * len(keys))
    summarize_all_stages(kb_root, summaries_root, client, only=[eid])
    # first call's user prompt must embed that chapter's raw text + the template tags
    user_prompt = client.calls[0][1]
    assert stage_text[:50] in user_prompt
    assert "<一句话概要>" in user_prompt


def test_summarize_all_stages_second_run_is_noop(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    client2 = FakeClient(responses=[])  # raises if called
    report = summarize_all_stages(kb_root, summaries_root, client2, backend_label="cli")
    assert report.wrote == []
    assert len(report.skipped) == len(keys)
    assert client2.calls == []


def test_summarize_all_stages_event_filter(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    target = keys[0].split("/")[0]
    target_keys = [k for k in keys if k.startswith(f"{target}/")]
    other = next(k for k in keys if not k.startswith(f"{target}/"))

    report = summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        only=[target],
    )
    assert sorted(report.wrote) == sorted(target_keys)
    oeid, onn = other.split("/")
    assert not (summaries_root / "stages" / oeid / f"{onn}.md").exists()


def test_summarize_all_stages_force_resummarizes(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    client2 = FakeClient(responses=[_full_event_body()] * len(keys))
    report = summarize_all_stages(kb_root, summaries_root, client2, force=True, backend_label="cli")
    assert sorted(report.wrote) == sorted(keys)
    assert len(client2.calls) == len(keys)


def test_summarize_all_stages_prune_drops_stale(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    orphan = paths.stage_summary_path(summaries_root, "ghost_event", 7)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("---\n---\nstale\n", encoding="utf-8")

    report = summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[]), backend_label="cli"
    )
    assert "ghost_event/07" in report.pruned
    assert not orphan.exists()
    assert not orphan.parent.exists()  # now-empty event dir removed too


def test_summarize_all_stages_no_prune_keeps_stale(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    orphan = paths.stage_summary_path(summaries_root, "ghost_event", 7)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("---\n---\n", encoding="utf-8")

    report = summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[]), backend_label="cli", prune=False
    )
    assert report.pruned == []
    assert orphan.exists()


def test_summarize_all_stages_only_filter_skips_prune(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    orphan = paths.stage_summary_path(summaries_root, "ghost_event", 7)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("---\n---\n", encoding="utf-8")

    target = keys[0].split("/")[0]
    report = summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        only=[target], force=True,
    )
    assert report.pruned == []
    assert orphan.exists()


def test_summarize_all_stages_continues_when_stage_file_missing(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    victim_eid, victim_nn = keys[0].split("/")
    vmeta = json.loads(paths.event_json_path(kb_root, victim_eid).read_text("utf-8"))
    (paths.event_dir(kb_root, victim_eid) / vmeta["stages"][int(victim_nn)]["file"]).unlink()

    report = summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    assert any(eid == keys[0] and "FileNotFoundError" in msg for eid, msg in report.errors)
    assert len(report.wrote) == len(keys) - 1


def test_summarize_all_stages_raises_when_kb_empty(tmp_path):
    kb_root = tmp_path / "kb"
    (kb_root / "events").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        summarize_all_stages(kb_root, tmp_path / "kb_summaries", FakeClient(responses=[]))


def test_estimate_remaining_stages_counts_all_then_none(tmp_path, build_real_kb):
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)

    est = estimate_remaining_stages(kb_root, summaries_root)
    assert est.n_to_run == len(keys)
    assert est.n_multi == 0  # stage summaries never multi-pass
    assert est.llm_calls == len(keys)
    assert est.in_chars > 0 and est.total_tokens > 0

    summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    est2 = estimate_remaining_stages(kb_root, summaries_root)
    assert est2.n_to_run == 0
    assert len(est2.already_done) == len(keys)


def test_prune_stale_stage_summaries_direct(tmp_path):
    summaries_root = tmp_path / "kb_summaries"
    keep = paths.stage_summary_path(summaries_root, "ev1", 0)
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("k", encoding="utf-8")
    drop = paths.stage_summary_path(summaries_root, "ev2", 3)
    drop.parent.mkdir(parents=True, exist_ok=True)
    drop.write_text("d", encoding="utf-8")

    removed = prune_stale_stage_summaries(summaries_root, current_stage_keys={"ev1/00"})
    assert removed == ["ev2/03"]
    assert keep.exists()
    assert not drop.exists() and not drop.parent.exists()


def test_prune_stale_stage_summaries_handles_missing_dir(tmp_path):
    assert prune_stale_stage_summaries(tmp_path / "missing", set()) == []


def test_summarize_event_and_stages_share_one_manifest(tmp_path, build_real_kb):
    """Event and stage bakes write to disjoint sections of the same
    kb_summaries/manifest.json — neither clobbers the other."""
    kb_root = build_real_kb(tmp_path / "kb")
    summaries_root = tmp_path / "kb_summaries"
    keys = _stage_keys_on_disk(kb_root)
    summarize_all(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * 10),
        backend_label="cli",
    )
    summarize_all_stages(
        kb_root, summaries_root, FakeClient(responses=[_full_event_body()] * len(keys)),
        backend_label="cli",
    )
    manifest = json.loads(paths.summaries_manifest_path(summaries_root).read_text("utf-8"))
    assert len(manifest["events"]) == 3
    assert len(manifest["stages"]) == len(keys)
    # a third no-op event run must not disturb the stages section
    summarize_all(kb_root, summaries_root, FakeClient(responses=[]), backend_label="cli")
    manifest2 = json.loads(paths.summaries_manifest_path(summaries_root).read_text("utf-8"))
    assert manifest2["stages"] == manifest["stages"]
