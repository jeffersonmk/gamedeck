# save_cover.py
#
# Copyright 2022-2023 kramo
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


import threading
from pathlib import Path
from shutil import copyfile
from typing import Optional

from gi.repository import Gdk, GdkPixbuf, Gio, GLib
from PIL import Image, ImageSequence, UnidentifiedImageError

from cartridges import shared


def _new_tmp_path(template: str) -> Path:
    file, stream = Gio.File.new_tmp(template)
    stream.close()
    return Path(file.get_path())


def convert_cover(
    cover_path: Optional[Path] = None,
    pixbuf: Optional[GdkPixbuf.Pixbuf] = None,
    resize: bool = True,
) -> Optional[Path]:
    """Convert an image to the cover format.

    Returns either `cover_path` itself (only when `resize` is False and the
    format is already supported) or a new temporary file owned by the caller,
    who should remove it with `remove_tmp_cover` once done.
    Intermediate temporary files are always removed.
    """
    if not cover_path and not pixbuf:
        return None

    pixbuf_extensions = set()
    for pixbuf_format in GdkPixbuf.Pixbuf.get_formats():
        for pixbuf_extension in pixbuf_format.get_extensions():
            pixbuf_extensions.add(pixbuf_extension)

    if not resize and cover_path and cover_path.suffix.lower()[1:] in pixbuf_extensions:
        return cover_path

    intermediate: Optional[Path] = None
    if pixbuf:
        cover_path = intermediate = _new_tmp_path("XXXXXX.tiff")
        pixbuf.savev(str(cover_path), "tiff")

    try:
        with Image.open(cover_path) as image:
            if getattr(image, "is_animated", False):
                frames = tuple(
                    frame.resize((200, 300)) if resize else frame
                    for frame in ImageSequence.Iterator(image)
                )

                tmp_path = _new_tmp_path("XXXXXX.gif")
                frames[0].save(
                    tmp_path,
                    save_all=True,
                    append_images=frames[1:],
                )

            else:
                # This might not be necessary in the future
                # https://github.com/python-pillow/Pillow/issues/2663
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA")

                tmp_path = _new_tmp_path("XXXXXX.tiff")
                (image.resize(shared.image_size) if resize else image).save(
                    tmp_path,
                    compression="tiff_adobe_deflate"
                    if shared.schema.get_boolean("high-quality-images")
                    else shared.TIFF_COMPRESSION,
                )
    except UnidentifiedImageError:
        try:
            fallback = _new_tmp_path("XXXXXX.tiff")
            Gdk.Texture.new_from_filename(str(cover_path)).save_to_tiff(str(fallback))
            try:
                return convert_cover(fallback)
            finally:
                fallback.unlink(missing_ok=True)
        except GLib.Error:
            return None
    finally:
        if intermediate:
            intermediate.unlink(missing_ok=True)

    return tmp_path


def remove_tmp_cover(path: Optional[Path], *originals: Optional[Path]) -> None:
    """Remove a temporary file returned by `convert_cover`/a download,
    unless it is one of the caller's original (non-temporary) files."""
    if path and path not in originals:
        Path(path).unlink(missing_ok=True)


def save_cover(game_id: str, cover_path: Path) -> None:
    """Store `cover_path` as the cover of `game_id`.

    Safe to call from worker threads: the file operations happen in the
    calling thread, the widget update is always done on the main thread.
    """
    shared.covers_dir.mkdir(parents=True, exist_ok=True)

    animated_path = shared.covers_dir / f"{game_id}.gif"
    static_path = shared.covers_dir / f"{game_id}.tiff"

    # Remove previous covers
    animated_path.unlink(missing_ok=True)
    static_path.unlink(missing_ok=True)

    if not cover_path:
        return

    new_path = animated_path if cover_path.suffix == ".gif" else static_path
    copyfile(cover_path, new_path)

    def update_displayed_cover() -> bool:
        if shared.win and game_id in shared.win.game_covers:
            shared.win.game_covers[game_id].new_cover(new_path)
        return False

    if threading.current_thread() is threading.main_thread():
        update_displayed_cover()
    else:
        GLib.idle_add(update_displayed_cover)
