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

    def __init__(self, pictures: set[Gtk.Picture], path: Optional[Path] = None) -> None:
        self.pictures = pictures
        self._anim_source = 0
        self._generation = 0
        self.new_cover(path, initial=True)

    def new_cover(self, path: Optional[Path] = None, initial: bool = False) -> None:
        self._generation += 1
        self.animation = None
        self.texture = None
        self.blurred = None
        self.luminance = None
        self.path = path

        if path:
            if path.suffix == ".gif":
                self.animation = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
                self.anim_iter = self.animation.get_iter()
                # Widgets may only be touched from the main thread: start the
                # animation from the main loop instead of a worker thread
                GLib.idle_add(self.update_animation, (None, self.animation))
            else:
                # Decode in the background so startup isn't blocked by reading
                # every cover. New covers show the placeholder until ready;
                # replaced covers keep the old image until the new one is ready.
                if initial:
                    self.set_texture(None)
                generation = self._generation

                def decode() -> None:
                    _deliver(self, generation, _load_texture(path))

                _decoder().submit(decode)
                return

        if not self.animation:
            self.set_texture(self.texture)

    def on_texture_loaded(
        self, generation: int, texture: Optional[Gdk.Texture]
    ) -> None:
        # Ignore results for a cover that has been replaced in the meantime
        if generation == self._generation and not self.animation:
            if self.texture is None:
                self.texture = texture
            self.set_texture(self.texture)

    def get_texture(self) -> Gdk.Texture:
        if self.animation:
            return Gdk.Texture.new_for_pixbuf(self.animation.get_static_image())
        if self.texture is None and self.path:
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
        if not self.animation:
            self.set_texture(self.texture)
        else:
            self.update_animation((None, self.animation))

    def set_texture(self, texture: Gdk.Texture) -> None:
        self.pictures.discard(
            picture for picture in self.pictures if not picture.is_visible()
        )
        if not self.pictures:
            self.animation = None
        else:
            for picture in self.pictures:
                picture.set_paintable(texture or self.placeholder)
                picture.queue_draw()

    def update_animation(self, data: GdkPixbuf.PixbufAnimation) -> None:
        if self.animation == data[1]:
            self.anim_iter.advance()  # type: ignore

            self.set_texture(Gdk.Texture.new_for_pixbuf(self.anim_iter.get_pixbuf()))  # type: ignore

            delay_time = self.anim_iter.get_delay_time()  # type: ignore

            # Keep a single timer per cover: every call used to start one more
            # never-ending chain (e.g. each time the details page was opened)
            if self._anim_source:
                GLib.source_remove(self._anim_source)
            self._anim_source = GLib.timeout_add(
                20 if delay_time < 20 else delay_time,
                self._on_animation_timeout,
                data,
            )

    def _on_animation_timeout(self, data: GdkPixbuf.PixbufAnimation) -> bool:
        self._anim_source = 0
        self.update_animation(data)
        return False
