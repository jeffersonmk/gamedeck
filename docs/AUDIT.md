# GameDeck — Audit, technology evaluation and refactoring plan

> Audited base: `main` at `d56323e` (identical to `kra-mo/cartridges` `main`:
> 0 commits ahead, 0 behind). Date: 2026-10-13.
> Measurements: `tests/bench/bench.py` (see `tests/bench/README.md`), release
> build, Python 3.14, GTK 4.24, Libadwaita 1.10, CachyOS, 12 CPUs.

## 1. Original architecture

**Stack:** Python 3 + PyGObject, GTK 4 ≥ 4.15, Libadwaita ≥ 1.6, UI written in
Blueprint (`blueprint-compiler` v0.16 subproject), Meson build, Python
dependencies `requests`, `Pillow`, `PyYAML`. Distribution: Flatpak (GNOME 49),
Windows (Inno Setup/MSYS2), macOS (PyInstaller). ~6.9k lines of Python,
42 translations.

**Components**

| Component | Responsibility |
|---|---|
| `main.py` (`CartridgesApplication`) | lifecycle, actions, loading the library from disk, CLI search/launch |
| `window.py` | `Gtk.FlowBox` with **one widget per game**, filtering/sorting, sidebar |
| `game.py` | `Game` = **model and widget at the same time** (`Gtk.Box` with a template) |
| `game_cover.py` | texture, animated GIF, blurred version of the cover |
| `store/` | `Store` (game index) + a per-game `Pipeline` of *managers* |
| `store/managers/` | Display (UI), File (JSON), Cover, SteamAPI, SGDB |
| `importer/` | 9 sources (Steam, Lutris, Heroic, Bottles, itch, Legendary, RetroArch, Flatpak, Desktop) run in `Gio.Task` |
| `utils/` | SteamGridDB, Steam API, rate limiter, cover conversion, sqlite, launching |
| `search-provider/` | GNOME Shell search provider (separate process) |

**Startup flow:** `do_activate` → reads every `games/*.json` → for each one it
creates the `Game` widget (GTK template), decodes the cover and appends it to
the `FlowBox` — all **synchronously on the main thread**.

## 2. `main` vs `rewrite` (upstream)

`rewrite` is **still Python** (not Rust). It is a rewrite versioned 49.0,
~3k lines.

| | `main` | `rewrite` |
|---|---|---|
| Model vs widget | `Game` is a `Gtk.Box` | `Game` is a plain `GObject`; widgets are separate |
| Grid | `FlowBox` (one widget per game, not virtualized) | `Gtk.GridView` (virtualized) |
| Concurrency | `Gio.Task` + threads | `asyncio` integrated with GLib (`asyncio.to_thread`) |
| Sources | 9 | 6 (Steam, Lutris, Heroic, itch, Legendary, Desktop) + "Imported" |
| Missing in `rewrite` | — | Bottles, RetroArch, Flatpak, **SteamGridDB**, preferences dialog, removal of missing games, GNOME Shell search provider, Windows/macOS packaging, **all translations** (`po/` only has `LINGUAS`/`POTFILES`) |
| New in `rewrite` | — | Collections, gamepad support (libmanette), `--filesystem=host:ro` |
| Status | stable | incomplete; last commit April 2026; upstream declared the project **no longer actively maintained** |

**Conclusion:** `rewrite` gets the architecture right (separate model +
`GridView`) but is far from feature parity. Switching to it today would drop
sources, SGDB, preferences and translations. The recommended strategy is to
**bring `rewrite`'s ideas into `main` incrementally**, not to switch branches.

## 3. Findings

Legend: **[C]** proven by code, **[M]** proven by measurement,
**[H]** hypothesis not yet verified.

| # | Problem | Evidence | User impact | Status |
|---|---|---|---|---|
| 1 | `Store.get()` scanned every game on each lookup → O(n²) library load | [C] `store.py`; [M] 6 million iterations for 2000 games | slow startup with large libraries | **fixed** |
| 2 | `filter_func` called `set_library_child()` (which scans the library) **for every game** → O(n²) search | [C] `window.py`; [M] 795 ms per keystroke with 2000 games | search freezes the UI | **fixed** |
| 3 | `CoverManager` was *blocking*: download (5 s timeout, up to 3 retries with `sleep(3)`) and image re-encoding **on the main thread** | [C] `manager.py: blocking=True`; [M] 2.6 s freezes during import | UI freezes while importing; up to ~30 s per game on a bad network | **fixed** |
| 4 | `create_source_rows()` (scans every game) called on every game update | [C] `display_manager.py` | O(n²) when many games are updated | **fixed** (coalesced) |
| 5 | Temporary file leak: 2 per imported cover, never deleted | [C] `Gio.File.new_tmp` without removal; [M] 600 files in `/tmp` for 300 games | `/tmp` (tmpfs = RAM) grows on every import | **fixed** |
| 6 | `save_cover` touched widgets from worker threads (SGDB already ran in a thread) | [C] `sgdb_manager.py` → `save_cover` → `new_cover` | GTK is not thread-safe: possible random crashes | **fixed** |
| 7 | First GIF frame set from a worker thread | [C] `game_cover.py: run_in_thread(update_animation)` | same | **fixed** |
| 8 | Each `add_picture()` (opening the details of a game with a GIF cover) started **one more** timer chain that never stopped | [C]; [M] regression test | animation speeds up and CPU use grows over time | **fixed** |
| 9 | JSON writes not closed and not atomic | [C] `json.dump(..., path.open("w"))` | a crash or full disk corrupts the game file | **fixed** (atomic write) |
| 10 | One invalid JSON file (bad UTF-8, a list, missing key) aborted loading | [C] `except` only covered `JSONDecodeError`; no validation before `Game()` | library does not open | **fixed** |
| 11 | A corrupt cover prevented the game from being shown | [C] `Gdk.Texture.new_from_filename` without `try` inside `DisplayManager` | game disappears from the grid | **fixed** |
| 12 | One `schema.changed` connection **per game**, never disconnected | [C] `game.py` | removed games stay alive in memory; O(n) cost on every settings change | **fixed** (single handler) |
| 13 | 2 DEBUG log lines per game written to the log file on every startup | [M] 0.22 s / 2000 games | startup | **fixed** (one summary line) |
| 14 | `Game` widgets are **created in import threads** | [C] sources instantiate `Game` (a `Gtk.Box`) inside `Gio.Task.run_in_thread` | violates GTK threading rules | **fixed** in step 2 ([M] 50 → 0 widgets created off the main thread) |
| 15 | Non-virtualized grid: one widget + one texture per game | [M] 682 MB RSS with 2000 games; ~1.3 s initial layout | memory and startup scale linearly | **open** — `GridView` (roadmap) |
| 16 | Flatpak uses `--filesystem=host` (read **and write**) | [C] manifest; `rewrite` already uses `host:ro` | more permission than needed | **open** — recommendation, not tested here |
| 17 | Games are launched with `flatpak-spawn --host sh -c` | [C] `run_executable.py` | the sandbox is bypassed by design (required to start host games); commands come from the user's local files | accepted (documented) |
| 18 | `set_texture` calls `discard(<generator>)`, which removes nothing | [C] `game_cover.py` | pruning of invisible pictures never worked | **unchanged** — "fixing" it would change behavior without visual testing |
| 19 | `retryable_on` uses Python's builtin `ConnectionError` and compares exact types | [C] | `requests` connection errors are not retried | [H] real impact not measured |
| 20 | A cover chosen in the details dialog leaves 1 temporary file | [C] `details_dialog.py` | minor, once per user action | open (low) |
| 21 | The Blueprint build target outputs `.`, so ninja never recompiles `.ui` files after a `.blp` edit | [C] `data/meson.build`; [M] stale `game.ui` after editing `game.blp` | developers test a stale UI | open — workaround: `rm -rf _build` and set up again |
| 22 | Heroic logs 4 ERROR lines + tracebacks when a store simply has no installed games | [M] real import on this machine | noisy logs, looks like a failure | open (low) |

**Security/packaging:** no secrets in the code; the SteamGridDB key is stored
in GSettings (`sgdb-key`). `.desktop` entries are passed through `shlex.quote`.
The Flatpak CI uses the `gnome-47` image while the manifest targets runtime 49 [C].

## 4. Technology evaluation

| Criterion | A. Refactored Python | B. Rust (progressive migration) | C. Hybrid Python + Rust |
|---|---|---|---|
| Startup/responsiveness | Measured bottlenecks were **algorithmic and threading issues**, not the language: fixed in Python (-45% startup, -98% search) | Small extra gain while the grid is not virtualized | Same as A for the UI |
| RAM | Dominated by GTK textures and widgets (C), not Python objects. `GridView` fixes it in any language | No interpreter (estimated saving of a few tens of MB, not measured) | Worse: Python + Rust runtime |
| Large libraries | With O(1) index and `GridView`, Python cost per game is µs | Better in theory; irrelevant below tens of thousands of games | — |
| Search/metadata | 13 ms for 2000 games after the fix | Faster, but already below one frame | Does not justify an FFI bridge |
| GTK/Libadwaita/GNOME | PyGObject: first-class support, Blueprint works | gtk4-rs/libadwaita-rs are mature; Blueprint templates work | Two build chains (Meson + Cargo) |
| Maintenance | Current codebase, same language as upstream and `rewrite` | Rewrite of ~7k lines + 9 integrations | Higher complexity (PyO3/GObject introspection, packaging) |
| Testing/debugging | Simple; 18 new tests run without a display | Good (cargo test), but everything must be rewritten first | Two worlds to debug |
| Integrations (Steam, Lutris, Heroic…) | Already exist and work | Rewrite VDF/SQLite/JSON/YAML parsers | No component has a CPU cost that justifies it |
| Flatpak | Same as today | Smaller package (no Python), but the GNOME runtime ships Python anyway | Needs the Rust SDK + Python modules |
| Cost/risk | Low, incremental | High: months, regressions, losing translations/sources along the way | Medium-high with no measured benefit |

**Recommendation: A — stay on Python and refactor progressively towards the
`rewrite` architecture (model separate from widget + `Gtk.GridView`).**

Rationale: every measured bottleneck had an algorithmic cause or came from
misuse of the main thread, and all were fixed in Python. What remains (memory
and initial layout) comes from one widget + one texture per game — a **UI
architecture** problem that would be identical in Rust. No component showed a
Python CPU cost that justifies FFI. Rust should only be reconsidered if,
**after** the `GridView` migration, a measurement shows a CPU bottleneck in
Python code (e.g. parsing huge Steam libraries).

## 5. Prioritized plan

| Step | Change | Risk | Done when |
|---|---|---|---|
| 1 ✅ | Low-risk fixes (items 1–13) | low | tests pass; no time regression in the benchmark; 0 temp files left |
| 2 ✅ | Split `Game` (plain `GObject` model) from `GameWidget`; sources create models only | medium | no widget created off the main thread (checkable with a debug `assert`); all 9 sources import |
| 3 | Replace `FlowBox` with `Gtk.GridView` + `Gtk.FilterListModel`/`SortListModel` | medium | RSS with 2000 games < 300 MB; `covers_ready_s` < 1 s; keyboard navigation and screen reader support preserved |
| 4 | Size-bounded texture cache (LRU by bytes) loaded on demand by the `GridView` | low | stable memory over prolonged use (open/close details 1000×) |
| 5 | Flatpak: `--filesystem=host` → `host:ro`; CI on the GNOME 49 image | low | Flatpak build + import from every source + changing a cover all work |
| 6 | Evaluate Rust **only** for a measured bottleneck | — | a profile showing > 100 ms of Python CPU on a hot path |

**Stays in Python:** everything. **Rust candidates:** none with a measurable
justification today.

**Regression mitigation:** each step in a separate commit/branch; automated
tests for the logic; before/after benchmark; manual test on the real display
with an isolated library.

## 6. Measured results (step 1)

Median of 5 runs, same synthetic library, release build.

**Large library — 2000 games (1600 with a cover), importing 300 Steam games**

| Metric | Before | After | Change |
|---|---:|---:|---:|
| `do_activate` (UI blocked) | 3.78 s | 1.96 s | **-48%** |
| Window ready | 4.00 s | 2.19 s | **-45%** |
| All covers visible | 5.29 s | 3.49 s | **-34%** |
| Search (1 keystroke) | 795 ms | 13 ms | **-98%** |
| Sort | 53 ms | 38 ms | -30% |
| Total import | 4.70 s | 1.85 s | **-61%** |
| Longest freeze during import | 2608 ms | 483 ms | **-81%** |
| Freezes > 50 ms during import | 4 | 9 | worse (shorter and spread out) |
| RSS after startup | 682 MB | 682 MB | = |
| RSS after import | 769 MB | 798 MB | +4% |
| Temp files left behind | 600 | 0 | **-100%** |

**Small library — 100 games, importing 50**

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Window ready | 0.365 s | 0.360 s | = |
| Search | 3.4 ms | 0.8 ms | -77% |
| Total import | 0.78 s | 0.42 s | -46% |
| Longest freeze | 621 ms | 29 ms | **-95%** |
| RSS after import | 146 MB | 177 MB | **+21%** (see limitations) |
| Temp files left behind | 100 | 0 | -100% |

## 6b. Step 2 — model/widget split

`Game` is now a plain `GObject.Object` (data + actions). The library card is
`GameWidget` (`cartridges/game_widget.py`, template `data/gtk/game.blp`),
created lazily by `DisplayManager` on the main thread and kept in
`game.widget`. Window code reads the game through `child.get_child().game`.
The card's menu popover is only rebuilt when the menu actually changes
(hidden ↔ visible) instead of on every update. Importers were not changed.

**Verification**

| Check | Step 1 | Step 2 |
|---|---:|---:|
| Widgets created off the main thread, import of 50 synthetic Steam games | 50 | **0** |
| Widgets created off the main thread, real import on the dev machine (Steam, Heroic, Flatpak, Desktop) | 25 | **0** |
| Real import: games, names and commands per source | 7 Steam + 9 Desktop | identical |
| Cards in the grid (benchmark, 2000 games) | 2000 | 2000 |
| Hide → hidden page → unhide, card menu, search, details (real display) | — | 0 errors |

Performance is unchanged within noise (median of 3 runs, 2000 games: window
ready 2.30 s → 2.34 s, all covers 3.59 s → 3.66 s, RSS 681 → 683 MB). This step
is a prerequisite for step 3 (`GridView`), which is where the memory and
startup gains are expected.

Lutris, Bottles, itch, Legendary and RetroArch are not installed on the dev
machine; their importer code is unchanged and they only depend on the `Game`
constructor, which is covered by unit tests (including creation in a worker
thread).

## 7. Known limitations

* **+25–30 MB RSS after importing**: covers are now processed in parallel in
  worker threads. With `MALLOC_ARENA_MAX=2` the difference is halved, i.e.
  most of it is glibc per-thread *arenas* (not a leak). Limiting concurrency
  to 4 was tested and did **not** reduce memory, so it was not kept.
* More short freezes (> 50 ms) when importing 300 games: the work went from a
  single 2.6 s block to several smaller chunks (GIL contention + widget
  creation). Steps 2 and 3 address this.
* Items 15–22 of the findings table remain open.
* Not measured here: cold start (needs root), Flatpak (needs the GNOME 49 SDK
  and flatpak-builder) and real GPU rendering (the benchmark uses Broadway).
  Procedures in `tests/bench/README.md`.
