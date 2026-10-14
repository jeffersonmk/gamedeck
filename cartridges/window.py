# window.py
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

# pyright: reportAssignmentType=none

from sys import platform
from typing import Any, Optional

from cartridges import shared
from cartridges.game import Game
from cartridges.game_cover import GameCover
from cartridges.game_widget import GameWidget
from cartridges.utils.relative_date import relative_date
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango


@Gtk.Template(resource_path=shared.PREFIX + "/gtk/window.ui")
class CartridgesWindow(Adw.ApplicationWindow):
    __gtype_name__ = "CartridgesWindow"

    overlay_split_view: Adw.OverlaySplitView = Gtk.Template.Child()
    navigation_view: Adw.NavigationView = Gtk.Template.Child()
    sidebar_navigation_page: Adw.NavigationPage = Gtk.Template.Child()
    sidebar: Gtk.ListBox = Gtk.Template.Child()
    all_games_row_box: Gtk.Box = Gtk.Template.Child()
    all_games_no_label: Gtk.Label = Gtk.Template.Child()
    added_row_box: Gtk.Box = Gtk.Template.Child()
    added_games_no_label: Gtk.Label = Gtk.Template.Child()
    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    primary_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    show_sidebar_button: Gtk.Button = Gtk.Template.Child()
    details_view: Gtk.Overlay = Gtk.Template.Child()
    library_page: Adw.NavigationPage = Gtk.Template.Child()
    library_view: Adw.ToolbarView = Gtk.Template.Child()
    library: Gtk.GridView = Gtk.Template.Child()
    scrolledwindow: Gtk.ScrolledWindow = Gtk.Template.Child()
    library_overlay: Gtk.Overlay = Gtk.Template.Child()
    notice_empty: Adw.StatusPage = Gtk.Template.Child()
    notice_no_results: Adw.StatusPage = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    search_button: Gtk.ToggleButton = Gtk.Template.Child()

    details_page: Adw.NavigationPage = Gtk.Template.Child()
    details_view_toolbar_view: Adw.ToolbarView = Gtk.Template.Child()
    details_view_cover: Gtk.Picture = Gtk.Template.Child()
    details_view_spinner: Adw.Spinner = Gtk.Template.Child()
    details_view_title: Gtk.Label = Gtk.Template.Child()
    details_view_blurred_cover: Gtk.Picture = Gtk.Template.Child()
    details_view_play_button: Gtk.Button = Gtk.Template.Child()
    details_view_developer: Gtk.Label = Gtk.Template.Child()
    details_view_added: Gtk.ShortcutLabel = Gtk.Template.Child()
    details_view_last_played: Gtk.Label = Gtk.Template.Child()
    details_view_hide_button: Gtk.Button = Gtk.Template.Child()

    hidden_library_page: Adw.NavigationPage = Gtk.Template.Child()
    hidden_primary_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    hidden_library: Gtk.GridView = Gtk.Template.Child()
    hidden_library_view: Adw.ToolbarView = Gtk.Template.Child()
    hidden_scrolledwindow: Gtk.ScrolledWindow = Gtk.Template.Child()
    hidden_library_overlay: Gtk.Overlay = Gtk.Template.Child()
    hidden_notice_empty: Adw.StatusPage = Gtk.Template.Child()
    hidden_notice_no_results: Adw.StatusPage = Gtk.Template.Child()
    hidden_search_bar: Gtk.SearchBar = Gtk.Template.Child()
    hidden_search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    hidden_search_button: Gtk.ToggleButton = Gtk.Template.Child()

    game_covers: dict = {}
    toasts: dict = {}
    active_game: Game
    details_view_game_cover: Optional[GameCover] = None
    sort_state: str = "last_played"
    filter_state: str = "all"
    source_rows: dict = {}

    def create_source_rows(self) -> None:
        def get_removed(source_id: str) -> Any:
            removed = tuple(
                game.removed or game.hidden or game.blacklisted
                for game in shared.store.source_games[source_id].values()
            )
            return (
                (count,) if (count := sum(removed)) != len(removed) else False
            )  # Return a tuple because 0 == False and 1 == True

        total_games_no = 0
        restored = False

        selected_id = (
            self.source_rows[selected_row][0]
            if (selected_row := self.sidebar.get_selected_row()) in self.source_rows
            else None
        )

        if selected_row == self.added_row_box.get_parent():
            self.sidebar.select_row(self.added_row_box.get_parent())
            restored = True

        if added_missing := (
            not shared.store.source_games.get("imported")
            or not (removed := get_removed("imported"))
        ):
            self.sidebar.select_row(self.all_games_row_box.get_parent())
        else:
            games_no = len(shared.store.source_games["imported"]) - removed[0]
            self.added_games_no_label.set_label(str(games_no))
            total_games_no += games_no
        self.added_row_box.get_parent().set_visible(not added_missing)

        self.sidebar.get_row_at_index(2).set_visible(False)

        while row := self.sidebar.get_row_at_index(3):
            self.sidebar.remove(row)

        for source_id in shared.store.source_games:
            if source_id == "imported":
                continue
            if not (removed := get_removed(source_id)):
                continue

            row = Gtk.Box(
                margin_top=12,
                margin_bottom=12,
                margin_start=6,
                margin_end=6,
                spacing=12,
            )
            games_no = len(shared.store.source_games[source_id]) - removed[0]
            total_games_no += games_no

            row.append(
                Gtk.Image.new_from_icon_name(
                    "user-desktop-symbolic"
                    if (split_id := source_id.split("_")[0]) == "desktop"
                    else f"{split_id}-source-symbolic"
                )
            )

            row.append(
                Gtk.Label(
                    label=self.get_application().get_source_name(source_id),
                    halign=Gtk.Align.START,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.CHAR,
                )
            )

            row.append(
                games_no_label := Gtk.Label(
                    label=str(games_no),
                    hexpand=True,
                    halign=Gtk.Align.END,
                )
            )

            games_no_label.add_css_class("dim-label")

            # Order rows based on the number of games in them
            index = 3
            while source_row := self.sidebar.get_row_at_index(index):
                if self.source_rows[source_row][1] < games_no:
                    self.sidebar.insert(row, index)
                    break
                index += 1
            if not row.get_parent():
                self.sidebar.append(row)

            self.source_rows[row.get_parent()] = (
                source_id,
                games_no,
            )

            if source_id == selected_id:
                self.sidebar.select_row(row.get_parent())
                restored = True

            self.sidebar.get_row_at_index(2).set_visible(True)

        self.all_games_no_label.set_label(str(total_games_no))

        if not restored:
            self.sidebar.select_row(self.all_games_row_box.get_parent())

    def row_selected(self, _widget: Any, row: Gtk.ListBoxRow | None) -> None:
        if not row:
            return
        match row.get_child():
            case self.all_games_row_box:
                value = "all"
            case self.added_row_box:
                value = "imported"
            case _:
                value = self.source_rows[row][0]

        self.library_page.set_title(self.get_application().get_source_name(value))

        self.filter_state = value
        self.library_filter.changed(Gtk.FilterChange.DIFFERENT)
        self.set_library_child()

        if self.overlay_split_view.get_collapsed():
            self.overlay_split_view.set_show_sidebar(False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self._library_child_queued = False
        self._source_rows_queued = False

        # One handler for every game instead of one connection per game,
        # which kept removed games alive and scaled with the library size
        shared.schema.connect(
            "changed::cover-launches-game", self.on_cover_launches_game_changed
        )

        if platform == "darwin":
            self.sidebar_navigation_page.set_title("")

        self.details_view.set_measure_overlay(self.details_view_toolbar_view, True)
        self.details_view.set_clip_overlay(self.details_view_toolbar_view, False)

        # Library views: every displayed game lives once in `games_model`;
        # each GridView shows a filtered and sorted view of it and only
        # creates cards for the games on screen
        self.games_model = Gio.ListStore(item_type=Game)
        self._model_games: set[Game] = set()
        self._pending_games: dict[Game, None] = {}
        self._games_update_queued = False

        self.library_filter = Gtk.CustomFilter.new(self.filter_func, False)
        self.hidden_library_filter = Gtk.CustomFilter.new(self.filter_func, True)
        self.library_sorter = Gtk.CustomSorter.new(self.sort_func)
        # Sorted once, then filtered: typing in the search box only re-runs
        # the (cheap) filter, never the sort
        self.sorted_games = Gtk.SortListModel(
            model=self.games_model, sorter=self.library_sorter
        )
        self.library_model = Gtk.FilterListModel(
            model=self.sorted_games, filter=self.library_filter
        )
        self.hidden_library_model = Gtk.FilterListModel(
            model=self.sorted_games, filter=self.hidden_library_filter
        )
        for view, model in (
            (self.library, self.library_model),
            (self.hidden_library, self.hidden_library_model),
        ):
            view.set_model(Gtk.NoSelection(model=model))
            view.set_factory(self.create_card_factory())

            # Arrow keys move between cards, as they did with the FlowBox
            keys = Gtk.EventControllerKey()
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            keys.connect("key-pressed", self.on_library_key_pressed, view, model)
            view.add_controller(keys)

        self.set_library_child()

        self.notice_empty.set_icon_name(shared.APP_ID + "-symbolic")

        self.overlay_split_view.set_show_sidebar(
            shared.state_schema.get_boolean("show-sidebar")
        )

        self.sidebar.select_row(self.all_games_row_box.get_parent())

        if shared.PROFILE == "development":
            self.add_css_class("devel")

        # Connect search entries
        self.search_bar.connect_entry(self.search_entry)
        self.hidden_search_bar.connect_entry(self.hidden_search_entry)

        # Connect signals
        self.search_entry.connect("search-changed", self.search_changed, False)
        self.hidden_search_entry.connect("search-changed", self.search_changed, True)

        self.search_entry.connect("activate", self.show_details_page_search)
        self.hidden_search_entry.connect("activate", self.show_details_page_search)

        self.navigation_view.connect("popped", self.set_show_hidden)
        self.navigation_view.connect("pushed", self.set_show_hidden)

        self.sidebar.connect("row-selected", self.row_selected)

        style_manager = Adw.StyleManager.get_default()
        style_manager.connect("notify::dark", self.set_details_view_opacity)
        style_manager.connect("notify::high-contrast", self.set_details_view_opacity)

        # Columns: as many cards as fit, up to "library-rows" (default 10).
        # GridView keeps ~30 rows of cards per column alive, so its column
        # limit must match what is really shown, not a fixed maximum
        self._grid_padding: dict[str, int] = {}
        self._grid_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._grid_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )
        self.library.set_name("library")
        self.hidden_library.set_name("hidden-library")
        for scrolled_window, view in (
            (self.scrolledwindow, self.library),
            (self.hidden_scrolledwindow, self.hidden_library),
        ):
            scrolled_window.get_hadjustment().connect(
                "changed", self.update_columns, view
            )
        shared.schema.connect("changed::library-rows", self.on_library_rows_changed)

    CARD_WIDTH = 200 + 12  # Card plus spacing, see style.css
    GRID_PADDING = 2 * 9

    def update_columns(self, _adjustment: Any, view: Gtk.GridView) -> None:
        # The scrolled window's width doesn't depend on the view's margins
        width = view.get_parent().get_width()
        if width <= 0:
            return
        limit = shared.schema.get_uint("library-rows") or 10
        columns = max(1, min(limit, (width - self.GRID_PADDING) // self.CARD_WIDTH))
        if view.get_max_columns() != columns:
            view.set_max_columns(columns)

        # GridView spreads the leftover width between the columns; keep the
        # cards together and centered like the FlowBox did instead. Widget
        # margins are ignored inside a ScrolledWindow, CSS padding is not
        padding = max(0, width - self.GRID_PADDING - columns * self.CARD_WIDTH) // 2
        if self._grid_padding.get(view.get_name()) != padding:
            self._grid_padding[view.get_name()] = padding
            self._grid_css.load_from_string(
                "".join(
                    f"gridview#{name} {{ padding-left: {9 + pad}px;"
                    f" padding-right: {9 + pad}px; }}"
                    for name, pad in self._grid_padding.items()
                )
            )

    def on_library_rows_changed(self, *_args: Any) -> None:
        for scrolled_window, view in (
            (self.scrolledwindow, self.library),
            (self.hidden_scrolledwindow, self.hidden_library),
        ):
            self.update_columns(scrolled_window.get_hadjustment(), view)

    @staticmethod
    def create_card_factory() -> Gtk.SignalListItemFactory:
        def setup(_factory: Any, list_item: Gtk.ListItem) -> None:
            list_item.set_child(GameWidget())
            # Focus goes to the card's buttons, as with the old FlowBox
            list_item.set_focusable(False)
            list_item.set_activatable(False)

        def bind(_factory: Any, list_item: Gtk.ListItem) -> None:
            list_item.get_child().bind(list_item.get_item(), list_item)

        def unbind(_factory: Any, list_item: Gtk.ListItem) -> None:
            list_item.get_child().unbind()

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)
        return factory

    ARROW_KEYS = {
        Gdk.KEY_Left: (-1, 0),
        Gdk.KEY_Right: (1, 0),
        Gdk.KEY_Up: (0, -1),
        Gdk.KEY_Down: (0, 1),
    }

    def on_library_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        view: Gtk.GridView,
        model: Gio.ListModel,
    ) -> bool:
        if keyval not in self.ARROW_KEYS or state & Gtk.accelerator_get_default_mod_mask():
            return False

        card = self.get_focus()
        while card and not isinstance(card, GameWidget):
            card = card.get_parent()
        if not card or not card.list_item:
            return False

        step, rows = self.ARROW_KEYS[keyval]
        if self.get_direction() == Gtk.TextDirection.RTL:
            step = -step
        position = card.list_item.get_position() + step + rows * view.get_max_columns()
        if not 0 <= position < model.get_n_items():
            return False  # Let focus leave the grid at its edges

        view.scroll_to(position, Gtk.ListScrollFlags.NONE, None)
        if (widget := model.get_item(position).widget) is not None:
            widget.cover_button.grab_focus()
        return True

    def queue_game_update(self, game: Game) -> None:
        """Add, refresh or remove a game in the library views (batched)"""
        self._pending_games[game] = None
        if self._games_update_queued:
            return
        self._games_update_queued = True
        # Default priority: idle callbacks can be starved by redraws
        GLib.idle_add(self.flush_game_updates, priority=GLib.PRIORITY_DEFAULT)

    def flush_game_updates(self) -> bool:
        self._games_update_queued = False
        if not self._pending_games:
            return False

        games, new = list(self._pending_games), []
        self._pending_games.clear()

        for game in games:
            show = not game.removed and not game.blacklisted
            present = game in self._model_games
            if show and not present:
                new.append(game)
                self._model_games.add(game)
            elif present:
                found, position = self.games_model.find(game)
                if not found:
                    continue
                if show:
                    # Replacing the item re-runs the filters and sorters for it
                    self.games_model.splice(position, 1, [game])
                else:
                    self.games_model.remove(position)
                    self._model_games.discard(game)

        if new:
            # One insertion for the whole batch (e.g. the library at startup)
            self.games_model.splice(self.games_model.get_n_items(), 0, new)

        self.set_library_child()
        return False

    def search_changed(self, _widget: Any, hidden: bool) -> None:
        # Refresh search filter on keystroke in search box
        (self.hidden_library_filter if hidden else self.library_filter).changed(
            Gtk.FilterChange.DIFFERENT
        )
        self.set_library_child()

    def queue_set_library_child(self) -> None:
        """Coalesce several set_library_child() requests into one idle call"""
        if self._library_child_queued:
            return
        self._library_child_queued = True

        def run() -> bool:
            self._library_child_queued = False
            self.set_library_child()
            return False

        GLib.idle_add(run)

    def queue_create_source_rows(self) -> None:
        """Coalesce several create_source_rows() requests into one idle call"""
        if self._source_rows_queued:
            return
        self._source_rows_queued = True

        def run() -> bool:
            self._source_rows_queued = False
            self.create_source_rows()
            return False

        GLib.idle_add(run)

    def on_cover_launches_game_changed(self, *_args: Any) -> None:
        for widget in GameWidget.instances:
            widget.set_play_icon()

    def set_library_child(self) -> None:
        """Show the "no games" or "no results" notice when needed"""
        n_hidden = sum(game.hidden for game in self._model_games)
        n_visible = len(self._model_games) - n_hidden

        def remove_from_overlay(widget: Gtk.Widget) -> None:
            if isinstance(parent := widget.get_parent(), Gtk.Overlay):
                parent.remove_overlay(widget)

        for overlay, n_games, model, empty, no_results in (
            (
                self.library_overlay,
                n_visible,
                self.library_model,
                self.notice_empty,
                self.notice_no_results,
            ),
            (
                self.hidden_library_overlay,
                n_hidden,
                self.hidden_library_model,
                self.hidden_notice_empty,
                self.hidden_notice_no_results,
            ),
        ):
            child = None
            if not n_games:
                child = empty
            elif not model.get_n_items():
                child = no_results

            for notice in (empty, no_results):
                if notice is not child:
                    remove_from_overlay(notice)
            if child and child.get_parent() is None:
                overlay.add_overlay(child)

    def filter_func(self, game: Game, hidden: bool) -> bool:
        if game.hidden != hidden:
            return False

        text = (
            (self.hidden_search_entry if hidden else self.search_entry)
            .get_text()
            .lower()
        )

        filtered = text != "" and not (
            text in game.name.lower()
            or (text in game.developer.lower() if game.developer else False)
        )

        if not filtered:
            if self.filter_state == "all":
                pass
            elif game.base_source != self.filter_state:
                filtered = True

        game.filtered = filtered

        return not filtered

    def set_active_game(self, _widget: Any, _pspec: Any, game: Game) -> None:
        self.active_game = game

    def show_details_page(self, game: Game) -> None:
        self.active_game = game

        self.details_view_cover.set_opacity(int(not game.loading))
        self.details_view_spinner.set_visible(game.loading)

        self.details_view_developer.set_label(game.developer or "")
        self.details_view_developer.set_visible(bool(game.developer))

        icon, text = "view-conceal-symbolic", _("Hide")
        if game.hidden:
            icon, text = "view-reveal-symbolic", _("Unhide")

        self.details_view_hide_button.set_icon_name(icon)
        self.details_view_hide_button.set_tooltip_text(text)

        if self.details_view_game_cover:
            self.details_view_game_cover.remove_picture(self.details_view_cover)

        self.details_view_game_cover = game.game_cover
        self.details_view_game_cover.add_picture(self.details_view_cover)

        self.details_view_blurred_cover.set_paintable(
            self.details_view_game_cover.get_blurred()
        )

        self.details_view_title.set_label(game.name)
        self.details_page.set_title(game.name)

        date = relative_date(game.added)
        self.details_view_added.set_label(
            # The variable is the date when the game was added
            _("Added: {}").format(date)
        )
        last_played_date = (
            relative_date(game.last_played) if game.last_played else _("Never")
        )
        self.details_view_last_played.set_label(
            # The variable is the date when the game was last played
            _("Last played: {}").format(last_played_date)
        )

        if self.navigation_view.get_visible_page() != self.details_page:
            self.navigation_view.push(self.details_page)
            self.set_focus(self.details_view_play_button)

        self.set_details_view_opacity()

    def set_details_view_opacity(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() != self.details_page:
            return

        if (
            style_manager := Adw.StyleManager.get_default()
        ).get_high_contrast() or not style_manager.get_system_supports_color_schemes():
            self.details_view_blurred_cover.set_opacity(0.3)
            return

        self.details_view_blurred_cover.set_opacity(
            1 - self.details_view_game_cover.luminance[0]  # type: ignore
            if style_manager.get_dark()
            else self.details_view_game_cover.luminance[1]  # type: ignore
        )

    def sort_func(self, game1: Game, game2: Game, *_args: Any) -> int:
        var, order = "name", True

        if self.sort_state in ("newest", "oldest"):
            var, order = "added", self.sort_state == "newest"
        elif self.sort_state == "last_played":
            var = "last_played"
        elif self.sort_state == "a-z":
            order = False

        def get_value(game: Game, attr: str) -> str:
            return str(getattr(game, attr)).lower().removeprefix("the ")

        value1, value2 = get_value(game1, var), get_value(game2, var)

        if var != "name" and value1 == value2:
            var, order = "name", False
            value1, value2 = get_value(game1, var), get_value(game2, var)

        if value1 == value2:
            return 0

        return ((value1 > value2) ^ order) * 2 - 1

    def set_show_hidden(self, navigation_view: Adw.NavigationView, *_args: Any) -> None:
        self.lookup_action("show_hidden").set_enabled(
            navigation_view.get_visible_page() == self.library_page
        )

    def on_show_sidebar_action(self, *_args: Any) -> None:
        shared.state_schema.set_boolean(
            "show-sidebar", (value := not self.overlay_split_view.get_show_sidebar())
        )
        self.overlay_split_view.set_show_sidebar(value)

    def on_go_to_parent_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.details_page:
            self.navigation_view.pop()

    def on_go_home_action(self, *_args: Any) -> None:
        self.navigation_view.pop_to_page(self.library_page)

    def on_show_hidden_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.hidden_library_page:
            return

        self.navigation_view.push(self.hidden_library_page)

    def on_sort_action(self, action: Gio.SimpleAction, state: GLib.Variant) -> None:
        action.set_state(state)
        self.sort_state = str(state).strip("'")
        self.library_sorter.changed(Gtk.SorterChange.DIFFERENT)

        shared.state_schema.set_string("sort-mode", self.sort_state)

    def on_toggle_search_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.library_page:
            search_bar = self.search_bar
            search_entry = self.search_entry
        elif self.navigation_view.get_visible_page() == self.hidden_library_page:
            search_bar = self.hidden_search_bar
            search_entry = self.hidden_search_entry
        else:
            return

        search_bar.set_search_mode(not (search_mode := search_bar.get_search_mode()))

        if not search_mode:
            self.set_focus(search_entry)

        search_entry.set_text("")

    def show_details_page_search(self, widget: Gtk.Widget) -> None:
        model = (
            self.hidden_library_model
            if widget == self.hidden_search_entry
            else self.library_model
        )
        if game := model.get_item(0):
            self.show_details_page(game)

    def on_undo_action(
        self, _widget: Any, game: Optional[Game] = None, undo: Optional[str] = None
    ) -> None:
        if not game:  # If the action was activated via Ctrl + Z
            if shared.importer and (
                shared.importer.imported_game_ids or shared.importer.removed_game_ids
            ):
                shared.importer.undo_import()
                return

            try:
                game = tuple(self.toasts.keys())[-1][0]
                undo = tuple(self.toasts.keys())[-1][1]
            except IndexError:
                return

        if game:
            if undo == "hide":
                game.toggle_hidden(False)

            elif undo == "remove":
                game.removed = False
                game.save()
                game.update()

            self.toasts[(game, undo)].dismiss()
            self.toasts.pop((game, undo))

    def on_open_menu_action(self, *_args: Any) -> None:
        if self.navigation_view.get_visible_page() == self.library_page:
            self.primary_menu_button.popup()
        elif self.navigation_view.get_visible_page() == self.hidden_library_page:
            self.hidden_primary_menu_button.popup()

    def on_close_action(self, *_args: Any) -> None:
        self.close()
