"""Tests for the logic changed by the performance/robustness refactor.

Run from the repo root (needs an installed build, see tests/support.py):

    python3 -m unittest discover -s tests -t . -v
"""

# pylint: disable=missing-function-docstring,protected-access,import-outside-toplevel
import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support import TMP, iterate_main_loop

from PIL import Image  # noqa: E402  (after support sets the environment)

from cartridges import shared  # noqa: E402

shared.image_size = (200, 300)  # normally computed from the monitors


def tmp_files() -> set[Path]:
    return {p for p in TMP.rglob("*") if p.is_file()}


class FakeGame:
    """Just what Store needs from a Game, without creating GTK widgets."""

    def __init__(self, game_id: str, source: str = "steam", removed: bool = False):
        self.game_id = game_id
        self.source = source
        self.base_source = source.split("_")[0]
        self.version = shared.SPEC_VERSION
        self.removed = removed
        self.name = game_id

    def connect(self, *_args):
        return 0


class JsonFileTests(unittest.TestCase):
    def setUp(self):
        from cartridges.utils import json_file

        self.jf = json_file
        self.dir = Path(TMP / f"json-{self.id()}")
        self.dir.mkdir()

    def test_atomic_write_roundtrip_leaves_no_temp_file(self):
        path = self.dir / "game.json"
        self.jf.write_json_atomic(path, {"name": "A"}, indent=4)
        self.assertEqual(self.jf.read_json(path), {"name": "A"})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["game.json"])

    def test_failed_write_keeps_previous_file(self):
        path = self.dir / "game.json"
        self.jf.write_json_atomic(path, {"name": "old"})
        with self.assertRaises(TypeError):
            self.jf.write_json_atomic(path, {"bad": object()})
        self.assertEqual(self.jf.read_json(path), {"name": "old"})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["game.json"])

    def test_is_game_file_ignores_temp_and_other_files(self):
        self.assertTrue(self.jf.is_game_file(Path("steam_1.json")))
        self.assertFalse(self.jf.is_game_file(Path(".steam_1.json.abc.tmp")))
        self.assertFalse(self.jf.is_game_file(Path(".hidden.json")))
        self.assertFalse(self.jf.is_game_file(Path("notes.txt")))

    def test_is_valid_game_data(self):
        good = {"game_id": "x", "source": "s", "name": "n", "executable": "e"}
        self.assertTrue(self.jf.is_valid_game_data(good))
        self.assertTrue(self.jf.is_valid_game_data({**good, "executable": ["a", "b"]}))
        self.assertFalse(self.jf.is_valid_game_data([good]))
        self.assertFalse(self.jf.is_valid_game_data(None))
        for key in good:
            bad = dict(good)
            del bad[key]
            self.assertFalse(self.jf.is_valid_game_data(bad), key)
        self.assertFalse(self.jf.is_valid_game_data({**good, "name": 3}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        from cartridges.store.store import Store

        self.store = Store()
        shared.win = SimpleNamespace(toasts={})

    def tearDown(self):
        shared.win = None

    def test_lookup_len_iter_contains(self):
        games = [FakeGame(f"steam_{i}") for i in range(50)] + [
            FakeGame("imported_1", "imported")
        ]
        for game in games:
            self.store.add_game(game, {}, run_pipeline=False)
        self.assertEqual(len(self.store), 51)
        self.assertIs(self.store["steam_7"], games[7])
        self.assertIs(self.store.get("imported_1"), games[-1])
        self.assertIsNone(self.store.get("nope"))
        with self.assertRaises(KeyError):
            _ = self.store["nope"]
        self.assertEqual({g.game_id for g in self.store}, {g.game_id for g in games})

    def test_duplicate_is_ignored(self):
        first = FakeGame("steam_1")
        self.store.add_game(first, {}, run_pipeline=False)
        self.store.add_game(FakeGame("steam_1"), {}, run_pipeline=False)
        self.assertIs(self.store["steam_1"], first)
        self.assertEqual(len(self.store), 1)
        self.assertEqual(self.store.duplicate_game_ids, {"steam_1"})

    def test_replacing_a_removed_game(self):
        old = FakeGame("steam_1", removed=True)
        # A removed game loaded from disk is cleaned up and not stored
        self.store.add_game(old, {}, run_pipeline=False)
        self.assertEqual(len(self.store), 0)

        # A removed game already in the store gets replaced, stored once
        stored = FakeGame("steam_2")
        self.store.add_game(stored, {}, run_pipeline=False)
        stored.removed = True
        new = FakeGame("steam_2")
        self.store.add_game(new, {}, run_pipeline=False)
        self.assertIs(self.store["steam_2"], new)
        self.assertEqual(len(self.store), 1)

    def test_lookup_is_constant_time(self):
        """Regression: get() used to scan every game (quadratic startup)."""
        for i in range(20000):
            self.store.add_game(FakeGame(f"steam_{i}"), {}, run_pipeline=False)
        start = time.perf_counter()
        for i in range(20000):
            self.store.get(f"steam_{i}")
        self.assertLess(time.perf_counter() - start, 0.5)


class LoadGamesFromDiskTests(unittest.TestCase):
    def test_invalid_files_are_skipped_not_fatal(self):
        from cartridges import main

        games_dir = shared.games_dir
        games_dir.mkdir(parents=True, exist_ok=True)
        valid = {"game_id": "imported_1", "source": "imported", "name": "Ok",
                 "executable": "true"}
        (games_dir / "imported_1.json").write_text(json.dumps(valid))
        (games_dir / "corrupt.json").write_text("{not json")
        (games_dir / "list.json").write_text("[1, 2]")
        (games_dir / "missing.json").write_text(json.dumps({"name": "x"}))
        (games_dir / "binary.json").write_bytes(b"\xff\xfe\x00garbage")
        (games_dir / ".imported_2.json.x.tmp").write_text("{}")

        added = []
        orig_game, orig_store = main.Game, shared.store
        main.Game = lambda data: data
        shared.store = SimpleNamespace(add_game=lambda g, _d: added.append(g))
        try:
            main.CartridgesApplication.load_games_from_disk(None)
        finally:
            main.Game, shared.store = orig_game, orig_store

        self.assertEqual([g["game_id"] for g in added], ["imported_1"])
        self.assertEqual(added[0]["added"], 0)  # defaulted when missing


class CoverConversionTests(unittest.TestCase):
    def setUp(self):
        from cartridges.utils import save_cover

        self.sc = save_cover
        self.src = TMP / "src"
        self.src.mkdir(exist_ok=True)

    def test_convert_cover_leaves_only_the_result(self):
        source = self.src / "cover.png"
        Image.new("RGB", (600, 900), (10, 20, 30)).save(source)
        before = tmp_files()
        result = self.sc.convert_cover(source)
        self.assertEqual(tmp_files() - before, {result})
        with Image.open(result) as image:
            self.assertEqual(image.size, shared.image_size)
        self.sc.remove_tmp_cover(result, source)
        self.assertEqual(tmp_files(), before)
        self.assertTrue(source.is_file())

    def test_convert_from_pixbuf_removes_intermediate(self):
        from gi.repository import GdkPixbuf

        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 300, 450)
        pixbuf.fill(0x336699FF)
        before = tmp_files()
        result = self.sc.convert_cover(pixbuf=pixbuf)
        self.assertEqual(tmp_files() - before, {result})
        self.sc.remove_tmp_cover(result)
        self.assertEqual(tmp_files(), before)

    def test_supported_original_is_never_removed(self):
        source = self.src / "keep.png"
        Image.new("RGB", (60, 90)).save(source)
        result = self.sc.convert_cover(source, resize=False)
        self.assertEqual(result, source)
        self.sc.remove_tmp_cover(result, source)
        self.assertTrue(source.is_file())

    def test_save_cover_updates_widgets_on_main_thread(self):
        calls = []
        cover = SimpleNamespace(
            new_cover=lambda path: calls.append((threading.current_thread(), path))
        )
        shared.win = SimpleNamespace(game_covers={"steam_9": cover})
        try:
            source = self.src / "thread.tiff"
            Image.new("RGB", (200, 300)).save(source)
            worker = threading.Thread(target=self.sc.save_cover,
                                      args=("steam_9", source))
            worker.start()
            worker.join()
            self.assertEqual(calls, [])  # deferred to the main loop
            iterate_main_loop(until=lambda: bool(calls), timeout=2)
        finally:
            shared.win = None
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], threading.main_thread())
        self.assertEqual(calls[0][1], shared.covers_dir / "steam_9.tiff")
        self.assertTrue((shared.covers_dir / "steam_9.tiff").is_file())


class FakePicture:
    def __init__(self):
        self.paintables = []

    def set_paintable(self, paintable):
        self.paintables.append(paintable)

    def queue_draw(self):
        pass

    def is_visible(self):
        return True


class GameCoverTests(unittest.TestCase):
    def setUp(self):
        from cartridges import game_cover

        self.gc = game_cover
        self.dir = TMP / "covers-test"
        self.dir.mkdir(exist_ok=True)

    @staticmethod
    def wait_for(condition):
        iterate_main_loop(until=condition, timeout=5)
        iterate_main_loop(timeout=0.2)  # let any straggler land

    def test_async_load_shows_placeholder_then_texture(self):
        path = self.dir / "a.tiff"
        Image.new("RGB", (20, 30)).save(path)
        picture = FakePicture()
        self.gc.GameCover({picture}, path)
        self.assertIs(picture.paintables[0], self.gc.GameCover.placeholder)
        self.wait_for(lambda: len(picture.paintables) > 1)
        self.assertEqual(picture.paintables[-1].get_width(), 20)

    def test_stale_result_does_not_override_newer_cover(self):
        first, second = self.dir / "first.tiff", self.dir / "second.tiff"
        Image.new("RGB", (11, 11)).save(first)
        Image.new("RGB", (22, 22)).save(second)
        picture = FakePicture()
        cover = self.gc.GameCover({picture}, first)
        cover.new_cover(second)
        self.wait_for(lambda: picture.paintables[-1].get_width() == 22)
        self.assertEqual(picture.paintables[-1].get_width(), 22)
        self.assertEqual(cover.get_texture().get_width(), 22)

    def test_get_texture_is_synchronous(self):
        path = self.dir / "sync.tiff"
        Image.new("RGB", (33, 44)).save(path)
        cover = self.gc.GameCover({FakePicture()}, path)
        self.assertEqual(cover.get_texture().get_height(), 44)

    def test_corrupt_cover_falls_back_to_placeholder(self):
        path = self.dir / "corrupt.tiff"
        path.write_bytes(b"not an image")
        picture = FakePicture()
        cover = self.gc.GameCover({picture}, path)
        self.wait_for(lambda: len(picture.paintables) > 1)
        self.assertIsNone(cover.get_texture())
        self.assertIs(picture.paintables[-1], self.gc.GameCover.placeholder)

    def test_single_animation_timer(self):
        """Regression: each add_picture() started one more timer chain."""
        path = self.dir / "anim.gif"
        frames = [Image.new("RGB", (20, 30), c) for c in ("red", "blue")]
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=100, loop=0)
        cover = self.gc.GameCover({FakePicture()}, path)
        ticks = []
        original = cover.update_animation
        cover.update_animation = lambda data: (ticks.append(1), original(data))[1]
        for _ in range(5):
            cover.add_picture(FakePicture())
        ticks.clear()
        iterate_main_loop(timeout=0.55)
        cover.animation = None  # stop the chain
        # ~5 frames at 100 ms; with 6 parallel chains it was ~30
        self.assertLessEqual(len(ticks), 8)


if __name__ == "__main__":
    unittest.main()
