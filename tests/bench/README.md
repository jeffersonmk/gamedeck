# GameDeck benchmark

`bench.py` measures an **installed** build (via `meson install`) against a
synthetic library in an isolated environment: fake HOME/XDG/TMPDIR, in-memory
GSettings and GTK's headless Broadway backend. Your games, settings and
display are never touched. Network access is disabled (the Steam API call is
replaced with a stub).

## Running

```sh
# 1. Install the build into a local prefix (release profile)
meson setup _build --prefix="$PWD/_install" -Dprofile=release
meson install -C _build

# 2. Small and large library (median of N runs)
python3 tests/bench/bench.py --prefix _install --games 100  --import-games 50  --runs 5 --out small.json
python3 tests/bench/bench.py --prefix _install --games 2000 --import-games 300 --runs 5 --out large.json
```

To compare with another version, install it into a different prefix (e.g. a
`git worktree` of the previous version) and run the same command with
`--prefix` pointing at it. Keep the same `--workdir` so both runs use the same
synthetic library.

## Metrics

| Metric | Meaning |
|---|---|
| `activate_s` | Duration of `do_activate` (main thread blocked loading the library) |
| `ready_s` | From process start until the main loop is free after presenting the window |
| `covers_ready_s` | Until every cover is decoded and applied (includes the first layout) |
| `search_ms` / `sort_ms` | Synchronous re-filter/re-sort of the library (one keystroke in search) |
| `rss_mb_*` | Resident memory after startup / after the import |
| `import_total_s` | Steam import of N games with local 600×900 covers |
| `import_max_gap_ms` | Longest main-loop stall during the import (UI frozen) |
| `import_gaps_50ms` | Number of stalls longer than 50 ms during the import |
| `tmp_files_left` | Temporary files left behind (leak) |

Diagnostic variables: `BENCH_PROFILE=1` (cProfile of `do_activate`),
`BENCH_STALLS=1` (samples the main thread's stack whenever it stalls > 100 ms
during the import), `BENCH_PROFILE_IMPORT=1` (cProfile of the import; on
Python ≥ 3.12 it mixes threads, use with care).

## Limitations

* **Cold start**: numbers are taken with a warm kernel page cache. To measure
  a cold start, run `sync; echo 3 > /proc/sys/vm/drop_caches` as root before
  each run (affects the whole system).
* **Broadway ≠ Wayland**: Broadway does not paint without a connected client,
  so real (GPU) rendering cost is not included, and layout after scrolling
  may not happen at all. Set `BENCH_BROWSER` to a headless Chromium to
  connect one (needed for `--scroll`):
  ```sh
  BENCH_BROWSER=/path/to/chrome-headless-shell python3 tests/bench/bench.py \
      --games 2000 --import-games 300 --scroll
  ```
  `--scroll` browses the whole library a page at a time (`--scroll-passes`
  times) and then opens `--details` details pages, reporting memory and the
  number of decoded covers kept.
* **Flatpak**: not measured here. Manual procedure:
  ```sh
  flatpak install --user flathub org.gnome.Sdk//49 org.flatpak.Builder
  flatpak run org.flatpak.Builder --user --install --force-clean \
      .flatpak-build build-aux/flatpak/page.kramo.Cartridges.Devel.json
  flatpak run page.kramo.Cartridges.Devel
  ```
