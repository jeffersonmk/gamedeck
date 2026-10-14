#!/usr/bin/env python3
"""Reproducible, headless performance benchmark for GameDeck (Cartridges).

Runs an installed build (``meson install`` into a prefix) against a synthetic,
isolated library: fake HOME/XDG dirs, in-memory GSettings and the GTK Broadway
backend, so the user's real data, settings and display are never touched.

Usage (from the repo root, after `meson install -C _build`):

    /usr/bin/python3 tests/bench/bench.py --prefix _install --games 1000 \
        --import-games 300 --runs 3 --out before.json

Metrics (all wall-clock, page cache warm — a true cold start needs
`echo 3 > /proc/sys/vm/drop_caches` as root, see README in this folder):

* import_s          time to import the application modules
* activate_s        duration of Application.do_activate (main loop blocked)
* ready_s           process start -> first idle after the window is presented
* search_ms         synchronous re-filter of the library for one keystroke
* sort_ms           synchronous re-sort of the library (a-z)
* rss_mb_*          resident memory after startup / after the import
* import_total_s    time for a Steam import of N fake games with local covers
* import_max_gap_ms longest main-loop stall during the import (UI freeze)
* import_gaps_50ms  number of main-loop stalls longer than 50 ms
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def make_library(root: Path, n_games: int, n_import: int) -> None:
    from PIL import Image  # system Pillow (the app depends on it anyway)

    marker = root / f".ready-{n_games}-{n_import}"
    if marker.exists():
        return
    shutil.rmtree(root, ignore_errors=True)

    data = root / "data" / "cartridges"
    games, covers = data / "games", data / "covers"
    games.mkdir(parents=True)
    covers.mkdir(parents=True)

    for i in range(n_games):
        game_id = f"imported_{i}"
        (games / f"{game_id}.json").write_text(
            json.dumps(
                {
                    "added": 1_700_000_000 + i,
                    "executable": f"echo game {i}",
                    "game_id": game_id,
                    "source": "imported",
                    "hidden": i % 10 == 0,
                    "last_played": (1_700_000_000 + i * 7) if i % 3 else 0,
                    "name": f"Game {i:05d} {'The Quest' if i % 2 else 'Adventure'}",
                    "developer": f"Studio {i % 37}",
                    "removed": False,
                    "blacklisted": False,
                    "version": 1.5,
                }
            )
        )
        if i % 5:  # 80% of the games have a cover
            color = ((i * 37) % 256, (i * 91) % 256, (i * 53) % 256)
            Image.new("RGB", (200, 300), color).save(
                covers / f"{game_id}.tiff", compression="webp"
            )

    # Fake Steam install for the import benchmark
    steam = root / "home" / ".steam" / "steam"
    apps = steam / "steamapps"
    cache = steam / "appcache" / "librarycache"
    apps.mkdir(parents=True)
    cache.mkdir(parents=True)
    (apps / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{steam}"\n\t}}\n}}\n'
    )
    for i in range(n_import):
        appid = str(100000 + i)
        (apps / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"Steam Game {i}"\n'
            f'\t"StateFlags"\t\t"4"\n}}\n'
        )
        (cache / appid).mkdir()
        color = ((i * 13) % 256, (i * 29) % 256, (i * 71) % 256)
        Image.new("RGB", (600, 900), color).save(
            cache / appid / "library_600x900.jpg", quality=85
        )

    (root / "home" / ".config").mkdir(parents=True, exist_ok=True)
    marker.touch()


def child(args: argparse.Namespace) -> None:
    """Runs inside the isolated environment; prints one JSON line."""
    t0 = time.perf_counter()
    prefix = Path(args.prefix).resolve()
    pkgdatadir = prefix / "share" / "cartridges"
    site = next((prefix / "lib").glob("python3*/site-packages"))
    sys.path.insert(0, str(site))
    sys.path.insert(1, str(pkgdatadir))

    import gettext

    gettext.install("cartridges", names=["ngettext"])

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gio, GLib, Gtk

    Gio.Resource.load(str(pkgdatadir / "cartridges.gresource"))._register()

    from cartridges import main as app_main  # noqa: E402
    from cartridges import shared  # noqa: E402

    t_imported = time.perf_counter()

    # Never touch the network in benchmarks
    from cartridges.utils import steam as steam_utils

    steam_utils.SteamAPIHelper.get_api_data = lambda self, appid: {
        "developer": "Bench"
    }

    for key in ("lutris", "heroic", "bottles", "flatpak", "desktop", "itch",
                "legendary", "retroarch", "sgdb"):
        shared.schema.set_boolean(key, False)
    shared.schema.set_boolean("steam", True)
    shared.schema.set_boolean("remove-missing", False)

    result: dict = {"import_s": t_imported - t0}
    app_cls = app_main.CartridgesApplication
    orig_activate = app_cls.do_activate

    def rss_mb() -> float:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
        return 0.0

    gaps: list[float] = []
    last_tick = [time.perf_counter()]

    def tick() -> bool:
        now = time.perf_counter()
        gaps.append((now - last_tick[0]) * 1000)
        last_tick[0] = now
        return True

    def finish_import(app, t_start: float) -> bool:
        if app.state != shared.AppState.DEFAULT:
            return True
        result["import_total_s"] = time.perf_counter() - t_start
        if stalls := result.pop("_stalls", None):
            for stack, n in stalls.most_common(12):
                sys.stderr.write(f"STALL {n * 10:5d}ms {stack}\n")
        if prof := result.pop("_prof", None):
            import pstats

            prof.disable()
            pstats.Stats(prof, stream=sys.stderr).sort_stats("tottime").print_stats(30)
        result["import_max_gap_ms"] = max(gaps) if gaps else 0.0
        result["import_gaps_50ms"] = sum(g > 50 for g in gaps)
        result["imported_games"] = len(shared.store) - args.games
        result["rss_mb_after_import"] = rss_mb()
        print("BENCH " + json.dumps(result), flush=True)
        app.quit()
        return False

    def after_ready(app) -> bool:
        win = shared.win

        # Works with both the old FlowBox library and the GridView one
        grid = hasattr(win, "library_model")

        def refilter() -> None:
            if grid:
                win.library_filter.changed(Gtk.FilterChange.DIFFERENT)
            else:
                win.library.invalidate_filter()

        # Search: one keystroke worth of synchronous filtering
        win.search_entry.set_text("quest")
        prof = None
        if os.environ.get("BENCH_PROFILE_SEARCH"):
            import cProfile

            prof = cProfile.Profile()
            prof.enable()
        ts = time.perf_counter()
        refilter()
        result["search_ms"] = (time.perf_counter() - ts) * 1000
        if prof:
            import pstats

            prof.disable()
            with open(os.environ["BENCH_PROFILE_SEARCH"], "w") as out:
                pstats.Stats(prof, stream=out).sort_stats("tottime").print_stats(15)
        win.search_entry.set_text("")
        refilter()

        # Sort
        win.sort_state = "a-z"
        ts = time.perf_counter()
        if grid:
            win.library_sorter.changed(Gtk.SorterChange.DIFFERENT)
        else:
            win.library.invalidate_sort()
        result["sort_ms"] = (time.perf_counter() - ts) * 1000

        result["rss_mb_startup"] = rss_mb()
        result["games_loaded"] = len(shared.store)

        # Sanity check: every visible game must be in the grid, otherwise a
        # broken build would show suspiciously good numbers
        def n_cards(flowbox) -> int:
            n = 0
            while flowbox.get_child_at_index(n):
                n += 1
            return n

        shown = sum(not (g.removed or g.blacklisted) for g in shared.store)
        if grid:
            result["cards"] = (win.library_model.get_n_items()
                               + win.hidden_library_model.get_n_items())
            # Cards actually created (only the visible ones are)
            from cartridges.game_widget import GameWidget

            result["card_widgets"] = sum(
                w.game is not None for w in GameWidget.instances)
            result["window"] = [win.get_width(), win.get_height()]
        else:
            result["cards"] = n_cards(win.library) + n_cards(win.hidden_library)
            result["card_widgets"] = result["cards"]
        if result["cards"] != shown:
            raise SystemExit(f"Only {result['cards']} of {shown} games have a card")

        if args.import_games:
            gaps.clear()
            last_tick[0] = time.perf_counter()
            GLib.timeout_add(5, tick)
            if os.environ.get("BENCH_STALLS"):
                import threading
                import traceback
                from collections import Counter

                main_id = threading.main_thread().ident
                stalls: Counter = Counter()
                result["_stalls"] = stalls

                def sampler() -> None:
                    while "_stalls" in result:
                        time.sleep(0.01)
                        if time.perf_counter() - last_tick[0] > 0.1:
                            frame = sys._current_frames().get(main_id)
                            stack = traceback.extract_stack(frame)[-6:]
                            stalls[" <- ".join(
                                f"{Path(f.filename).name}:{f.lineno}:{f.name}"
                                for f in reversed(stack))] += 1

                threading.Thread(target=sampler, daemon=True).start()
            if os.environ.get("BENCH_PROFILE_IMPORT"):
                import cProfile

                result["_prof"] = cProfile.Profile()
                result["_prof"].enable()
            t_start = time.perf_counter()
            app.on_import_action()
            GLib.timeout_add(20, finish_import, app, t_start)
        else:
            print("BENCH " + json.dumps(result), flush=True)
            app.quit()
        return False

    class BenchApp(app_cls):
        def do_activate(self):
            prof = None
            if os.environ.get("BENCH_PROFILE"):
                import cProfile

                prof = cProfile.Profile()
                prof.enable()
            ts = time.perf_counter()
            orig_activate(self)
            result["activate_s"] = time.perf_counter() - ts
            if prof:
                import pstats

                prof.disable()
                pstats.Stats(prof, stream=sys.stderr).sort_stats(
                    "cumulative").print_stats(35)

            # "ready" = main loop free again after the window was presented
            # (Broadway does not paint without a connected client, so
            # frame-clock signals are not reliable here)
            def on_ready():
                result["ready_s"] = time.perf_counter() - t0
                GLib.timeout_add(10, wait_covers)
                return False

            # "covers_ready" = every cover texture decoded and shown
            def wait_covers():
                covers = shared.win.game_covers.values()
                # Covers on screen (all of them with the old FlowBox)
                if all(c.texture is not None or c.animation or not c.path
                       or not c.pictures for c in covers):
                    result["covers_ready_s"] = time.perf_counter() - t0
                    GLib.timeout_add(100, after_ready, self)
                    return False
                return True

            GLib.timeout_add(0, on_ready)

    app = BenchApp()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    app.run([sys.argv[0]])


def parent(args: argparse.Namespace) -> None:
    work = Path(args.workdir).resolve()
    make_library(work, args.games, args.import_games)
    prefix = Path(args.prefix).resolve()

    display = ":7"
    broadway = subprocess.Popen(
        ["gtk4-broadwayd", display],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    runs = []
    try:
        for n in range(args.runs):
            # Fresh copy of the library so imports always start from the same state
            run_dir = work / f"run{n}"
            shutil.rmtree(run_dir, ignore_errors=True)
            shutil.copytree(work / "data", run_dir / "data")
            shutil.copytree(work / "home", run_dir / "home", symlinks=True)
            (run_dir / "tmp").mkdir()
            env = {
                **os.environ,
                "HOME": str(run_dir / "home"),
                "XDG_DATA_HOME": str(run_dir / "data"),
                "XDG_CONFIG_HOME": str(run_dir / "home" / ".config"),
                "XDG_CACHE_HOME": str(run_dir / "cache"),
                "TMPDIR": str(run_dir / "tmp"),
                "GSETTINGS_BACKEND": "memory",
                "GSETTINGS_SCHEMA_DIR": str(prefix / "share" / "glib-2.0" / "schemas"),
                "GDK_BACKEND": "broadway",
                "BROADWAY_DISPLAY": display,
                "GTK_A11Y": "none",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            }
            cmd = [sys.executable, __file__, "--child", "--prefix", str(prefix),
                   "--games", str(args.games), "--import-games",
                   str(args.import_games)]
            out = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                 timeout=900, check=False)
            line = next((l for l in out.stdout.splitlines() if l.startswith("BENCH ")),
                        None)
            if not line:
                sys.stderr.write(out.stdout[-3000:] + out.stderr[-6000:])
                raise SystemExit(f"run {n} failed (exit {out.returncode})")
            runs.append(json.loads(line[6:]))
            # Temporary files the app left behind (never cleaned up = leak)
            runs[-1]["tmp_files_left"] = sum(
                1 for p in (run_dir / "tmp").rglob("*") if p.is_file()
            )
            print(f"run {n}: {runs[-1]}", flush=True)
    finally:
        broadway.terminate()

    summary = {
        k: statistics.median(r[k] for r in runs)
        for k in runs[0]
        if isinstance(runs[0][k], (int, float))
    }
    summary["runs"] = len(runs)
    summary["games"] = args.games
    summary["import_games_requested"] = args.import_games
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "runs": runs},
                                             indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--prefix", default="_install")
    p.add_argument("--games", type=int, default=1000)
    p.add_argument("--import-games", type=int, default=200)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--workdir", default=os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "gamedeck-bench"))
    p.add_argument("--out")
    p.add_argument("--child", action="store_true")
    a = p.parse_args()
    child(a) if a.child else parent(a)
