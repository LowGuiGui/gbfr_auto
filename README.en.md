[简体中文](README.md) | English

# GBFR Auto

[![Project Status: Suspended – Initial development has started, but there has not yet been a stable, usable release; work has been stopped for the time being but the author(s) intend on resuming work.](https://www.repostatus.org/badges/latest/suspended.svg)](https://www.repostatus.org/#suspended)

> [!IMPORTANT]
> **Windows development was paused on 2026-10-02, and this repository is archived read-only.**
>
> - The project is moving to Linux; development continues in [LowGuiGui/gbfr-auto-linux](https://github.com/LowGuiGui/gbfr-auto-linux).
> - The Windows version may be resumed at any time; see [Resuming development](#resuming-development).
> - **It does not currently work**, so no downloadable release is published. If you want to pick it up, build it yourself as described in [Building the executables](#building-the-executables).

> [!WARNING]
> **AI-written code.** The changes this fork makes to upstream were written by an AI coding assistant (Anthropic's Claude Code) at the owner's direction, and have **not been reviewed line by line by a human**.
>
> - Only the items marked ✅ under [Feature status](#feature-status) have run on real hardware; everything else has automated tests only, or has never run.
> - The code injects a DLL into the game process and changes how the game perceives window focus. Review it yourself before running it, and use it at your own risk.
>
> Details: [About the AI-written code](#about-the-ai-written-code).

An automation script for Granblue Fantasy: Relink that farms battles in a loop, using image recognition plus simulated keyboard and mouse input.

This repository is a fork of [zhiyual/gbfr_auto](https://github.com/zhiyual/gbfr_auto). This file is a translation of the [Chinese README](README.md), which is canonical.

## Feature status

The real state at the pause (2026-10-02). Legend:

- ✅ ran on the owner's real machine
- 🧪 automated tests only
- ⚠️ built, never run
- ⏳ not done
- 🐞 known defect

| Feature | Status | Evidence |
|---|---|---|
| Template-based page detection, battle loop, re-running from the results screen (upstream's feature) | 🧪 | The owner has not re-run the full loop on real hardware since this fork's changes; page detection and dispatch are unit-tested |
| Diagnostic probe `gbfr-probe.exe` | ✅ | Ran several times on the owner's machine, 2026-08-24 to 08-26; every measured finding below comes from it |
| Background capture (PrintWindow) | ✅ | Returns real frames in both windowed and borderless modes |
| Virtual Xbox controller (ViGEmBus) | ✅ focused only | Connects on the first attempt and moves the character while the game has focus; the game pauses itself once unfocused |
| Focus spoof: the game keeps running when unfocused ([#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)) | ✅ probe only | Two runs on 2026-08-26 (probe section 9): with it on, the game kept running unfocused, and both keyboard and a physical controller still drove it. **The app never turns it on** |
| Fix for reading replies over the injection channel ([#65](https://github.com/LowGuiGui/gbfr_auto/pull/65)) | ✅ | The two runs above used the fixed channel |
| Keyboard/mouse and virtual-controller modes, automatic recovery, F12 panic key ([#66](https://github.com/LowGuiGui/gbfr_auto/pull/66)) | ⚠️ | Automated tests pass; never run against the game (tests G1–G3 not done); the controller button mapping is a guess |
| Config file, log file, diagnostics (score logging, anomaly frames, dry run) | 🧪 | Unit tests |
| Template upgrades never overwrite templates the user edited | 🧪 | Unit tests |
| Non-zero exit when elevation fails (#1); inject watchdog race (#2) | 🧪 | Unit tests |
| Middle-click lands on the client-area centre ([#46](https://github.com/LowGuiGui/gbfr_auto/issues/46), first half) | 🧪 | The calculation is unit-tested; the real-machine check was not done |
| App turns the focus spoof on in controller mode | ⏳ | Not started; it is the missing piece for unattended background farming |
| Item counts read from the save file ([#48](https://github.com/LowGuiGui/gbfr_auto/issues/48)) | ⏳ | Save confirmed readable and written during play; the parser is not written |
| Multi-resolution template matching ([#12](https://github.com/LowGuiGui/gbfr_auto/issues/12)) | ⏳ | Not started |
| Leaving the target window empty ("full screen") sends no input at all | 🐞 | Inherited from upstream's dev-branch refactor: the input layer requires a target window |
| Capture includes the title bar and borders ([#46](https://github.com/LowGuiGui/gbfr_auto/issues/46), second half) | 🐞 | Measured capture is 1942×1136 against a 1920×1080 client area |
| Only system-DPI-aware ([#47](https://github.com/LowGuiGui/gbfr_auto/issues/47)) | 🐞 | Coordinates go wrong across monitors with different scaling |
| Inject mode still sends system-wide keyboard and mouse events | 🐞 | They land in whichever window has focus and move the real cursor; injection only saves the focus-stealing step |
| While the game is unfocused, the virtual controller also drives other programs ([#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)) | 🐞 | Most likely Steam Input's Desktop Layout; the test that would confirm it (F2) was not done |
| With the focus spoof on in keyboard/mouse mode, the game locks the cursor to its window centre | 🐞 | Measured 2026-08-26; that is why the spoof is restricted to controller mode |

## Features

- **Page detection by image recognition**: OpenCV template matching identifies the current screen (battle, results, rewards, pause, …)
- **Fully automatic battle loop**: on the battle screen it holds W to move forward and holds the middle mouse button, and keeps fighting
- **Results handling**: after a battle it switches to "again" and confirms
- **Global hotkeys**: F1 / F2 / F12 work even when the window is unfocused
- **Overlay**: shows the running state in the top-left corner of the screen
- **Live log**: the window shows actions and the battle count, also written to a log file
- **Two input modes** (⚠️ not verified on real hardware): keyboard and mouse, or a virtual Xbox controller
- **Config file**: `gbfr_auto.toml`

## Prerequisites

- Set the game language to Simplified Chinese, and keep the default key and lock-on bindings
- Select the game window under "目标窗口" (target window). **Do not leave it empty**: an empty target captures the whole screen but sends no input
- Make sure the game resolution matches the template images, or detection may fail
- The program asks for administrator rights when it starts

## Hotkeys

| Key | Action |
|---|---|
| **F1** | Start the loop |
| **F2** | Stop the loop |
| **F12** | Panic stop: release every input, turn the focus spoof off, and stop the loop (⚠️ not verified on real hardware) |

## Requirements

- Windows 10 / 11 (the owner tested Windows 11 26200)
- Python 3.12+ (numpy 2.5.2 requires 3.12 or later; CI uses 3.13)

## Installing dependencies

```bash
pip install -r requirements.txt
```

On Windows this also installs pywin32. Packaging, test and lint dependencies are in `requirements-dev.txt`.

## Configuration

On first run, a commented `gbfr_auto.toml` is created next to the program. Save it as UTF-8; saving it as "ANSI" in Notepad turns the Chinese comments into garbage. Common settings:

| Setting | Effect |
|---|---|
| `[input] backend` | `"kmb"` for keyboard and mouse, or `"pad"` for the virtual controller |
| `[input] mode` | Keyboard/mouse transport: `"fallback"` (steals focus) or `"inject"` (injected DLL) |
| `[input] dry_run` | `true` detects and logs as usual but sends no input |
| `[detect] log_scores` / `save_anomaly_frames` | Log every template's match score / save a screenshot whenever no page matches; for debugging detection |
| `[pad]` | Controller button mapping. **The defaults are guesses** |

## Diagnostic probe

`gbfr-probe.exe` checks the machine in one pass. Without flags it is read-only: it does not change the game, the save or any settings. It checks:

- whether the game window is borderless, bordered or exclusive fullscreen, and its border offsets
- the process's DPI awareness and the monitor layout
- whether background capture returns real frames (the captured image is saved for you to check)
- where the save file is, how big it is and when it was written
- which input-related modules the game has loaded

**Start the game first**, then run it. The results go to `gbfr-probe-report.txt` next to the exe, and the capture to `gbfr-probe-capture.png`. Run it first whenever detection misbehaves.

| Flag | Effect |
|---|---|
| `--title "keyword"` | Use when the window title does not match. If no window is found, it lists every visible window title |
| `--gamepad-test` | Actually push the virtual controller's stick (sends input to the game) |
| `--xinput-test` | Measure whether Windows zeroes XInput while unfocused |
| `--focus-test` | Measure whether the game pauses when unfocused, or keeps running and ignores input |
| `--focus-hook-test` | **Inject the hook DLL into the game** and test the focus spoof (asks first) |

The parts that read the game process must run as administrator, because a game launched through Reloaded-II runs elevated. Restart the game before injecting: the DLL cannot be unloaded, and an old copy left in the process silently ignores commands from a newer build.

The report is in English: on non-Chinese Windows the console code page (cp437/cp1252) cannot encode Chinese, and printing it raises an exception. The report is written line by line, so even if the probe fails part-way, everything found so far plus the traceback stays in the file; paste the whole file.

## Template images

On first run, the bundled templates are extracted into a `template/` folder next to the executable. If your resolution does not match the templates, replace the images in that folder.

When you upgrade:

- templates you have not edited are updated automatically;
- templates you edited are **never overwritten**; the new bundled version is saved beside yours as a `.new` file for you to swap in if you want;
- `template/.bundled.json` records which files have been edited. Do not edit or delete it.

## Building the hook DLL (inject mode)

Inject mode needs `hook/gbfr_hook.dll`, built from `hook/gbfr_hook.c`. The DLL is **not version-controlled** (a binary cannot be diffed or reviewed), so build it once before running from source:

```bash
cd hook
build.bat
```

`build.bat` picks Visual Studio Build Tools (`cl`) or MinGW (`gcc`), whichever is available. You can also cross-compile it on Linux with MinGW:

```bash
x86_64-w64-mingw32-gcc -shared -O2 -o hook/gbfr_hook.dll hook/gbfr_hook.c -luser32
```

Without the DLL the program still runs; only enabling inject mode reports that `gbfr_hook.dll` is missing. This project has **no release**. CI build artifacts include a compiled DLL, but they are kept for 90 days only.

## Usage

1. Start the game and go to a screen where battles can be repeated
2. Run the script:

```bash
python main.py
```

3. Select the game window under "目标窗口", then choose the input mode and transport
4. Press **F1** to start the loop; a green notice in the top-left corner confirms it started
5. Press **F2** to stop; the log shows how many battles were completed. **F12** is the panic stop

## Building the executables

PyInstaller is not a cross-compiler, so this only works on Windows. The verified, complete recipe is the CI workflow [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml): run it manually in your own fork (workflow_dispatch) to get both executables and the DLL. To build by hand:

1. Install the dependencies (including PyInstaller):

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

2. Build the app:

```bash
pyinstaller --onefile --windowed --name "GBFR Auto" --uac-admin --icon "icon.ico" --add-data "template;template" --add-data "icon.ico;." main.py
```

3. Put the compiled DLL next to the executable, at `dist/hook/gbfr_hook.dll`. It must sit beside the exe, not inside it.
4. Build the probe (optional). First take `vgamepad/win/vigem/client/x64/ViGEmClient.dll` from the [vgamepad 0.1.0](https://pypi.org/project/vgamepad/0.1.0/) source archive (MIT licence) and put it in `vigem_bin/`, then:

```bash
pyinstaller --noconfirm --onefile --console --name "gbfr-probe" --icon "icon.ico" --add-data "vigem_bin;vigem_bin" --paths . tools/windows_probe.py
```

`--paths .` is required: the script lives in `tools/`, but the modules it imports are at the repository root.

The virtual controller also needs the ViGEmBus driver, which is not bundled. Install the official [ViGEmBus 1.22.0](https://github.com/ViGEm/ViGEmBus/releases/tag/v1.22.0); it is the final release, and the project was archived in 2023 ([#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)).

## Project structure

```
gbfr_auto/
├── main.py               # app: UI, hotkeys, main loop, page tables and dispatch
├── pages.py              # page-detection rule tree (pure functions)
├── option.py             # action layer: move, battle, again, confirm, plus reconciliation
├── backend.py            # input backends: keyboard/mouse / virtual controller / none
├── supervisor.py         # reconciliation rules (pure): window lost, game restarted, physical controller in use, …
├── window_capture.py     # window capture: PrintWindow, falling back to a foreground screenshot
├── window_input.py       # keyboard/mouse transports: fallback (pynput) / inject (hook DLL + named pipe)
├── opencv.py             # template matching and blank-frame detection
├── geometry.py           # window rect and client-area arithmetic
├── framediff.py          # frame differencing (used by the probe)
├── config.py             # the gbfr_auto.toml config file
├── applog.py             # logging
├── vigem.py              # virtual Xbox controller (ViGEmClient)
├── xinput.py             # reading XInput controller state
├── procinfo.py           # processes and loaded modules
├── hook/                 # source of the injected DLL (gbfr_hook.c) and the injector
├── tools/windows_probe.py  # the gbfr-probe.exe diagnostic
├── tests/                # tests that run on Linux (the Windows boundary is stubbed in tests/conftest.py)
└── template/             # template images
```

## Page detection

Every 3 seconds (`loop.poll_interval_ms`) it captures a frame and checks the rules below, top to bottom:

| Page | Matched by | Action (keyboard/mouse mode) |
|---|---|---|
| Battle (BATTLE) | `flag_battle` | Hold W, and hold the middle mouse button at the client-area centre |
| Results, "again" selected (REWARD_AGAIN) | `flag_battleresult` + `flag_again` | Press `a` (`keys.confirm`) to confirm |
| Results, "exit" selected (REWARD_EXIT) | `flag_battleresult` + `flag_exit` | Press `3` (`keys.again`) to switch to "again" |
| Results (SCORE) | `flag_battleresult` only | Press `a` |
| Pause (PAUSE) | `flag_continue` | Press `a` to continue |
| Other (UNKNOWN) | nothing matches | Press `a` to advance, at most 5 times in a row (`loop.max_blind_taps`), then stop and warn |

- Upstream's README says "press Enter"; the key actually pressed has always been `a`.
- The tool re-selects "again" on every results screen instead of using the game's built-in repeat (capped at 10 runs), so that cap does not apply.
- In controller mode: move is the left stick forward, battle is a right-stick click, again is Y, confirm is A. **All of these mappings are unverified guesses.**

## In progress at the pause

In the planned order:

1. **F2**: check whether Steam Input's Desktop Layout causes [#53](https://github.com/LowGuiGui/gbfr_auto/issues/53). It is a reversible test: unplug the physical controller, then set the Desktop Layout's left-stick binding to "None". Do **not** press "Disable Steam Input" on the Desktop Layout; users report being unable to re-enable it.
2. **G1**: verify the controller button mapping ([#66](https://github.com/LowGuiGui/gbfr_auto/pull/66)).
3. **G2 / G3**: automatic recovery (window dragged, mode switched, game restarted, …), and yielding when you pick up a physical controller (#66).
4. Have the app inject the DLL and turn the focus spoof on in controller mode: the missing piece of background farming ([#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)).
5. Per-monitor DPI awareness ([#47](https://github.com/LowGuiGui/gbfr_auto/issues/47), together with matching probe output), then capture the client area only ([#46](https://github.com/LowGuiGui/gbfr_auto/issues/46)).
6. Multi-resolution matching ([#12](https://github.com/LowGuiGui/gbfr_auto/issues/12)). First collect real match scores with `log_scores`, `save_anomaly_frames` and `dry_run` on, then tune from that data.
7. A status panel docked beside the game window.
8. Item counts from the save file ([#48](https://github.com/LowGuiGui/gbfr_auto/issues/48)). Read-only: copy first, then parse; never write to the save.

Still unanswered: whether the game throttles its frame rate in the background. Two measurements disagree (153% and 15%), and neither controlled the scene.

## Measured environment

All from the owner's machine (2026-08-24 to 08-26):

- Windows 11 26200; GBFR 2.0.4; Simplified Chinese UI; windowed
- primary display 3840×2160 at 150% scaling
- the game is launched through Reloaded-II and runs elevated
- window borders: left 11, top 45, right 11, bottom 11 (the same at 1440p and 1080p)
- PrintWindow returns real frames in both windowed and borderless modes
- the save `%LOCALAPPDATA%\GBFR\Saved\SaveGames\SaveData1.dat` is about 22 MB and is also written during play
- ViGEmBus 1.22.0 is installed and works
- the game pauses itself when it loses focus (an anti-AFK measure added in 1.1; three independent lines of evidence). Windows itself does not block XInput while unfocused, but that finding comes from a single run

## Resuming development

1. In the repository's Settings → Danger Zone, click "Unarchive this repository", or run `gh repo unarchive LowGuiGui/gbfr_auto`.
2. Old CI records and build artifacts will have expired (public repositories keep them for at most 90 days). Rebuild on dev first with `gh workflow run build-windows.yml --ref dev`. Download artifacts with `gh run download <RUN_ID> -D <dir>`; without `-D`, gh 2.46 reports a bogus path-traversal error.
3. Read the pinned status issue and [In progress at the pause](#in-progress-at-the-pause) first.
4. Restart the game before every injection. If an old DLL is still in the process, the probe report shows `window proc subclassed=no`.
5. Development conventions:
   - One branch per change, merged into dev through a PR. dev is protected: `ruff`, `pytest (ubuntu)` and `build (windows-latest)` must all pass.
   - Tests run on Linux (`pytest`); the Windows boundary is stubbed in `tests/conftest.py`.
   - CI runs the same suite again on Windows against the real win32 modules.

## About the AI-written code

- **Tool**: Anthropic's Claude Code (Claude Opus 5 / 5.5 models), working at the owner's direction.
- **Scope**:
  - The changes this fork made to upstream, starting 2026-08-23.
  - Every non-merge commit in this fork carries a `Co-Authored-By: Claude` trailer, except the 3 initial setup commits of 2026-08-23 (ruff and pre-commit config, pinned requirements, restored LICENSE).
  - Upstream's original code is not covered.
- **Review**: **no human line-by-line review.** The owner set the direction and ran the real-machine tests; what was tested on real hardware is marked ✅ under [Feature status](#feature-status).
- **Verification**:
  - 558 automated tests on Linux; CI runs the same suite again on Windows against the real win32 modules.
  - The tests do not cover real Win32 calls, injection and the named pipe, the Tk UI, or anything that needs the game itself.
- **Risk**: the code injects a DLL into the game process and changes how the game perceives window focus. GPL-2.0 provides no warranty (sections 11 and 12).
- **Copyright**: whether AI-generated output is protected by copyright is legally unsettled. To the extent it is, this fork's changes are licensed under GPL-2.0; see [COPYRIGHT](COPYRIGHT). AI output may also resemble its training data.
- **Please do not** submit this code to projects that ban AI-generated contributions (for example Gentoo, NetBSD, QEMU).
- This README was drafted by the same AI; the owner reviewed it before publishing.

## Notes

- This tool is for learning purposes only; do not use it commercially
- Make sure the game resolution matches the template images, or detection may fail
- Test on an easy quest first, and confirm detection and input behave correctly before leaving it running for long

## Licence

GPL-2.0; the full text is in [LICENSE](LICENSE). Upstream's work belongs to its author; the copyright statement for this fork's changes is in [COPYRIGHT](COPYRIGHT).
