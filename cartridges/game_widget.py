# game_widget.py
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

from typing import Any, Optional
from weakref import WeakSet

from gi.repository import Gtk

from cartridges import shared
from cartridges.game import Game
from cartridges.game_cover import GameCover


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/game.ui")
class GameWidget(Gtk.Box):
    """A library card. Main thread only.

    Cards are recycled by the library `Gtk.GridView`: only the visible ones
    exist, and `bind()` / `unbind()` attach them to a different `Game` as the
    user scrolls. The bound game keeps a reference in `game.widget`.
    """

    __gtype_name__ = "GameWidget"

    title = Gtk.Template.Child()
    play_button = Gtk.Template.Child()
    cover = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    cover_button = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    play_revealer = Gtk.Template.Child()
    menu_revealer = Gtk.Template.Child()
    game_options = Gtk.Template.Child()
    hidden_game_options = Gtk.Template.Child()

    # Every live card, to update them all when a setting changes
    instances: "WeakSet[GameWidget]" = WeakSet()

    game: Optional[Game] = None
    game_cover: Optional[GameCover] = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.instances.add(self)
        self._menu_model = None

        self.set_play_icon()

        self.event_contoller_motion = Gtk.EventControllerMotion.new()
        self.add_controller(self.event_contoller_motion)
        self.event_contoller_motion.connect("enter", self.toggle_play, False)
        self.event_contoller_motion.connect("leave", self.toggle_play, None, None)
        self.cover_button.connect("clicked", self.main_button_clicked, False)
        self.play_button.connect("clicked", self.main_button_clicked, True)

        # The menu is only built when opened: building a popover for every
        # card made scrolling and filtering slow
        self.menu_button.set_create_popup_func(self.create_menu)

    def create_menu(self, menu_button: Gtk.MenuButton) -> None:
        if not self.game:
            return
        menu_model = self.hidden_game_options if self.game.hidden else self.game_options
        if menu_model is self._menu_model:
            return
        # GTK builds a new popover for every set_menu_model() call
        self._menu_model = menu_model
        menu_button.set_menu_model(menu_model)
        popover = menu_button.get_popover()
        popover.connect("notify::visible", self.toggle_play, None)
        popover.connect("notify::visible", self.on_menu_visible)

    list_item: Optional[Gtk.ListItem] = None

    def bind(self, game: Game, list_item: Optional[Gtk.ListItem] = None) -> None:
        self.list_item = list_item
        self.game = game
        game.widget = self
        self.sync()

    def unbind(self) -> None:
        if self.game and self.game.widget is self:
            self.game.widget = None
        self.set_cover(None)
        self.game = None
        self.list_item = None
        if self.menu_button.get_active():
            self.menu_button.popdown()
        self.play_revealer.set_reveal_child(False)
        self.menu_revealer.set_reveal_child(False)

    def set_cover(self, game_cover: Optional[GameCover]) -> None:
        if game_cover is self.game_cover:
            return
        if self.game_cover:
            self.game_cover.remove_picture(self.cover)
        self.game_cover = game_cover
        if game_cover:
            game_cover.add_picture(self.cover)
        else:
            self.cover.set_paintable(None)

    def sync(self) -> None:
        """Show the current values of the bound game"""
        if not (game := self.game):
            return

        self.title.set_label(game.name)
        self.update_loading()
        self.set_cover(game.game_cover)

    def on_menu_visible(self, *_args: Any) -> None:
        if self.game:
            shared.win.active_game = self.game

    def update_loading(self) -> None:
        loading = bool(self.game and self.game.loading > 0)
        self.cover.set_opacity(int(not loading))
        self.spinner.set_visible(loading)

    def toggle_play(
        self, _widget: Any, _prop1: Any, _prop2: Any, state: bool = True
    ) -> None:
        if not self.menu_button.get_active():
            self.play_revealer.set_reveal_child(not state)
            self.menu_revealer.set_reveal_child(not state)

    def main_button_clicked(self, _widget: Any, button: bool) -> None:
        if not self.game:
            return
        if shared.schema.get_boolean("cover-launches-game") ^ button:
            self.game.launch()
        else:
            shared.win.show_details_page(self.game)

    def set_play_icon(self) -> None:
        self.play_button.set_icon_name(
            "help-about-symbolic"
            if shared.schema.get_boolean("cover-launches-game")
            else "media-playback-start-symbolic"
        )
