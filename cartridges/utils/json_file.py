# json_file.py
#
# Copyright 2026 GameDeck contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small helpers to read and write the per-game JSON files safely."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any, **dump_kwargs: Any) -> None:
    """Write `data` as JSON to `path` without ever leaving a truncated file.

    The data is written to a temporary file in the same directory, then moved
    over `path` with an atomic rename, so a crash or a full disk mid-write
    keeps the previous version intact instead of corrupting the game.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, **dump_kwargs)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    """Read a JSON file, always closing it."""
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def is_game_file(path: Path) -> bool:
    """Whether `path` is a game JSON file (not a leftover temporary file)."""
    return path.suffix == ".json" and not path.name.startswith(".")


# Keys a game file must have, with their accepted types. Files written by any
# Cartridges version contain them; anything else is corrupt or foreign data
# that would otherwise crash Game() and abort loading the whole library.
REQUIRED_GAME_KEYS: dict[str, tuple[type, ...]] = {
    "game_id": (str,),
    "source": (str,),
    "name": (str,),
    "executable": (str, list),
}


def is_valid_game_data(data: Any) -> bool:
    """Minimal structural validation of a game file's content."""
    if not isinstance(data, dict):
        return False
    return all(
        isinstance(data.get(key), types) for key, types in REQUIRED_GAME_KEYS.items()
    )
