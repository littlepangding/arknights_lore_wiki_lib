"""Internal I/O helpers shared across the kb package.

Atomic-write helpers use `mkstemp` + `os.replace` so a partial write
never lands at the destination path — load-bearing because rebuild
runs over many small files and a crash mid-build would otherwise
corrupt the cache.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_or(path: Path, default: _T) -> _T:
    return read_json(path) if path.is_file() else default


def load_dir_manifests(root: Path, basename: str) -> dict[str, dict]:
    """Walk `root/*/`, return `{dirname: parsed_json(dir/basename)}` for
    every subdir that contains a `basename` JSON file. Used to load
    `events/<id>/event.json` and `chars/<id>/manifest.json` uniformly."""
    out: dict[str, dict] = {}
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        f = d / basename
        if f.is_file():
            out[d.name] = read_json(f)
    return out


def invert_alias_lists(
    rows: list[dict] | dict[str, list[str]],
    *,
    id_field: str | None = None,
    aliases_field: str = "aliases",
) -> dict[str, list[str]]:
    """Invert `{id: [aliases]}` (or a row list with id_field + aliases_field
    keys) into `{alias: [ids]}`. Multi-target rows are how the resolver
    encodes ambiguity (`暮落` → two char_ids; same shape for entity ids).
    Order within each list mirrors first-seen; the outer dict is sorted.

    When `rows` is a row list, `id_field` is required (one entity / one
    row → one id). When `rows` is a `{id: [aliases]}` dict, leave
    `id_field=None`."""
    out: dict[str, list[str]] = {}
    if isinstance(rows, dict):
        items = rows.items()
    else:
        if id_field is None:
            raise ValueError("id_field is required when rows is a list of dicts")
        items = ((r[id_field], r.get(aliases_field, [])) for r in rows)
    for owner_id, aliases in items:
        for alias in aliases:
            if not alias:
                continue
            bucket = out.setdefault(alias, [])
            if owner_id not in bucket:
                bucket.append(owner_id)
    return dict(sorted(out.items()))


def prune_stale_files(directory: Path, glob_pattern: str, keep: set[str]) -> list[str]:
    """Delete files in `directory` matching `glob_pattern` whose name is not
    in `keep`. Returns sorted list of removed filenames. Used after a build
    rewrites an entity to drop renamed/removed chunks before they leak into
    later grep / filesystem scans."""
    if not directory.is_dir():
        return []
    removed: list[str] = []
    for p in sorted(directory.glob(glob_pattern)):
        if p.name not in keep:
            p.unlink()
            removed.append(p.name)
    return removed
