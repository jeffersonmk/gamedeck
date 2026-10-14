# game_cover.py
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

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from os import cpu_count
from pathlib import Path
from threading import Lock
from typing import Optional

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from PIL import Image, ImageFilter, ImageStat

from cartridges import shared

_DECODER: Optional[ThreadPoolExecutor] = None


def _decoder() -> ThreadPoolExecutor:
    """Small shared pool decoding cover files off the main thread
    (gdk_texture_new_from_filename is documented as thread-safe)"""
    global _DECODER  # pylint: disable=global-statement
    if _DECODER is None:
        _DECODER = ThreadPoolExecutor(
            max_workers=min(4, cpu_count() or 1), thread_name_prefix="cover"
        )
    return _DECODER


def _load_texture(path: Path) -> Optional[Gdk.Texture]:
    try:
        return Gdk.Texture.new_from_filename(str(path))
    except GLib.Error:
        # A corrupt cover file must not prevent the game from being shown
        return None


# Decoded textures waiting to be applied on the main thread, in batches:
# one main-loop callback per batch instead of one per cover
_PENDING: list[tuple["GameCover", int, Optional[Gdk.Texture]]] = []
_PENDING_LOCK = Lock()
_PENDING_SCHEDULED = False


def _deliver(cover: "GameCover", generation: int, texture: Optional[Gdk.Texture]) -> None:
    global _PENDING_SCHEDULED  # pylint: disable=global-statement
    with _PENDING_LOCK:
        _PENDING.append((cover, generation, texture))
        if _PENDING_SCHEDULED:
            return
        _PENDING_SCHEDULED = True
    # Default (not idle) priority: an idle callback can be starved for seconds
    # by layout/redraw work on large libraries, delaying every cover
    GLib.idle_add(_apply_pending, priority=GLib.PRIORITY_DEFAULT)


def _apply_pending() -> bool:
    global _PENDING_SCHEDULED  # pylint: disable=global-statement
    with _PENDING_LOCK:
        batch = _PENDING.copy()
        _PENDING.clear()
        _PENDING_SCHEDULED = False
    for cover, generation, texture in batch:
        cover.on_texture_loaded(generation, texture)
    return False


class GameCover:
    """Cover of a game, shared by every `Gtk.Picture` showing it.

    Images are decoded on demand: only when a picture is attached (a card
    scrolled into view, the details page...) or when `get_texture()` is
    called. Covers of games that were never shown cost no memory.
    """

    texture: Optional[Gdk.Texture]
    blurred: Optional[Gdk.Texture]
    luminance: Optional[tuple[float, float]]
    path: Optional[Path]
    animation: Optional[GdkPixbuf.PixbufAnimation]
    anim_iter: Optional[GdkPixbuf.PixbufAnimationIter]

    placeholder = Gdk.Texture.new_from_resource(
        shared.PREFIX + "/library_placeholder.svg"
    )
    placeholder_small = Gdk.Texture.new_from_resource(
        shared.PREFIX + "/library_placeholder_small.svg"
    )

    def __init__(
        self, pictures: Optional[set[Gtk.Picture]] = None, path: Optional[Path] = None
    ) -> None:
        self.pictures = pictures if pictures is not None else set()
        self._anim_source = 0
        self._generation = 0
        self._loading = False
        self.new_cover(path, initial=True)

    def new_cover(self, path: Optional[Path] = None, initial: bool = False) -> None:
        self._generation += 1
        self._loading = False
        self._stop_animation()
        self.animation = None
        self.anim_iter = None
        self.texture = None
        self.blurred = None
        self.luminance = None
        self.path = path

        if not self.pictures:
            return  # Loaded when a picture is attached

        if path and path.suffix != ".gif" and not initial:
            # Replaced covers keep showing the old image until the new one
            # is decoded
            self._load()
            return

        self.set_texture(None)
        self._load()

    def _load(self) -> None:
        """Start loading the image (main thread)"""
        if not self.path or self._loading or self.texture or self.animation:
            return

        if self.path.suffix == ".gif":
            try:
                self.animation = GdkPixbuf.PixbufAnimation.new_from_file(
                    str(self.path)
                )
            except GLib.Error:
                return
            self.anim_iter = self.animation.get_iter()
            self.update_animation((None, self.animation))
            return

        # Decode in the background so the UI isn't blocked by reading files
        self._loading = True
        generation, path = self._generation, self.path

        def decode() -> None:
            _deliver(self, generation, _load_texture(path))

        _decoder().submit(decode)

    def on_texture_loaded(
        self, generation: int, texture: Optional[Gdk.Texture]
    ) -> None:
        # Ignore results for a cover that has been replaced in the meantime
        if generation != self._generation or self.animation:
            return
        self._loading = False
        if self.texture is None:
            self.texture = texture
        self.set_texture(self.texture)

    def get_texture(self) -> Gdk.Texture:
        if self.animation:
            return Gdk.Texture.new_for_pixbuf(self.animation.get_static_image())
        if self.texture is None and self.path:
            if self.path.suffix == ".gif":
                try:
                    return Gdk.Texture.new_for_pixbuf(
                        GdkPixbuf.PixbufAnimation.new_from_file(
                            str(self.path)
                        ).get_static_image()
                    )
                except GLib.Error:
                    return None
            # Needed now (e.g. by the details dialog): don't wait for the pool
            self.texture = _load_texture(self.path)
        return self.texture

    def get_blurred(self) -> Gdk.Texture:
        if not self.blurred:
            if self.path:
                with Image.open(self.path) as image:
                    image = (
                        image.convert("RGB")
                        .resize((100, 150))
                        .filter(ImageFilter.GaussianBlur(20))
                    )

                    buffer = BytesIO()
                    image.save(buffer, "tiff", compression=None)
                    gbytes = GLib.Bytes.new(buffer.getvalue())

                    self.blurred = Gdk.Texture.new_from_bytes(gbytes)

                    stat = ImageStat.Stat(image.convert("L"))

                    # Luminance values for light and dark mode
                    self.luminance = (
                        min((stat.mean[0] + stat.extrema[0][0]) / 510, 0.7),
                        max((stat.mean[0] + stat.extrema[0][1]) / 510, 0.3),
                    )
            else:
                self.blurred = self.placeholder_small
                self.luminance = (0.3, 0.5)

        return self.blurred

    def add_picture(self, picture: Gtk.Picture) -> None:
        self.pictures.add(picture)
        if self.animation:
            self.update_animation((None, self.animation))
            return
        picture.set_paintable(self.texture or self.placeholder)
        self._load()

    def remove_picture(self, picture: Gtk.Picture) -> None:
        self.pictures.discard(picture)
        if not self.pictures:
            # Nothing shows it: stop animating (restarted by add_picture)
            self._stop_animation()

    def set_texture(self, texture: Optional[Gdk.Texture]) -> None:
        for picture in self.pictures:
            picture.set_paintable(texture or self.placeholder)
            picture.queue_draw()

    def _stop_animation(self) -> None:
        if self._anim_source:
            GLib.source_remove(self._anim_source)
            self._anim_source = 0

    def update_animation(self, data: GdkPixbuf.PixbufAnimation) -> None:
        if self.animation != data[1] or not self.pictures:
            return

        self.anim_iter.advance()  # type: ignore

        self.set_texture(Gdk.Texture.new_for_pixbuf(self.anim_iter.get_pixbuf()))  # type: ignore

        delay_time = self.anim_iter.get_delay_time()  # type: ignore

        # Keep a single timer per cover: every call used to start one more
        # never-ending chain (e.g. each time the details page was opened)
        self._stop_animation()
        self._anim_source = GLib.timeout_add(
            20 if delay_time < 20 else delay_time,
            self._on_animation_timeout,
            data,
        )

    def _on_animation_timeout(self, data: GdkPixbuf.PixbufAnimation) -> bool:
        self._anim_source = 0
        self.update_animation(data)
        return False
