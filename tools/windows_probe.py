# -*- coding: utf-8 -*-
"""Windows 现场探测。在装了游戏的机器上跑一次，把报告贴回来。

PLANNING.md §5 列的问题只能在 Windows 上、开着游戏才能回答。这个脚本一次性把
它们全测了。

    gbfr-probe.exe                 只读，不发任何输入
    gbfr-probe.exe --gamepad-test  额外做虚拟手柄测试（会向游戏发输入）
    gbfr-probe.exe --title "关键字" 窗口标题对不上时指定

只读。除了往当前文件夹写一份报告和一张截图之外，什么都不改。

—— 输出为什么是英文 ——
控制台编码。英文版 Windows 的控制台代码页是 cp437/cp1252，`print()` 一个中文字
符直接抛 UnicodeEncodeError；而原来的报错处理本身也打中文，于是报错的时候又炸
一次，异常逃出 except 和 finally，进程静默退出 —— 表现就是"打了几行就没了，
什么都没留下"。用 ASCII 输出，这一整类故障就不存在了。代码注释保持中文。
"""

import argparse
import ctypes
import os
import sys
import time
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 模块级导入而不是用到时再导：vigem 只依赖标准库，任何平台都能导入，放在函数里
# 会让 PyInstaller 的静态分析更容易漏掉它。
#
# 但这里必须兜住 ImportError。模块级导入失败发生在 main() 存在之前，任何 try 都
# 来不及 —— 打包漏了一个模块，用户看到的就是一屏 PyInstaller 的堆栈然后窗口关闭。
# 真出这种事的时候，能出一份报告远比崩得干脆有用。
try:
    import vigem  # noqa: E402
    _VIGEM_ERROR = None
except BaseException as _e:  # noqa: BLE001 - 任何导入期故障都要活下来
    vigem = None
    _VIGEM_ERROR = f"{type(_e).__name__}: {_e}"

DEFAULT_TITLE = "Granblue"
REPORT_NAME = "gbfr-probe-report.txt"
CAPTURE_NAME = "gbfr-probe-capture.png"
RELINK_APP_ID = "1090670"

OUT_DIR = None
_report = None


# ---------------------------------------------------------------------------
# 输出：任何一步都不允许因为编码而失败
# ---------------------------------------------------------------------------

def harden_stdout():
    """把 stdout/stderr 切到 UTF-8 且遇到无法编码的字符就替换而不是抛异常。

    输出已经全是 ASCII，这里是双保险 —— 万一将来有人加了一行非 ASCII 的文字，
    也不该把整个工具带崩。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def output_dir():
    """报告写到哪。

    双击 exe 时"当前文件夹"就是 exe 所在目录，所以首选那里 —— 从别处用命令行
    启动时 cwd 可能是任意位置，写到那里等于文件失踪。只读位置依次退回 cwd 和
    临时目录。
    """
    if getattr(sys, "frozen", False):
        preferred = os.path.dirname(os.path.abspath(sys.executable))
    else:
        preferred = os.getcwd()

    import tempfile
    for candidate in (preferred, os.getcwd(), tempfile.gettempdir()):
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".gbfr_probe_write_test")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return candidate
        except OSError:
            continue
    return None


def open_report():
    """在任何探测开始之前就把报告文件打开。

    utf-8-sig 而不是 utf-8：带 BOM 的话 Windows 记事本才会正确显示，不带就是乱码。
    """
    global OUT_DIR, _report
    OUT_DIR = output_dir()
    if OUT_DIR is None:
        print("WARNING: no writable directory found; screen output only.")
        return None
    path = os.path.join(OUT_DIR, REPORT_NAME)
    try:
        _report = open(path, "w", encoding="utf-8-sig")
    except OSError as e:
        print(f"WARNING: cannot write report to {path}: {e}")
        _report = None
        return None
    return path


def say(line=""):
    """先落盘，再打印。

    顺序是刻意的：文件是 UTF-8，永远写得进去；控制台才是会出问题的那一端。
    先写文件，就算 print 炸了，内容也已经保住了。而 print 本身再包一层，
    保证 say() 在任何情况下都不抛异常 —— 报错处理器自己不能是故障源。
    """
    if _report is not None:
        try:
            _report.write(line + "\n")
            _report.flush()
        except (OSError, UnicodeError):
            pass
    try:
        print(line)
    except (UnicodeError, OSError):
        try:
            print(line.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def section(title):
    say()
    say("=" * 68)
    say(title)
    say("=" * 68)


# ---------------------------------------------------------------------------
# 0. 运行环境本身
# ---------------------------------------------------------------------------

def probe_self():
    section("0. Probe environment")
    frozen = getattr(sys, "frozen", False)
    say(f"  probe build     : {'frozen exe' if frozen else 'source'}")
    say(f"  python          : {sys.version.split()[0]}")
    say(f"  executable      : {sys.executable}")
    say(f"  cwd             : {os.getcwd()}")
    say(f"  report dir      : {OUT_DIR}")
    # 控制台编码是上一次故障的直接原因，明确记下来
    say(f"  stdout encoding : {getattr(sys.stdout, 'encoding', '?')}")
    try:
        say(f"  console cp      : in={ctypes.windll.kernel32.GetConsoleCP()} "
            f"out={ctypes.windll.kernel32.GetConsoleOutputCP()}")
    except Exception as e:
        say(f"  console cp      : unavailable ({e})")
    try:
        say(f"  admin           : {bool(ctypes.windll.shell32.IsUserAnAdmin())}")
    except Exception:
        say("  admin           : unknown")
    say(f"  os              : {sys.getwindowsversion()}"
        if hasattr(sys, "getwindowsversion") else "  os              : ?")


def probe_imports():
    """先确认依赖导得进来。

    打包的 exe 里 pywin32 是最可能出问题的一环（它要 pywintypes313.dll），
    与其让它在某个探测中途炸掉，不如一开始就逐个点名。
    """
    section("0b. Imports")
    ok = True
    if _VIGEM_ERROR:
        ok = False
        say(f"  [FAIL] {'vigem':<14} {_VIGEM_ERROR}   <-- not bundled")
    else:
        say(f"  [ ok ] {'vigem':<14} {getattr(vigem, '__file__', '?')}")

    # 只列这个探测器真正会用到的东西。列多了会误报：PyInstaller 不会打包没人
    # import 的模块，于是一个用不上的名字就变成一条假的 [FAIL]。
    # （win32process 就是这么被误列进来的 —— 只有 hook/injector.py 用它，
    #   而探测器根本不碰注入那条路。）
    for name in ("win32gui", "win32api", "win32con",
                 "PIL", "numpy", "cv2", "pyautogui", "opencv", "window_capture"):
        try:
            module = __import__(name)
            where = getattr(module, "__file__", "(builtin)")
            say(f"  [ ok ] {name:<14} {where}")
        except BaseException as e:
            ok = False
            say(f"  [FAIL] {name:<14} {type(e).__name__}: {e}")
    if not ok:
        say()
        say("  >> A failed import means the packaged build is missing something.")
        say("     Sections that need it will be skipped rather than crash.")
    return ok


# ---------------------------------------------------------------------------
# 1. 窗口
# ---------------------------------------------------------------------------

GWL_STYLE = -16
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000


def list_visible_windows(win32gui):
    titles = []

    def _all(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd)
            if text.strip():
                titles.append((hwnd, text))
        return True

    win32gui.EnumWindows(_all, None)
    return titles


def probe_window(title_substring):
    section("1. Game window")
    import win32api
    import win32con
    import win32gui

    windows = list_visible_windows(win32gui)
    matches = [(h, t) for h, t in windows if title_substring.lower() in t.lower()]

    if not matches:
        say(f"  No visible window title contains {title_substring!r}.")
        say()
        # 双击运行的 exe 没法方便地传参数，所以直接把所有标题列出来 —— 中文
        # 客户端、改过标题、或者装了 Reloaded-II 之类的加载器，答案就在这份
        # 列表里，不用再跑一趟。
        say(f"  All {len(windows)} visible window titles:")
        for hwnd, text in windows[:60]:
            say(f"    {hwnd:<10} {text!r}")
        if len(windows) > 60:
            say(f"    ... and {len(windows) - 60} more")
        say()
        say("  If the game is not listed, it is not running (or has no visible window).")
        say('  If it is listed under another name:  gbfr-probe.exe --title "part of it"')
        return None

    for hwnd, text in matches:
        say(f"  hwnd={hwnd}  title={text!r}")
    hwnd, _title = matches[0]

    style = win32api.GetWindowLong(hwnd, GWL_STYLE)
    win_rect = win32gui.GetWindowRect(hwnd)
    cli_rect = win32gui.GetClientRect(hwnd)
    cli_origin = win32gui.ClientToScreen(hwnd, (0, 0))

    say()
    say(f"  GetWindowRect : {win_rect}  size {win_rect[2]-win_rect[0]}x{win_rect[3]-win_rect[1]}")
    say(f"  GetClientRect : {cli_rect}  size {cli_rect[2]}x{cli_rect[3]}")
    say(f"  client origin : {cli_origin}")
    say(f"  style=0x{style & 0xFFFFFFFF:08X}"
        f"  caption={bool(style & WS_CAPTION)}"
        f"  thickframe={bool(style & WS_THICKFRAME)}"
        f"  popup={bool(style & WS_POPUP)}")

    border_x = cli_origin[0] - win_rect[0]
    border_y = cli_origin[1] - win_rect[1]
    say(f"  border offset : x={border_x} y={border_y}")
    if border_x or border_y:
        say("  >> Non-zero border. option._get_center() derives the centre from the")
        say("     WINDOW rect, so it is off by exactly this much in windowed mode.")

    if not (style & WS_CAPTION):
        say("  >> No caption: borderless window or exclusive fullscreen.")
        say("     Exclusive fullscreen draws over any overlay (feature 3) and")
        say("     usually defeats PrintWindow capture.")
    else:
        say("  >> Has a caption: ordinary windowed mode.")

    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    info = win32api.GetMonitorInfo(monitor)
    say(f"  monitor       : {info.get('Device')}  work={info.get('Work')}")
    say(f"  is primary    : {bool(info.get('Flags'))}")
    return hwnd


# ---------------------------------------------------------------------------
# 2. DPI
# ---------------------------------------------------------------------------

def probe_dpi(hwnd):
    section("2. DPI and monitor layout")
    user32 = ctypes.windll.user32
    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
        say(f"  process DPI awareness : {awareness.value}  (0=unaware 1=system 2=per-monitor)")
        if awareness.value == 0:
            say("  >> Unaware: coordinates and capture sizes will be wrong when")
            say("     monitors run at different scaling.")
    except Exception as e:
        say(f"  GetProcessDpiAwareness failed: {e}")

    if hwnd:
        try:
            say(f"  window DPI            : {user32.GetDpiForWindow(hwnd)}  (96 = 100%)")
        except Exception as e:
            say(f"  GetDpiForWindow failed: {e}")

    say(f"  virtual desktop       : origin=({user32.GetSystemMetrics(76)},"
        f"{user32.GetSystemMetrics(77)}) "
        f"size={user32.GetSystemMetrics(78)}x{user32.GetSystemMetrics(79)}")
    say("  >> A negative origin means a monitor sits left of / above the primary,")
    say("     which is where pyautogui region capture goes wrong.")


# ---------------------------------------------------------------------------
# 3. 截图后端
# ---------------------------------------------------------------------------

def probe_capture(hwnd):
    section("3. Capture backend  --  the key question for feature 1")
    from opencv import is_blank_frame
    from window_capture import _capture_printwindow

    say("  [PrintWindow] background capture, does not steal focus -- what we want")
    try:
        img = _capture_printwindow(hwnd)
    except BaseException as e:
        say(f"    raised {type(e).__name__}: {e}")
        say("    >> Needs Windows.Graphics.Capture (WGC) instead.")
        return None

    if img is None:
        say("    returned None -- failed.")
        say("    >> Needs Windows.Graphics.Capture (WGC) instead.")
        return None

    blank = is_blank_frame(img)
    path = os.path.join(OUT_DIR, CAPTURE_NAME) if OUT_DIR else None
    if path:
        try:
            img.save(path)
        except (OSError, ValueError) as e:
            say(f"    could not save capture: {e}")
            path = None
    say(f"    returned {img.size[0]}x{img.size[1]}" + (f", saved to {path}" if path else ""))
    say(f"    blank-frame verdict: {blank}")
    if blank:
        say("    >> All black: PrintWindow cannot reach this D3D window. Needs WGC.")
    else:
        say("    >> Real content. PrintWindow is enough; feature 1 does not need WGC.")
        say("       Please open the PNG and confirm it is the game, not a grey box.")
    return img


# ---------------------------------------------------------------------------
# 4. 存档
# ---------------------------------------------------------------------------

def steam_roots():
    """找出 Steam 安装目录。

    注册表是唯一可靠来源 —— 装在 D: 或自定义位置时猜路径必然落空。
    """
    roots = []
    try:
        import winreg
        for hive, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    path = winreg.QueryValueEx(handle, value)[0]
                if path:
                    roots.append((os.path.normpath(path), f"registry {value}"))
            except OSError:
                continue
    except ImportError:
        pass

    for guess in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Steam"),
        os.path.expandvars(r"%ProgramFiles%\Steam"),
        r"C:\Steam",
        os.path.expandvars(r"%USERPROFILE%\Steam"),
    ):
        roots.append((guess, "common path"))

    seen = set()
    unique = []
    for path, source in roots:
        key = path.lower()
        if key not in seen:
            seen.add(key)
            unique.append((path, source))
    return unique


def save_locations():
    """所有可能放存档的地方，按可信度排序。

    第一版只找了 Steam userdata/<id>/1090670/remote，结果在真机上一个都没找到。
    游戏真正写盘的位置是 %LOCALAPPDATA%\\GBFR\\Saved\\SaveGames —— Steam 云那
    几份是同步出来的副本。对功能 3B 来说本地那份才是关键：它的 mtime 才是"每打完
    一关有没有落盘"的真实信号。
    """
    places = []
    local = os.path.expandvars(r"%LOCALAPPDATA%\GBFR\Saved\SaveGames")
    places.append((local, "local save (this is where the game writes)"))

    for root, source in steam_roots():
        userdata = os.path.join(root, "userdata")
        if not os.path.isdir(userdata):
            continue
        try:
            account_ids = os.listdir(userdata)
        except OSError:
            continue
        for account in account_ids:
            places.append((
                os.path.join(userdata, account, "881020", "ac",
                             "WinAppDataLocal", "GBFR", "Saved", "SaveGames"),
                f"Steam cloud mirror ({source})",
            ))
            places.append((
                os.path.join(userdata, account, RELINK_APP_ID, "remote"),
                f"Steam cloud remote ({source})",
            ))
    return places


def describe_save(path):
    import struct
    size = os.path.getsize(path)
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    say(f"    {os.path.basename(path)}   {size} bytes   last written {mtime:%Y-%m-%d %H:%M:%S}")
    try:
        with open(path, "rb") as f:
            head = f.read(52)
    except OSError as e:
        say(f"      could not read: {e}")
        return
    if len(head) >= 12:
        main_ver, steam_id = struct.unpack_from("<iQ", head, 0)
        # 合理性检查：SteamID64 都是 7656119... 开头
        plausible = 76561197960265728 <= steam_id <= 76561202255233023
        say(f"      main_version={main_ver}  steam_id={steam_id}"
            f"  {'(SaveGameFile header, readable)' if plausible else '(header looks unusual)'}")


def probe_save():
    section("4. Save file  --  the key question for feature 3B")

    found = []
    for path, source in save_locations():
        if not os.path.isdir(path):
            continue
        try:
            names = sorted(os.listdir(path))
        except OSError as e:
            say(f"  [{path}] could not list: {e}")
            continue
        if not names:
            continue
        say(f"  {path}   ({source})")
        for name in names:
            full = os.path.join(path, name)
            if os.path.isfile(full):
                found.append(full)
                describe_save(full)
        say()

    if not found:
        say("  No save found. Locations checked:")
        for path, source in save_locations():
            mark = "exists" if os.path.isdir(path) else "  --  "
            say(f"    [{mark}] {path}   ({source})")
        say()
        # 硬编码 AppID 猜错过一次，所以这里干脆把实际存在的都列出来
        say("  Every app id present under Steam userdata (so we stop guessing):")
        for root, _source in steam_roots():
            userdata = os.path.join(root, "userdata")
            if not os.path.isdir(userdata):
                continue
            try:
                for account in sorted(os.listdir(userdata)):
                    account_dir = os.path.join(userdata, account)
                    if not os.path.isdir(account_dir):
                        continue
                    ids = sorted(x for x in os.listdir(account_dir)
                                 if os.path.isdir(os.path.join(account_dir, x)))
                    say(f"    {account_dir}")
                    say(f"      {', '.join(ids) if ids else '(empty)'}")
            except OSError as e:
                say(f"    could not list {userdata}: {e}")
        say()
        say("  If you find a SaveData file anywhere, paste its full path back.")
        return

    import struct
    for path in found:
        size = os.path.getsize(path)
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        say(f"  {path}")
        say(f"    {size} bytes, last written {mtime:%Y-%m-%d %H:%M:%S}")
        try:
            with open(path, "rb") as f:
                head = f.read(52)
            if len(head) >= 12:
                main_ver, steam_id = struct.unpack_from("<iQ", head, 0)
                say(f"    main_version={main_ver}  steam_id={steam_id}")
                say("    >> Header parses. This is a SaveGameFile container; readable.")
        except OSError as e:
            say(f"    could not read: {e}")

    say("  ** Run this again while farming and compare 'last written'. **")
    say("  Changes after each quest -> the panel can refresh per run (what we want).")
    say("  Changes only on exit     -> start/end comparison only.")


# ---------------------------------------------------------------------------
# 5. 虚拟手柄
# ---------------------------------------------------------------------------

def probe_gamepad(do_test):
    section("5. Virtual gamepad  --  the plan for feature 4")

    if vigem is None:
        say(f"  The vigem module failed to import: {_VIGEM_ERROR}")
        say("  >> This build is broken: the module was not bundled.")
        say("     Everything above still stands; only this section is lost.")
        return

    dll = vigem.client_dll_path()
    if not os.path.exists(dll):
        say(f"  Bundled ViGEmClient.dll not found at {dll}")
        say("  >> This build has no gamepad support. Use a CI artifact.")
        return

    # 两条线索先记下来，但都不作数 —— 唯一算数的是能不能真的连上。
    # 第一版在卸载项里没找到就直接 return，于是用 nefconw 手动装的驱动（官方
    # 支持的方式，不写卸载项）被判成"没装"，连试都没试。
    uninstall_entry, version = vigem.driver_installed()
    service = vigem.driver_service_present()
    say(f"  uninstall entry : {uninstall_entry}  {version or ''}")
    say(f"  driver service  : {service}   (HKLM\\SYSTEM\\...\\Services\\ViGEmBus)")
    say("  Neither is proof. Connecting is.")
    say()

    pad = vigem.VirtualGamepad()
    try:
        pad.connect()
    except BaseException as e:
        say(f"  Could not create the virtual gamepad: {e}")
        say()
        if service is False and uninstall_entry is False:
            say("  Both checks say the driver is absent, and connecting failed:")
            say("  the ViGEmBus driver is genuinely not installed.")
            say()
            say("  Extracting the installer is NOT installing it. If you have a")
            say("  folder with ViGEmBus.inf / ViGEmBus.sys / nefconw.exe, that is")
            say("  the extracted payload -- double-clicking nefconw.exe does nothing")
            say("  because it is a command line tool. From an ADMIN prompt, in that")
            say("  folder, run both of these:")
            say()
            say("    nefconw.exe --create-device-node --hardware-id Nefarius\\ViGEmBus\\Gen1"
                " --class-name System --class-guid 4D36E97D-E325-11CE-BFC1-08002BE10318")
            say("    nefconw.exe --install-driver --inf-path \"ViGEmBus.inf\"")
            say()
            say("  Or just run the official installer, which does it for you:")
            say(f"      {vigem.DRIVER_DOWNLOAD_URL}")
        else:
            say("  A driver IS present but the connection failed -- likely a version")
            say("  mismatch between the bundled client and the installed bus driver.")
        return

    try:
        say("  >> Virtual Xbox 360 pad created; Windows sees it.")
        say("     It goes through XInput device state, not window messages, so it")
        say("     needs no focus and never touches your mouse or keyboard.")

        if not do_test:
            say()
            say("  Pass --gamepad-test to actually push the stick.")
            return

        say()
        say("  About to hold the left stick forward for 5 seconds.")
        say("  First run : leave the game focused and watch the character.")
        say("  Second run: Alt-Tab away and repeat. Still moving = feature 4 solved.")
        try:
            input("  Press Enter to start, Ctrl-C to cancel... ")
        except (EOFError, KeyboardInterrupt):
            say("  Cancelled.")
            return
        try:
            pad.left_stick_forward()
            for i in range(5, 0, -1):
                print(f"    ...{i}", end="\r", flush=True)
                time.sleep(1)
        finally:
            pad.neutral()
        say("  Released. Did the character move?")
    finally:
        # 一定要拔掉，否则虚拟手柄会留在系统里，摇杆还推着
        pad.close()


# ---------------------------------------------------------------------------

def run_all(args):
    """跑完所有探测。任何一段抛出的异常由 main() 兜住并写进报告。"""
    say(f"gbfr_auto Windows probe   {datetime.now():%Y-%m-%d %H:%M:%S}")

    probe_self()
    imports_ok = probe_imports()

    if imports_ok:
        hwnd = probe_window(args.title)
    else:
        section("1. Game window")
        say("  Skipped: a required import failed above.")
        hwnd = None

    probe_dpi(hwnd)

    if hwnd and imports_ok:
        probe_capture(hwnd)
    else:
        section("3. Capture backend")
        say("  Skipped: no game window." if imports_ok else "  Skipped: imports failed.")

    probe_save()
    probe_gamepad(args.gamepad_test or ask_gamepad_test(args))


def ask_gamepad_test(args):
    """打包版双击运行时没法传参数，所以在这里问一句。

    只在打包版、且用户没显式给过 --gamepad-test 时才问；命令行运行保持非交互。
    """
    if args.gamepad_test or not getattr(sys, "frozen", False):
        return False
    print()
    print("  The gamepad test pushes the stick for 5s (everything else is read-only).")
    try:
        answer = input("  Run the gamepad test? (y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    chose = answer in ("y", "yes")
    say(f"  [interactive] gamepad test: {'yes' if chose else 'skipped'}")
    return chose


def pause_if_frozen():
    """双击运行时别让窗口一闪而过 —— 输出就是这个工具的全部意义。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        input("\nPress Enter to close... ")
    except (EOFError, KeyboardInterrupt):
        pass


def emergency_dump(text):
    """报告文件都没打开成的时候，最后再试一次把错误落到磁盘上。

    纯 ASCII、纯标准库、不依赖任何前面成功过的东西。
    """
    for directory in (os.path.dirname(os.path.abspath(sys.executable)), os.getcwd()):
        try:
            path = os.path.join(directory, "gbfr-probe-crash.txt")
            with open(path, "w", encoding="ascii", errors="replace") as f:
                f.write(text)
            return path
        except OSError:
            continue
    return None


def main():
    harden_stdout()

    parser = argparse.ArgumentParser(description="gbfr_auto Windows probe")
    parser.add_argument("--title", default=DEFAULT_TITLE,
                        help="substring of the game window title")
    parser.add_argument("--gamepad-test", action="store_true",
                        help="actually push the stick (default: only check the pad can be made)")
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        print("This probe only runs on Windows.")
        pause_if_frozen()
        return 1

    report_path = open_report()
    status = 0
    try:
        run_all(args)
        section("Done")
    except KeyboardInterrupt:
        section("Interrupted")
        say("Ctrl-C. Everything above still stands.")
        status = 130
    except BaseException:
        # 崩溃本身就是最有价值的信息，必须进报告
        section("ABORTED: unexpected error")
        say("Please paste this part back:")
        say()
        detail = traceback.format_exc().rstrip()
        for line in detail.splitlines():
            say("  " + line)
        if report_path is None:
            dumped = emergency_dump(detail)
            if dumped:
                print(f"\nCrash written to {dumped}")
        status = 1
    finally:
        if report_path:
            say()
            say(f"Report: {report_path}")
        if _report is not None:
            try:
                _report.close()
            except OSError:
                pass
        pause_if_frozen()
    return status


if __name__ == "__main__":
    sys.exit(main())
