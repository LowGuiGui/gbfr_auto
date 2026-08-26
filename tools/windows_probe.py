# -*- coding: utf-8 -*-
"""Windows 现场探测。在装了游戏的机器上跑一次，把报告贴回来。

PLANNING.md §5 列的问题只能在 Windows 上、开着游戏才能回答。这个脚本一次性把
它们全测了。

    gbfr-probe.exe                 只读，不发任何输入
    gbfr-probe.exe --gamepad-test  额外做虚拟手柄测试（会向游戏发输入）
    gbfr-probe.exe --xinput-test   量"失焦时 XInput 会不会被清零"（#45 的决定性
                                   测量；不需要游戏在跑，只需要十几秒和一次点击）
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
import textwrap
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

try:
    import procinfo  # noqa: E402
    import xinput  # noqa: E402
    _INPUT_SCAN_ERROR = None
except BaseException as _e:  # noqa: BLE001 - 同上，打包漏了也要出报告
    procinfo = None
    xinput = None
    _INPUT_SCAN_ERROR = f"{type(_e).__name__}: {_e}"

try:
    import framediff  # noqa: E402
    _FRAMEDIFF_ERROR = None
except BaseException as _e:  # noqa: BLE001 - numpy 没打进去也要出报告
    framediff = None
    _FRAMEDIFF_ERROR = f"{type(_e).__name__}: {_e}"

# 注入那条路。**模块级导入是刻意的**：hook/ 没有 __init__.py，是个命名空间包，
# 而 PyInstaller 对命名空间包的静态分析本来就弱 —— 写在函数里更容易被漏掉，然后
# 在用户机器上才炸。放在这里，CI 的 warn 文件检查也能看见它。
try:
    import window_input  # noqa: E402
    from hook import injector as hook_injector  # noqa: E402
    _INJECT_ERROR = None
except BaseException as _e:  # noqa: BLE001 - 漏打包也要出报告，不能整个崩掉
    window_input = None
    hook_injector = None
    _INJECT_ERROR = f"{type(_e).__name__}: {_e}"

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


def wrap(text, width=68):
    """把一段判定说明折成报告里的等宽行。

    判定文字来自 xinput / procinfo 两个模块，长度不受这里控制；报告的缩进又全是
    手写的，所以折行必须在打印这一侧做。空字符串也要返回一行，否则调用点还得
    单独判空。
    """
    return textwrap.wrap(text, width) or [""]


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

    say("  ** Run this again while farming and compare 'last written'. **")
    say("  Changes after each quest -> the panel can refresh per run (what we want).")
    say("  Changes only on exit     -> start/end comparison only.")


# ---------------------------------------------------------------------------
# 5. 虚拟手柄
# ---------------------------------------------------------------------------

def window_mode_label(hwnd):
    """把窗口模式说成人话 —— 手柄测试的结论完全取决于它。"""
    if not hwnd:
        return "unknown (no game window found)"
    try:
        import win32api
        import win32gui
        style = win32api.GetWindowLong(hwnd, GWL_STYLE)
        rect = win32gui.GetWindowRect(hwnd)
    except Exception:
        return "unknown (could not read the window)"
    if style & WS_CAPTION:
        return "ordinary WINDOWED (has a title bar)"
    size = (rect[2] - rect[0], rect[3] - rect[1])
    return f"borderless / Full Screen Window ({size[0]}x{size[1]})"


def probe_gamepad(do_test, hwnd=None):
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
    say(f"  uninstall entry : {uninstall_entry}  {version or ''}"
        "   (both 32- and 64-bit registry views)")
    say(f"  driver service  : {service}   (HKLM\\SYSTEM\\...\\Services\\ViGEmBus)")
    say(f"  driver binary   : {vigem.loaded_driver_path()}")

    # 重复的总线设备是"插入音响了、一两秒后又拔出"最常见的原因
    instances = vigem.bus_device_instances()
    if instances is None:
        say("  bus instances   : could not read the Enum registry")
    else:
        say(f"  bus instances   : {len(instances)}   {instances}")
        if len(instances) > 1:
            say("  >> MORE THAN ONE bus device. That is the classic symptom of two")
            say("     ViGEmBus installations -- the client attaches to one while the")
            say("     device enumerates on the other. Uninstall every ViGEmBus entry")
            say("     from Apps & Features, reboot, then install 1.22.0 once.")

    # Sunshine 等同样依赖 ViGEmBus 的软件常常自带一份驱动
    others = vigem.other_vigem_users()
    if others:
        say("  other ViGEm users on this machine:")
        for name, description, start in others:
            mode = {2: "auto", 3: "manual", 4: "disabled"}.get(start, start)
            say(f"    {name:<18} {description}   (start={mode})")
    else:
        say("  other ViGEm users: none detected")

    say()
    say("  None of the above is proof. Connecting is.")
    say()

    pad = vigem.VirtualGamepad()
    try:
        pad.connect()
    except BaseException as e:
        say(f"  Could not create the virtual gamepad: {e}")
        say()
        if others:
            say(f"  {others[0][0]} also uses ViGEmBus and ships its own copy. Two")
            say("  installations is a known cause of this. Test it, reversibly:")
            say(f"    net stop {others[0][0]}     (admin; undo with net start ...)")
            say()
        say("  What that sequence means: vigem_target_add first plugs the device in")
        say("  (Windows plays the connect chime), then waits for it to become ready.")
        say("  When that wait fails the client unplugs it again (disconnect chime)")
        say("  and returns the REMOVAL's error -- which is why the code says")
        say("  TARGET_NOT_PLUGGED_IN even though plugging in is the part that worked.")
        say()
        # 只有两条线索都说"没有"，才敢让人去装。任何一条说"有"或"不知道"，
        # 都可能是已经装好了 —— 这种情况下再跑一次 --create-device-node
        # 会多出一个重复的设备节点，比什么都不做更糟。
        if service is False and uninstall_entry is False:
            say("  Both checks say the driver is absent, and connecting failed:")
            say("  the ViGEmBus driver is genuinely not installed.")
            say()
            say("  Do this:")
            say()
            say(f"    1. Download {vigem.DRIVER_DOWNLOAD_URL}")
            say("    2. Double-click it and let it install. Do NOT pass /extract.")
            say("    3. Reboot, then run this probe again.")
            say()
            say("  If you already have a folder holding ViGEmBus.inf, ViGEmBus.sys")
            say("  and nefconw.exe, that is the EXTRACTED payload, not an install --")
            say("  the installer was run with /extract, or unpacked by hand. Nothing")
            say("  in that folder installs anything when double-clicked; nefconw.exe")
            say("  is a command line tool and exits immediately without arguments.")
            say("  Just run the installer above instead.")
            say()
            say("  Only if the installer refuses to run (Windows Server, or a policy")
            say("  that blocks it), install by hand from an ADMIN prompt in that")
            say("  folder. Values below are taken from ViGEmBus's own INF:")
            say()
            say("    nefconw.exe --create-device-node --hardware-id Nefarius\\ViGEmBus\\Gen1"
                " --class-name System --class-guid 4D36E97D-E325-11CE-BFC1-08002BE10318")
            say("    nefconw.exe --install-driver --inf-path ViGEmBus.inf")
        else:
            say("  Something says a driver IS present, but connecting failed.")
            say("  DO NOT run the manual install commands in this state -- a second")
            say("  --create-device-node would add a duplicate device node.")
            say()
            say("  Check Device Manager -> System devices for")
            say("  'Nefarius Virtual Gamepad Emulation Bus'. If it is there with a")
            say("  warning icon, the driver is installed but not started: reboot, or")
            say("  reinstall over the top with the official installer.")
        return

    try:
        say(f"  >> Virtual Xbox 360 pad created (attempt {pad.attempts_used}"
            f" of {vigem.RETRY_ATTEMPTS}); Windows sees it.")
        say("     It goes through XInput device state, not window messages, so it")
        say("     needs no focus and never touches your mouse or keyboard.")

        if not do_test:
            say()
            say("  Pass --gamepad-test to actually push the stick.")
            return

        say()
        # 这是功能 4 唯一还没答的问题，而窗口模式是关键变量：社区那条"后台也能
        # 收手柄输入"的说法明确限定在"全屏窗口"（无边框）模式，普通窗口模式并不
        # 在其列。在窗口模式下失焦不动，并不能推翻它。
        say(f"  The game is currently in: {window_mode_label(hwnd)}")
        say()
        say("  About to hold the left stick forward for 5 seconds.")
        say("  Run A: game focused          -> character should move")
        say("  Run B: Alt-Tab away, repeat  -> THIS is the question")
        say()
        say("  If B fails, retry the whole thing with the game set to")
        say("  'Full Screen Window' (borderless) in its display options. That mode")
        say("  is the one reported to keep accepting controller input while in the")
        say("  background; ordinary windowed mode is not, and testing windowed")
        say("  proves nothing either way.")
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
# 6. 游戏在用哪套输入 API
# ---------------------------------------------------------------------------

def probe_input_backend(hwnd):
    section("6. Game's input backend  --  which #45 fix applies")

    if procinfo is None:
        say(f"  The procinfo module failed to import: {_INPUT_SCAN_ERROR}")
        say("  >> This build is broken: the module was not bundled.")
        return None

    if not hwnd:
        say("  Skipped: no game window, so there is no process to look at.")
        return None

    pid = procinfo.pid_for_window(hwnd)
    if pid is None:
        say("  Could not map the window to a process.")
        return None
    say(f"  pid           : {pid}")

    paths, error, exe = procinfo.process_modules(pid)
    if exe:
        say(f"  image         : {exe}")
    if paths is None:
        say(f"  Could not list the loaded modules: {error}")
        say()
        say("  If that says access denied, the game is running elevated and this")
        say("  probe is not. Right-click gbfr-probe.exe -> Run as administrator")
        say("  and run it again. Everything else in this report is unaffected.")
        return None

    say(f"  modules loaded: {len(paths)}")
    say()

    found = procinfo.classify_modules(paths)
    if not found:
        say("  No recognised input DLL is loaded.")
    for label, strength, hits in found:
        mark = "  " if strength == "strong" else " ?"
        say(f"  {mark} {label:<36} {', '.join(hits)}")
    say()

    code, explanation = procinfo.backend_verdict(found)
    say(f"  verdict: [{code}]")
    for line in wrap(explanation):
        say(f"    {line}")
    return code


# ---------------------------------------------------------------------------
# 7. XInput 失焦门 —— #45 的决定性测试
# ---------------------------------------------------------------------------

def probe_xinput_focus(do_test):
    section("7. XInput focus gate  --  the decisive test for #45")

    if xinput is None:
        say(f"  The xinput module failed to import: {_INPUT_SCAN_ERROR}")
        return

    libs = xinput.available_libraries()
    if not libs:
        say("  No XInput DLL could be loaded at all. That is very unusual on")
        say("  Windows; nothing below can run.")
        return
    say("  XInput DLLs present: " + ", ".join(name for name, _dll in libs))
    say()
    say("  Why this test exists: Microsoft documents that on Windows 10+ the")
    say("  SYSTEM disables game controller input based on window focus, and that")
    say("  a disabled XInput returns neutral data. A developer report on their own")
    say("  Q&A says the opposite happens in practice. Whichever is true here")
    say("  decides whether #45 is fixable by hooking the game at all.")
    say()

    if not do_test:
        say("  Pass --xinput-test to run it. It needs about 15 seconds and one click.")
        return

    if vigem is None:
        say("  Needs the virtual pad to have something to read, and the vigem")
        say(f"  module failed to import: {_VIGEM_ERROR}")
        return

    name, dll = libs[0]
    say(f"  Using {name} (first that loaded).")
    say()
    say("  What will happen: a virtual pad is plugged in and its left stick is")
    say("  held forward. This process then reads XInputGetState ten times a")
    say("  second for 12 seconds, recording which window was in front each time.")
    say()
    say("  WHAT YOU DO:")
    say("    1. Press Enter.")
    say("    2. Leave THIS window in front for about 4 seconds.")
    say("    3. Then click on any other window and leave it in front.")
    say()
    say("  The game does not need to be running for this test.")
    try:
        input("  Press Enter to start, Ctrl-C to skip... ")
    except (EOFError, KeyboardInterrupt):
        say("  Skipped.")
        return

    baseline = xinput.foreground_window()
    if baseline is None:
        say("  Could not read the foreground window; cannot run the test.")
        return

    pad = vigem.VirtualGamepad()
    try:
        pad.connect()
    except BaseException as e:
        say(f"  Could not create the virtual gamepad: {e}")
        say("  Section 5 above explains what to do about that.")
        return

    try:
        slots = xinput.connected_slots(dll)
        say(f"  pad visible on XInput slot(s): {slots if slots else 'none'}")
        if not slots:
            say("  >> The virtual pad is plugged in but XInput cannot see it.")
            say("     Nothing below would mean anything; stopping here.")
            return

        # 先推摇杆，再挑槽位。ViGEm 拔设备不是同步的，上一段（第 5 段）的手柄
        # 可能还在，于是新的落到 1 号而 0 号留着个恒中立的幽灵 —— 盲取 slots[0]
        # 就是这样把真机上那次测量整段作废的。
        pad.left_stick_forward()
        slot = xinput.responding_slot(dll, slots)
        if slot is None:
            say("  >> The stick is held down but no slot reports it.")
            say("     Every reading would be neutral and the verdict would be")
            say("     meaningless, so stopping here instead of measuring noise.")
            if len(slots) > 1:
                say(f"     {len(slots)} slots are occupied -- a pad from an earlier")
                say("     test may not have finished unplugging. Re-run the probe.")
            return
        if slot != slots[0]:
            say(f"  >> Measuring slot {slot}, not {slots[0]}: only that one responds")
            say("     to the stick. The others look like leftover devices.")
        else:
            say(f"  measuring slot {slot}")

        samples = xinput.sample_focus(
            dll, index=slot, seconds=12.0, interval=0.1,
            focus=xinput.baseline_watcher(baseline),
        )
    finally:
        try:
            pad.neutral()
        finally:
            pad.close()

    buckets = xinput.summarize(samples)
    say()
    say(f"  samples: {len(samples)}")
    say(f"    focused   + live    : {buckets['focused_live']}")
    say(f"    focused   + NEUTRAL : {buckets['focused_neutral']}")
    say(f"    unfocused + live    : {buckets['unfocused_live']}")
    say(f"    unfocused + NEUTRAL : {buckets['unfocused_neutral']}")
    say(f"    focus unknown       : {buckets['unknown']}")
    say()

    code, explanation = xinput.verdict(buckets)
    say(f"  verdict: [{code}]")
    for line in wrap(explanation):
        say(f"    {line}")

    caveat = xinput.focus_caveat(buckets, code)
    if caveat:
        say()
        say("  Note on this result:")
        for line in wrap(caveat, 66):
            say(f"    {line}")


# ---------------------------------------------------------------------------
# 8. 失焦以后：停了，还是只是不理输入
# ---------------------------------------------------------------------------

PHASE_FRAMES = 16
PHASE_INTERVAL = 0.2


def _capture_deltas(hwnd, frames=PHASE_FRAMES, interval=PHASE_INTERVAL):
    """连续截图，返回相邻帧差分的统计量和截图失败的次数。"""
    from window_capture import _capture_printwindow

    deltas = []
    failures = 0
    previous = None
    for _ in range(frames):
        try:
            img = _capture_printwindow(hwnd)
        except BaseException:  # noqa: BLE001 - 截图失败不该让整段测试消失
            img = None
        if img is None:
            failures += 1
        else:
            if previous is not None:
                deltas.append(framediff.frame_delta(previous, img))
            previous = img
        time.sleep(interval)
    return framediff.summarize(deltas), failures


def _report_phase(label, stats, failures):
    say(f"  {label:<28} frames={stats['count']:<3} "
        f"mean={stats['mean']:7.3f}  max={stats['max']:7.3f}")
    if failures:
        say(f"  {'':28} ({failures} capture(s) failed)")
    if stats["dropped"]:
        say(f"  {'':28} ({stats['dropped']} frame(s) dropped -- window resized?)")


def _game_is_focused(hwnd):
    """游戏窗口是不是前台。拿不到返回 None。"""
    front = xinput.foreground_window() if xinput else None
    if front is None:
        return None
    return int(front) == int(hwnd)


def probe_focus_behaviour(do_test, hwnd):
    section("8. Focus behaviour  --  frozen, or just ignoring input?")

    if framediff is None:
        say(f"  The framediff module failed to import: {_FRAMEDIFF_ERROR}")
        say("  >> This build is broken: the module (or numpy) was not bundled.")
        return

    say("  Section 5 asks 'did the character move?' and a human answers it. That")
    say("  cannot tell these two apart, and they need completely different fixes:")
    say()
    say("    the whole game is PAUSED   -> the 1.1 anti-AFK pause. Nothing about")
    say("                                  input delivery would help.")
    say("    running but IGNORING input -> an input-delivery problem, which is")
    say("                                  what #45 has been assuming all along.")
    say()
    say("  This measures it by diffing captured frames instead of guessing.")
    say()

    if not hwnd:
        say("  Skipped: the game window was not found, so there is nothing to watch.")
        return
    if not do_test:
        say("  Pass --focus-test to run it. Needs ~30 seconds and two clicks,")
        say("  and the game must be IN A QUEST with something moving on screen.")
        return

    say("  IMPORTANT: be in a quest with visible motion. Run this on a menu or a")
    say("  still screen and there is no signal to measure -- the test will say so")
    say("  rather than invent an answer, but you will have wasted the run.")
    say()
    say("  Three phases, about 3 seconds each:")
    say("    1. game focused, no input     <- the baseline")
    say("    2. game NOT focused, no input <- did it stop?")
    say("    3. game NOT focused, stick held forward")
    say()
    try:
        input("  Click the GAME so it is in front, then press Enter here... ")
    except (EOFError, KeyboardInterrupt):
        say("  Skipped.")
        return

    # Enter 是在探测器窗口里按的，所以此刻前台多半是探测器而不是游戏。
    # 给一点时间让用户点回游戏，再开始量。
    say()
    say("  Starting in 5 seconds -- click the GAME window now.")
    for i in range(5, 0, -1):
        print(f"    ...{i}", end="\r", flush=True)
        time.sleep(1)
    print("         ", end="\r")

    focused = _game_is_focused(hwnd)
    if focused is False:
        say("  >> The game is NOT in front. Phase 1 would measure the wrong thing.")
        say("     Stopping here rather than producing a misleading baseline.")
        return
    say("  Phase 1: game focused, no input")
    phase1, fail1 = _capture_deltas(hwnd)
    _report_phase("focused + idle", phase1, fail1)

    say()
    say("  Now click ANY OTHER window and leave it in front. 5 seconds.")
    for i in range(5, 0, -1):
        print(f"    ...{i}", end="\r", flush=True)
        time.sleep(1)
    print("         ", end="\r")

    if _game_is_focused(hwnd) is True:
        say("  >> The game is still in front. Phases 2 and 3 need it unfocused.")
        say("     Stopping here rather than reporting a comparison that is not one.")
        return
    say("  Phase 2: game unfocused, no input")
    phase2, fail2 = _capture_deltas(hwnd)
    _report_phase("unfocused + idle", phase2, fail2)

    phase3, fail3 = None, 0
    if vigem is None:
        say()
        say(f"  Phase 3 skipped: the vigem module failed to import ({_VIGEM_ERROR}).")
        say("  The motion verdict below still stands; only the input half is lost.")
    else:
        pad = vigem.VirtualGamepad()
        try:
            pad.connect()
        except BaseException as e:
            say()
            say(f"  Phase 3 skipped: could not create the virtual gamepad ({e}).")
            say("  Section 5 explains what to do about that. The motion verdict")
            say("  below still stands.")
            pad = None
        if pad is not None:
            try:
                say()
                say("  Phase 3: game unfocused, holding the stick forward")
                pad.left_stick_forward()
                phase3, fail3 = _capture_deltas(hwnd)
            finally:
                try:
                    pad.neutral()
                finally:
                    pad.close()
            _report_phase("unfocused + stick held", phase3, fail3)

    say()
    motion_code, motion_text = framediff.motion_verdict(phase1, phase2)
    say(f"  motion verdict: [{motion_code}]")
    for line in wrap(motion_text):
        say(f"    {line}")

    if phase3 is not None:
        say()
        input_code, input_text = framediff.input_verdict(phase2, phase3, motion_code)
        say(f"  input verdict : [{input_code}]")
        for line in wrap(input_text):
            say(f"    {line}")

    say()
    say("  Thresholds in framediff.py are estimates until real numbers come back.")
    say("  The raw means above are the measurement; the verdicts are a reading of")
    say("  them. If the two disagree, trust the numbers and say so.")


# ---------------------------------------------------------------------------
# 9. 焦点钩子 —— #45 的修法，先观察再试
# ---------------------------------------------------------------------------

def _spoof_countdown(seconds, message):
    say(f"  {message}")
    for i in range(seconds, 0, -1):
        print(f"    ...{i}", end="\r", flush=True)
        time.sleep(1)
    print("         ", end="\r")


def probe_focus_hook(do_test, hwnd):
    section("9. Focus hook  --  the #45 fix, observed then tested")

    say("  Measured (PLANNING.md 5.3): Windows does not gate XInput, the game")
    say("  reads the pad fine, and the game PAUSES ITSELF on focus loss. So the")
    say("  fix is to stop it noticing. This section tries that.")
    say()

    if window_input is None:
        say(f"  The injection modules failed to import: {_INJECT_ERROR}")
        say("  >> This build is broken: they were not bundled.")
        return
    if framediff is None:
        say(f"  The framediff module failed to import: {_FRAMEDIFF_ERROR}")
        return
    if not hwnd:
        say("  Skipped: the game window was not found.")
        return

    if not do_test:
        say("  Pass --focus-hook-test to run it.")
        say()
        say("  READ THIS FIRST. Unlike every other section, it INJECTS A DLL into")
        say("  the game process. Specifically:")
        say("    - it loads hook/gbfr_hook.dll into Granblue Fantasy: Relink")
        say("    - focus spoofing starts OFF; stage 1 only counts, and every")
        say("      message it counts is forwarded to the game untouched")
        say("    - the DLL cannot be unloaded again; it stays until the game exits")
        say("  Stage 2 asks separately before changing anything.")
        return

    say("  ** This section INJECTS A DLL into the running game. **")
    say()
    say("  Everything else in this probe only reads. This does not.")
    say()
    say("    - loads hook/gbfr_hook.dll into the game process")
    say("    - spoofing starts OFF: stage 1 only counts, changes nothing")
    say("    - to count window messages it does subclass the game's window,")
    say("      which forwards everything untouched while spoofing is off")
    say("    - THE DLL CANNOT BE UNLOADED. It stays until the game exits.")
    say("    - if anything goes wrong, closing the game clears it completely")
    say()
    say("  Do not do this in the middle of a run you care about.")
    say()
    try:
        answer = input("  Type  inject  to proceed, anything else to skip: ")
    except (EOFError, KeyboardInterrupt):
        say("  Skipped.")
        return
    if answer.strip().lower() != "inject":
        say(f"  Skipped (got {answer.strip()!r}).")
        return

    wi = window_input.WindowInput()
    wi.set_target(hwnd)
    say()
    say("  Injecting...")
    try:
        ok = wi.enable_inject(progress_cb=lambda m: say(f"    {m}"))
    except BaseException as e:
        say(f"  Injection failed: {type(e).__name__}: {e}")
        say()
        say("  If that mentions access or a handle, the game is running at a")
        say("  higher privilege level than this probe -- Reloaded-II runs as")
        say("  admin, so the game does too. Right-click gbfr-probe.exe ->")
        say("  Run as administrator and try again.")
        return
    if not ok:
        say("  Injection reported failure. Nothing was changed.")
        return
    say("  >> Injected, and the pipe is connected.")

    try:
        _focus_hook_stage1(wi, hwnd)
    finally:
        # 无论如何都要把伪装关掉。DLL 留在进程里没办法，但它必须是"什么都不做"
        # 的状态 —— 否则游戏会一直以为自己是前台，而用户并不知道。
        try:
            wi.disable_focus_spoof()
        except BaseException:  # noqa: BLE001
            pass
        say()
        say("  Focus spoofing is OFF again. The DLL stays loaded until the game")
        say("  exits, but in this state it only counts calls -- it changes nothing.")


def _focus_hook_stage1(wi, hwnd):
    say()
    say("  --- Stage 1: observe. Spoofing is OFF; nothing changes. ---")
    say()

    # 必须在用户 alt-tab 之前装上。消息计数器靠窗口子类化，而子类化原来只在
    # SPOOF_ON 里才装 —— stage 1 从不发 SPOOF_ON，于是那三个计数器结构上永远是
    # 0，判定只可能落在 polls 或 no-hooks-hit 上，无论游戏实际上在做什么。
    if wi.watch_focus_events():
        say("  Observer armed: the window-proc counters are now live too.")
        say("  Spoofing is still OFF -- with it off the subclass only counts,")
        say("  and forwards every message to the game untouched.")
    else:
        say("  >> Could not arm the window-proc observer. The poll counters")
        say("     below still mean something; the message ones cannot.")
    say()
    say("  Alt-tab AWAY from the game and BACK, three times. Take your time.")
    say("  The counters record how the game noticed each time.")
    try:
        input("  Press Enter when you have done that... ")
    except (EOFError, KeyboardInterrupt):
        say("  Skipped.")
        return

    raw = wi.focus_spoof_stats()
    say(f"  raw: {raw}")
    stats = hook_injector.parse_stats(raw)
    if stats:
        # 先报"装上了没有"，再报"被调了几次"。两者混在一起看，全零的计数器会
        # 被读成"游戏不走这条路"，而它同样可能是"钩子根本没进去"。
        say(f"    installed IAT patches={stats.get('iat', '?')}/3"
            f"  window proc subclassed={'yes' if stats.get('sub') else 'no'}")
        say(f"    polls    GetForegroundWindow={stats.get('fg', 0)}"
            f"  GetActiveWindow={stats.get('active', 0)}"
            f"  GetFocus={stats.get('focus', 0)}")
        say(f"    messages WM_KILLFOCUS={stats.get('kill', 0)}"
            f"  WM_ACTIVATE={stats.get('act', 0)}"
            f"  WM_ACTIVATEAPP={stats.get('actapp', 0)}")

    # 指令有没有真的走到对面。这一条是在管道**另一头**数出来的，所以它能回答
    # Python 这边永远回答不了的那个问题：写调用说成功了，DLL 到底收到没有。
    delivery = hook_injector.delivery_verdict(
        getattr(wi, "commands_sent", 0), stats)
    if delivery:
        say()
        say(f"  delivery: [{delivery[0]}]")
        for line in wrap(delivery[1]):
            say(f"    {line}")

    code, explanation = hook_injector.stats_verdict(stats)
    say()
    say(f"  mechanism: [{code}]")
    for line in wrap(explanation):
        say(f"    {line}")

    if code in ("no-hooks-hit", "no-data", "not-installed"):
        say()
        say("  Stage 2 would prove nothing from here -- if nothing is being")
        say("  intercepted, turning the spoof on cannot change the outcome.")
        say("  Stopping. Send this section back; the hook needs widening.")
        return

    say()
    say("  --- Stage 2: actually lie to the game. ---")
    say()
    say("  This makes the game believe it is focused. If it works, the game")
    say("  keeps running while you are in another window.")
    say("  Be in a quest with visible motion, as in section 8.")
    try:
        answer = input("  Run stage 2? (y/N) ")
    except (EOFError, KeyboardInterrupt):
        say("  Skipped.")
        return
    if answer.strip().lower() not in ("y", "yes"):
        say("  Stage 2 skipped.")
        return

    _spoof_countdown(5, "Click the GAME window now.")
    if _game_is_focused(hwnd) is False:
        say("  >> The game is not in front; the baseline would be wrong. Stopping.")
        return
    say("  Baseline: game focused, spoofing off")
    before, _ = _capture_deltas(hwnd)
    _report_phase("focused + idle", before, 0)

    if not wi.enable_focus_spoof():
        say("  >> Could not turn spoofing on.")
        return
    say("  >> Spoofing ON: the game is now told it is always focused.")

    _spoof_countdown(5, "Now click ANY OTHER window and leave it in front.")
    if _game_is_focused(hwnd) is True:
        say("  >> The game is still in front; this would not test anything.")
        return
    say("  Measuring: game unfocused, spoofing ON")
    after, _ = _capture_deltas(hwnd)
    _report_phase("unfocused + spoofed", after, 0)

    say()
    code, explanation = framediff.motion_verdict(before, after)
    say(f"  verdict: [{code}]")
    for line in wrap(explanation):
        say(f"    {line}")
    say()
    if code == "running":
        say("  >> That is #45 solved: the game kept running while unfocused.")
    elif code == "frozen":
        say("  >> The spoof did not stop the pause. The game noticed some other")
        say("     way -- see PLANNING.md 5.1 for what is left to try.")
    say("  Compare against section 8's numbers, which were taken WITHOUT the")
    say("  spoof. That comparison is the whole result.")


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
    probe_gamepad(args.gamepad_test or ask_gamepad_test(args), hwnd)
    probe_input_backend(hwnd)
    probe_xinput_focus(args.xinput_test or ask_xinput_test(args))
    probe_focus_behaviour(args.focus_test, hwnd)
    probe_focus_hook(args.focus_hook_test, hwnd)


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


def ask_xinput_test(args):
    """同 ask_gamepad_test：打包版双击运行时没法传参数，所以问一句。

    问得比手柄那个更值得 —— 这一项是 #45 唯一的决定性测量，而且不需要游戏在跑，
    所以任何一次探测都可以顺手做掉。
    """
    if args.xinput_test or not getattr(sys, "frozen", False):
        return False
    print()
    print("  The XInput focus test answers the one open question for #45.")
    print("  It needs ~15 seconds and one click. The game does not need to run.")
    try:
        answer = input("  Run the XInput focus test? (Y/n) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    chose = answer in ("", "y", "yes")
    say(f"  [interactive] xinput focus test: {'yes' if chose else 'skipped'}")
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
    parser.add_argument("--xinput-test", action="store_true",
                        help="measure whether Windows zeroes XInput while unfocused (#45)")
    parser.add_argument("--focus-test", action="store_true",
                        help="measure whether the game freezes or just ignores input (#45)")
    parser.add_argument("--focus-hook-test", action="store_true",
                        help="INJECT the hook DLL and try the #45 focus spoof (asks first)")
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
