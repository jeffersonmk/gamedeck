# display_manager.py
#
# Copyright 2023 Geoffrey Coulaud
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

from cartridges import shared
from cartridges.game import Game
from cartridges.game_cover import GameCover
from cartridges.store.managers.manager import Manager
from cartridges.store.managers.sgdb_manager import SgdbManager
from cartridges.store.managers.steam_api_manager import SteamAPIManager


class DisplayManager(Manager):
    """Manager in charge of adding a game to the UI"""

    run_after = (SteamAPIManager, SgdbManager)
    signals = {"update-ready"}

    def main(self, game: Game, _additional_data: dict) -> None:
        # Covers are created without decoding anything: images are loaded
        # when a card showing them scrolls into view
        if (game_cover := shared.win.game_covers.get(game.game_id)) is None:
            game_cover = GameCover(path=game.get_cover_path())
            shared.win.game_covers[game.game_id] = game_cover
        game.game_cover = game_cover

        # Refresh the card if the game is currently on screen
        if game.widget:
            game.widget.sync()

        if (
            shared.win.navigation_view.get_visible_page() == shared.win.details_page
            and shared.win.active_game == game
        ):
            shared.win.show_details_page(game)

        # Add, refresh or remove the game in the library views (batched)
        shared.win.queue_game_update(game)

        if shared.win.get_application().state == shared.AppState.DEFAULT:
            shared.win.queue_create_source_rows()
