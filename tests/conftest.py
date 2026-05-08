"""Test bootstrap: makes `libs.*` importable from any cwd and exposes the
synthetic mini gamedata path. Fixtures pass `game_data_path` directly so no
test needs `keys.json`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def mini_gamedata_path() -> str:
    return str(_REPO_ROOT / "tests" / "fixtures" / "mini_gamedata")
