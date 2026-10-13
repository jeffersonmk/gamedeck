"""Shared setup for GameDeck tests.

The app modules need a meson-installed build (compiled resources, configured
shared.py, GSettings schema). Point GAMEDECK_PREFIX at it (default: _install
in the repo root, created with `meson setup _build --prefix=$PWD/_install &&
meson install -C _build`).

Everything runs in a throwaway XDG/HOME/TMPDIR with in-memory GSettings, so
the user's real library and settings are never touched. No display is needed.
"""

import gettext
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PREFIX = Path(os.environ.get("GAMEDECK_PREFIX", REPO / "_install")).resolve()

if not (PREFIX / "share" / "cartridges" / "cartridges.gresource").is_file():
    raise SystemExit(
        f"No installed build found in {PREFIX}. Run:\n"
        "  meson setup _build --prefix=$PWD/_install && meson install -C _build\n"
        "or set GAMEDECK_PREFIX."
    )

SANDBOX = Path(tempfile.mkdtemp(prefix="gamedeck-tests-"))
for var, sub in (
    ("HOME", "home"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_CACHE_HOME", "cache"),
    ("TMPDIR", "tmp"),
):
    (SANDBOX / sub).mkdir()
    os.environ[var] = str(SANDBOX / sub)
tempfile.tempdir = None  # re-read TMPDIR
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["GSETTINGS_SCHEMA_DIR"] = str(PREFIX / "share" / "glib-2.0" / "schemas")

sys.path.insert(0, str(next((PREFIX / "lib").glob("python3*/site-packages"))))
sys.path.insert(1, str(PREFIX / "share" / "cartridges"))
gettext.install("cartridges", names=["ngettext"])

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib  # noqa: E402

Gio.Resource.load(str(PREFIX / "share" / "cartridges" / "cartridges.gresource"))._register()

TMP = Path(os.environ["TMPDIR"])


def iterate_main_loop(until=lambda: False, timeout: float = 5.0) -> None:
    """Run the default main context until `until()` or the timeout."""
    import time

    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end and not until():
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)
    while ctx.pending():
        ctx.iteration(False)
