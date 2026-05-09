"""Test bootstrap: makes `libs.*` importable from any cwd and exposes
the synthetic mini gamedata path. Provides shared fixtures for the kb
test suites (test_indexer, test_query) so per-test scaffolding stays
out of individual test files."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Mapping

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def mini_gamedata_path() -> str:
    return str(_REPO_ROOT / "tests" / "fixtures" / "mini_gamedata")


# --- synthetic-KB factories used by indexer/query tests -------------


@pytest.fixture
def make_event() -> Callable[..., dict]:
    """Factory: write a synthetic event with hand-crafted per-stage
    bodies. The indexer only needs `event.json` + `stage_*.txt`; nothing
    in this helper depends on the chunker, so callers can construct
    pathological cases (single-char matches, blocklist hits,
    subtraction-rule scenarios) directly."""
    from libs.kb import paths

    def _make(
        kb_root: Path,
        event_id: str,
        stage_bodies: list[tuple[str, str]],
        *,
        name: str = "evt",
        entry_type: str = "ACTIVITY",
        source_family: str = "activity",
        storytxt_prefix: str = "activities/test",
    ) -> dict:
        event_dir = paths.event_dir(kb_root, event_id)
        event_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        for i, (sname, body) in enumerate(stage_bodies):
            fname = f"stage_{i:02d}_{sname}.txt"
            (event_dir / fname).write_text(body, encoding="utf-8")
            stages.append(
                {
                    "idx": i,
                    "name": sname,
                    "avgTag": None,
                    "file": fname,
                    "length": len(body),
                    "story_txt": f"{storytxt_prefix}/{sname}",
                }
            )
        manifest = {
            "event_id": event_id,
            "name": name,
            "entryType": entry_type,
            "source_family": source_family,
            "storyTxt_prefixes": [storytxt_prefix],
            "stages": stages,
            "total_length": sum(s["length"] for s in stages),
            "source_data_version": "test",
        }
        (event_dir / "event.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        return manifest

    return _make


@pytest.fixture
def make_char() -> Callable[..., dict]:
    """Factory: write a synthetic char dir with manifest + storysets."""
    from libs.kb import paths

    def _make(
        kb_root: Path,
        char_id: str,
        *,
        name: str,
        appellation: str | None = None,
        nation: str | None = None,
        storysets: list[dict] | None = None,
        sections: list[str] | None = None,
        aliases: list[str] | None = None,
        profile_text: str | None = None,
    ) -> dict:
        cdir = paths.char_dir(kb_root, char_id)
        cdir.mkdir(parents=True, exist_ok=True)
        storysets = storysets or []
        if aliases is None:
            aliases = [a for a in (name, appellation) if a]
        manifest = {
            "char_id": char_id,
            "name": name,
            "appellation": appellation,
            "aliases": aliases,
            "nationId": nation,
            "sections": list(sections or []),
            "storyset_count": len(storysets),
        }
        (cdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        (cdir / "storysets.json").write_text(
            json.dumps(storysets, ensure_ascii=False), encoding="utf-8"
        )
        if profile_text is not None:
            (cdir / "profile.txt").write_text(profile_text, encoding="utf-8")
        return manifest

    return _make


@pytest.fixture
def build_real_kb(mini_gamedata_path: str) -> Callable[..., Path]:
    """Factory: chunk the mini fixture into a fresh KB on disk and run
    the indexer. Returns the kb_root path. Pass `curated={canonical:
    [aliases]}` to also write a `char_alias.txt` and index it."""
    from libs import game_data
    from libs.kb import chunker, indexer

    def _build(kb_root: Path, *, curated: Mapping[str, list[str]] | None = None) -> Path:
        sr = game_data.extract_data_from_story_review_table(mini_gamedata_path)
        ci, _ = game_data.get_all_char_info(mini_gamedata_path)
        storytxt_idx = chunker.build_storytxt_index(sr)
        for eid, ev in sr.items():
            chunker.write_event(kb_root, mini_gamedata_path, eid, ev, "test-v")
        for cid, char in ci.items():
            if not char.get("name"):
                continue
            chunker.write_char(kb_root, cid, char, storytxt_idx)
        curated_path = None
        if curated:
            curated_path = kb_root.parent / "char_alias.txt"
            curated_path.write_text(
                "\n".join(f"{k};" + ";".join(v) for k, v in curated.items()) + "\n",
                encoding="utf-8",
            )
        indexer.build_all_indexes(kb_root, curated_aliases_path=curated_path)
        return kb_root

    return _build


@pytest.fixture
def loaded_kb(tmp_path, build_real_kb):
    """Pre-built + loaded KB from the mini fixture (no curated file).
    Use this for read-only tests; if a test needs curated aliases or a
    custom KB layout, take `build_real_kb` directly."""
    from libs.kb import query

    kb_root = build_real_kb(tmp_path / "kb")
    return query.load_kb(kb_root)
