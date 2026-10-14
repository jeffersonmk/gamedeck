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

from typing import Any

from gi.repository import Gtk

from cartridges import shared
from cartridges.game import Game


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/game.ui")
class GameWidget(Gtk.Box):
    """The library card of a `Game`. Main thread only."""

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

    def __init__(self, game: Game, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.game = game

        self.set_play_icon()
        self.update_loading()

        self.event_contoller_motion = Gtk.EventControllerMotion.new()
        self.add_controller(self.event_contoller_motion)
        self.event_contoller_motion.connect("enter", self.toggle_play, False)
        self.event_contoller_motion.connect("leave", self.toggle_play, None, None)
        self.cover_button.connect("clicked", self.main_button_clicked, False)
        self.play_button.connect("clicked", self.main_button_clicked, True)

        self._menu_model = None

    def sync(self) -> None:
        """Show the current values of the game"""
        menu_model = self.hidden_game_options if self.game.hidden else self.game_options
        if menu_model is not self._menu_model:
            # GTK builds a new popover for every set_menu_model() call, so
            # only replace it when the menu actually changes
            self._menu_model = menu_model
            self.menu_button.set_menu_model(menu_model)
            popover = self.menu_button.get_popover()
            popover.connect("notify::visible", self.toggle_play, None)
            popover.connect("notify::visible", shared.win.set_active_game, self.game)

        self.title.set_label(self.game.name)

    def update_loading(self) -> None:
        loading = self.game.loading > 0
        self.cover.set_opacity(int(not loading))
        self.spinner.set_visible(loading)

    def toggle_play(
        self, _widget: Any, _prop1: Any, _prop2: Any, state: bool = True
    ) -> None:
        if not self.menu_button.get_active():
            self.play_revealer.set_reveal_child(not state)
            self.menu_revealer.set_reveal_child(not state)

    def main_button_clicked(self, _widget: Any, button: bool) -> None:
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
