"""Mock-based unit tests for libs/kb/relations_bake.py.

No real LLM calls — every test that needs an LLM uses FakeClient.

What's load-bearing:

- **Parsing tolerates the LLM's formatting wiggle**: comment lines,
  blank lines, optional notes, semicolons inside notes.
- **Resolution is honest about ambiguity**: an `Ambiguous` tail keeps
  the row with `tail=null` + `ambiguous_candidates`; a `Missing` tail
  is dropped with a warning (curator's punch list, never silent loss).
- **Hash-gated skips don't burn tokens**: if the handbook hash matches
  the manifest entry and the output file exists, no `query` is called.
- **Novel relation types are accepted but warned** so a curator can
  decide whether to extend the vocabulary; the LLM isn't allowed to
  silently coin new edge semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pytest

from libs.kb import paths, relations_bake


# -------- FakeClient (same shape as test_summarize.py) ----------------


@dataclass
class FakeClient:
    """Returns canned responses in order. Records every (system, prompt,
    model) call so a test can assert the bake's prompt contents without
    depending on real LLM output."""

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


def _wrap(body: str) -> str:
    """Wrap a bake body string in `<关系>` tags so it parses."""
    return f"<关系>\n{body}\n</关系>\n"


# -------- parse_relations_block --------------------------------------


def test_parse_relations_block_basic():
    body = _wrap(
        "member_of;罗德岛;阿米娅是领导者之一\n"
        "mentor_of;凯尔希\n"
    )
    rows, warns = relations_bake.parse_relations_block(body)
    assert warns == []
    assert len(rows) == 2
    assert rows[0].type == "member_of"
    assert rows[0].tail_surface == "罗德岛"
    assert rows[0].notes == "阿米娅是领导者之一"
    assert rows[1].notes is None


def test_parse_relations_block_skips_blank_and_comments():
    body = _wrap(
        "# 这是一个注释\n"
        "\n"
        "  \n"
        "ally_of;凯尔希\n"
        "# 另一个注释\n"
    )
    rows, warns = relations_bake.parse_relations_block(body)
    assert warns == []
    assert len(rows) == 1
    assert rows[0].type == "ally_of"


def test_parse_relations_block_empty_body_is_valid():
    """No relations is a valid bake outcome (some chars genuinely have
    none in their handbook)."""
    rows, warns = relations_bake.parse_relations_block(_wrap(""))
    assert rows == [] and warns == []


def test_parse_relations_block_wu_means_no_relations():
    """The LLM may emit `无` (Chinese "none") instead of an empty body
    — same prompt convention as the summary bake."""
    rows, warns = relations_bake.parse_relations_block(_wrap("无"))
    assert rows == [] and warns == []


def test_parse_relations_block_missing_tag_warns():
    rows, warns = relations_bake.parse_relations_block(
        "no关系 tag here at all"
    )
    assert rows == []
    assert len(warns) == 1 and "关系" in warns[0]["reason"]


def test_parse_relations_block_unparseable_line_warns():
    body = _wrap(
        "member_of;罗德岛\n"
        "this line has no semicolon\n"
        "mentor_of;凯尔希\n"
    )
    rows, warns = relations_bake.parse_relations_block(body)
    assert len(rows) == 2
    assert len(warns) == 1
    assert "unparseable" in warns[0]["reason"]


def test_parse_relations_block_empty_field_warns():
    body = _wrap(";罗德岛\n")  # empty type
    rows, warns = relations_bake.parse_relations_block(body)
    assert rows == []
    assert any("unparseable" in w["reason"] or "empty" in w["reason"] for w in warns)


# -------- resolve_tail -----------------------------------------------


def test_resolve_tail_resolved():
    aliases = {"罗德岛": ["ent_abc"]}
    eid, ambig, status = relations_bake.resolve_tail("罗德岛", aliases)
    assert (eid, ambig, status) == ("ent_abc", [], "resolved")


def test_resolve_tail_ambiguous():
    aliases = {"暮落": ["char_a", "char_b"]}
    eid, ambig, status = relations_bake.resolve_tail("暮落", aliases)
    assert eid is None
    assert ambig == ["char_a", "char_b"]
    assert status == "ambiguous"


def test_resolve_tail_missing():
    eid, ambig, status = relations_bake.resolve_tail("陌生人", {})
    assert (eid, ambig, status) == (None, [], "missing")


# -------- assemble_char_rows ----------------------------------------


def _parsed(type_, tail, notes=None, line_no=1):
    return relations_bake._ParsedLine(
        type=type_, tail_surface=tail, notes=notes, line_no=line_no
    )


def test_assemble_rows_resolved_keeps_entity_id_and_surface():
    aliases = {"罗德岛": ["ent_rhodes"]}
    rows, warns = relations_bake.assemble_char_rows(
        "char_002_amiya",
        [_parsed("member_of", "罗德岛", "领导者之一")],
        aliases,
    )
    assert warns == []
    assert rows == [
        {
            "head": "char_002_amiya",
            "type": "member_of",
            "tail": "ent_rhodes",
            "tail_name": "罗德岛",
            "notes": "领导者之一",
        }
    ]


def test_assemble_rows_missing_drops_with_warning():
    """An unresolved tail is the curator's punch list — not silent loss."""
    rows, warns = relations_bake.assemble_char_rows(
        "char_x",
        [_parsed("member_of", "陌生组织")],
        entity_alias_to_ids={},
    )
    assert rows == []
    assert len(warns) == 1
    assert warns[0]["tail_surface"] == "陌生组织"
    assert "unresolved" in warns[0]["reason"]


def test_assemble_rows_ambiguous_keeps_row_with_null_tail():
    aliases = {"暮落": ["char_a", "char_b"]}
    rows, warns = relations_bake.assemble_char_rows(
        "char_x",
        [_parsed("ally_of", "暮落")],
        aliases,
    )
    assert warns == []
    assert len(rows) == 1
    assert rows[0]["tail"] is None
    assert rows[0]["tail_name"] == "暮落"
    assert rows[0]["ambiguous_candidates"] == ["char_a", "char_b"]


def test_assemble_rows_novel_type_warns_but_keeps_row():
    """Novel `type` is signal, not noise — the row is kept so the bake's
    contribution isn't lost, but the curator sees the warning."""
    aliases = {"X": ["ent_x"]}
    rows, warns = relations_bake.assemble_char_rows(
        "char_x",
        [_parsed("invented_relation_type", "X")],
        aliases,
    )
    assert len(rows) == 1
    assert any("novel" in w["reason"] for w in warns)


# -------- rows_to_jsonl ---------------------------------------------


def test_rows_to_jsonl_empty_is_empty_string():
    """An empty-relations bake writes an empty file — the manifest still
    records the source_hash so re-bakes skip."""
    assert relations_bake.rows_to_jsonl([]) == ""


def test_rows_to_jsonl_one_row_round_trip():
    row = {"head": "a", "type": "member_of", "tail": "b", "tail_name": "B"}
    out = relations_bake.rows_to_jsonl([row])
    assert out.endswith("\n")
    assert json.loads(out.strip()) == row


# -------- handbook reading ------------------------------------------


def _make_char_dir(tmp_path: Path, char_id: str, sections: dict[str, str]) -> Path:
    cdir = tmp_path / "kb" / "chars" / char_id
    cdir.mkdir(parents=True)
    (cdir / "manifest.json").write_text(
        json.dumps({"char_id": char_id, "name": "测试"}), encoding="utf-8"
    )
    for name, body in sections.items():
        (cdir / name).write_text(body, encoding="utf-8")
    return tmp_path / "kb"


def test_read_char_handbook_skips_missing_sections(tmp_path):
    kb_root = _make_char_dir(tmp_path, "char_x", {"profile.txt": "P", "archive.txt": "A"})
    out = relations_bake.read_char_handbook(kb_root, "char_x")
    assert [name for name, _ in out] == ["profile.txt", "archive.txt"]
    assert [text for _, text in out] == ["P", "A"]


def test_read_char_handbook_empty_when_no_sections(tmp_path):
    (tmp_path / "kb" / "chars" / "char_x").mkdir(parents=True)
    assert relations_bake.read_char_handbook(tmp_path / "kb", "char_x") == []


# -------- build_user_prompt -----------------------------------------


def test_build_user_prompt_includes_char_name_and_handbook():
    p = relations_bake.build_user_prompt(
        "阿米娅", [("profile.txt", "PROFILE_TEXT"), ("archive.txt", "ARCHIVE_TEXT")]
    )
    assert "阿米娅" in p
    assert "PROFILE_TEXT" in p
    assert "ARCHIVE_TEXT" in p
    # Section headers — so the LLM can tell the sections apart.
    assert "=== profile.txt ===" in p
    assert "=== archive.txt ===" in p
    # Vocabulary listed.
    for rt in relations_bake.RELATION_TYPES:
        assert f"`{rt}`" in p


# -------- bake_relations_all end-to-end -----------------------------


def _ent_aliases() -> dict[str, list[str]]:
    return {
        "罗德岛": ["ent_rhodes"],
        "凯尔希": ["char_003_kalts"],
    }


def _bake_response() -> str:
    return _wrap(
        "member_of;罗德岛;阿米娅是领导者之一\n"
        "mentor_of;凯尔希\n"
    )


def test_bake_relations_writes_per_char_jsonl(tmp_path):
    kb_root = _make_char_dir(
        tmp_path, "char_002_amiya", {"profile.txt": "profile body"}
    )
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()
    client = FakeClient(responses=[_bake_response()])

    report = relations_bake.bake_relations_all(
        kb_root, rels_root, client, _ent_aliases(),
        {"char_002_amiya": {"char_id": "char_002_amiya", "name": "阿米娅"}},
    )

    assert report.errors == []
    assert report.wrote == ["char_002_amiya"]
    out_path = paths.char_relations_path(rels_root, "char_002_amiya")
    assert out_path.is_file()
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[0]["head"] == "char_002_amiya"
    # Manifest persisted.
    manifest = json.loads(
        paths.relations_manifest_path(rels_root).read_text(encoding="utf-8")
    )
    assert "char_002_amiya" in manifest["chars"]
    assert manifest["chars"]["char_002_amiya"]["source_hash"]


def test_bake_skips_when_hash_unchanged(tmp_path):
    """Second run with same handbook should not call the LLM."""
    kb_root = _make_char_dir(tmp_path, "char_x", {"profile.txt": "stable body"})
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()
    chars = {"char_x": {"char_id": "char_x", "name": "X"}}

    # First run — one response consumed.
    relations_bake.bake_relations_all(
        kb_root, rels_root, FakeClient(responses=[_wrap("member_of;罗德岛")]),
        {"罗德岛": ["ent_rhodes"]}, chars,
    )

    # Second run — empty response list; should not be touched.
    client2 = FakeClient(responses=[])
    report = relations_bake.bake_relations_all(
        kb_root, rels_root, client2, {"罗德岛": ["ent_rhodes"]}, chars,
    )
    assert report.wrote == []
    assert report.skipped == ["char_x"]
    assert client2.calls == []


def test_bake_force_reruns_even_when_hash_matches(tmp_path):
    kb_root = _make_char_dir(tmp_path, "char_x", {"profile.txt": "stable"})
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()
    chars = {"char_x": {"char_id": "char_x", "name": "X"}}

    relations_bake.bake_relations_all(
        kb_root, rels_root,
        FakeClient(responses=[_wrap("member_of;罗德岛")]),
        {"罗德岛": ["ent_rhodes"]}, chars,
    )

    client2 = FakeClient(responses=[_wrap("ally_of;凯尔希")])
    report = relations_bake.bake_relations_all(
        kb_root, rels_root, client2,
        {"凯尔希": ["char_003_kalts"]},
        chars, force=True,
    )
    assert report.wrote == ["char_x"]
    assert len(client2.calls) == 1


def test_bake_only_filter_restricts_to_one_char(tmp_path):
    kb_root = _make_char_dir(tmp_path, "char_a", {"profile.txt": "A"})
    (kb_root / "chars" / "char_b").mkdir()
    (kb_root / "chars" / "char_b" / "manifest.json").write_text(
        json.dumps({"char_id": "char_b", "name": "B"}), encoding="utf-8"
    )
    (kb_root / "chars" / "char_b" / "profile.txt").write_text("B", encoding="utf-8")
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()
    chars = {
        "char_a": {"char_id": "char_a", "name": "A"},
        "char_b": {"char_id": "char_b", "name": "B"},
    }

    client = FakeClient(responses=[_wrap("")])
    report = relations_bake.bake_relations_all(
        kb_root, rels_root, client, {}, chars, only=["char_a"],
    )
    assert report.wrote == ["char_a"]
    # No write for char_b (and its file should not exist).
    assert not paths.char_relations_path(rels_root, "char_b").is_file()


def test_bake_nameless_char_is_skipped(tmp_path):
    """A char with no `name` has no useful bake target — skipped silently."""
    kb_root = _make_char_dir(tmp_path, "char_x", {"profile.txt": "body"})
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()

    client = FakeClient(responses=[])
    report = relations_bake.bake_relations_all(
        kb_root, rels_root, client, {},
        {"char_x": {"char_id": "char_x", "name": ""}},  # nameless
    )
    assert report.wrote == []
    assert client.calls == []


# -------- estimate_remaining_relations ------------------------------


def test_estimate_classifies_to_run_vs_done(tmp_path):
    kb_root = _make_char_dir(tmp_path, "char_a", {"profile.txt": "AAAA"})
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()

    chars = {"char_a": {"char_id": "char_a", "name": "A"}}
    est = relations_bake.estimate_remaining_relations(
        kb_root, rels_root, chars
    )
    assert est.n_to_run == 1
    assert est.llm_calls == 1
    assert est.in_chars > 0

    # Now bake it.
    relations_bake.bake_relations_all(
        kb_root, rels_root, FakeClient(responses=[_wrap("")]),
        {}, chars,
    )

    est2 = relations_bake.estimate_remaining_relations(
        kb_root, rels_root, chars
    )
    assert est2.n_to_run == 0
    assert len(est2.already_done) == 1


def test_estimate_skips_chars_with_no_handbook(tmp_path):
    """A char without any handbook section can't be baked — also excluded
    from the cost estimate so the figure stays honest."""
    cdir = tmp_path / "kb" / "chars" / "char_x"
    cdir.mkdir(parents=True)
    (cdir / "manifest.json").write_text(
        json.dumps({"char_id": "char_x", "name": "X"}), encoding="utf-8"
    )
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()
    chars = {"char_x": {"char_id": "char_x", "name": "X"}}
    est = relations_bake.estimate_remaining_relations(
        tmp_path / "kb", rels_root, chars
    )
    assert est.n_to_run == 0


def test_estimate_force_returns_everything_as_to_run(tmp_path):
    kb_root = _make_char_dir(tmp_path, "char_x", {"profile.txt": "X"})
    rels_root = tmp_path / "kb_relations"
    rels_root.mkdir()
    chars = {"char_x": {"char_id": "char_x", "name": "X"}}
    relations_bake.bake_relations_all(
        kb_root, rels_root, FakeClient(responses=[_wrap("")]), {}, chars
    )
    est = relations_bake.estimate_remaining_relations(
        kb_root, rels_root, chars, force=True
    )
    assert est.n_to_run == 1
    assert len(est.already_done) == 0
