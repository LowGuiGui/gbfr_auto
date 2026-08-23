# -*- coding: utf-8 -*-
"""Windows 现场探测。在装了游戏的机器上跑一次，把结果贴回来。

PLANNING.md §5 列了六个问题，都只能在 Windows 上、开着游戏才能回答。这个脚本
一次性把它们全测了，省得来回。

    python tools/windows_probe.py                 # 只读，不发任何输入
    python tools/windows_probe.py --gamepad-test  # 额外做虚拟手柄测试（会发输入）

只读。除了往 probe-out/ 写截图和报告之外，什么都不改：不动存档、不动配置、
不动游戏。手柄测试必须显式开启，且会先问一遍。
"""

import argparse
import ctypes
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_DIR = os.path.join(os.getcwd(), "probe-out")
DEFAULT_TITLE = "Granblue"

report_lines = []


def say(line=""):
    print(line)
    report_lines.append(line)


def section(title):
    say()
    say("=" * 68)
    say(title)
    say("=" * 68)


# --------------------------------------------------------------------------
# 1. 窗口：句柄、样式、窗口矩形 vs 客户区矩形
# --------------------------------------------------------------------------

WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
GWL_STYLE = -16


def probe_window(title_substring):
    import win32api
    import win32con
    import win32gui

    section("1. 窗口")
    hwnd = None
    matches = []

    def _enum(h, _):
        if win32gui.IsWindowVisible(h):
            text = win32gui.GetWindowText(h)
            if title_substring.lower() in text.lower():
                matches.append((h, text))
        return True

    win32gui.EnumWindows(_enum, None)
    if not matches:
        say(f"找不到标题含 {title_substring!r} 的窗口。游戏开了吗？")
        say("提示：--title 可以指定其它关键字。")
        return None
    for h, text in matches:
        say(f"  hwnd={h}  title={text!r}")
    hwnd, title = matches[0]

    style = win32api.GetWindowLong(hwnd, GWL_STYLE)
    win_rect = win32gui.GetWindowRect(hwnd)
    cli_rect = win32gui.GetClientRect(hwnd)
    cli_origin = win32gui.ClientToScreen(hwnd, (0, 0))

    say()
    say(f"  窗口矩形 GetWindowRect : {win_rect}  尺寸 {win_rect[2]-win_rect[0]}x{win_rect[3]-win_rect[1]}")
    say(f"  客户区   GetClientRect : {cli_rect}  尺寸 {cli_rect[2]}x{cli_rect[3]}")
    say(f"  客户区原点(屏幕坐标)   : {cli_origin}")
    say(f"  style=0x{style & 0xFFFFFFFF:08X}"
        f"  caption={bool(style & WS_CAPTION)}"
        f"  thickframe={bool(style & WS_THICKFRAME)}"
        f"  popup={bool(style & WS_POPUP)}")

    border_x = cli_origin[0] - win_rect[0]
    border_y = cli_origin[1] - win_rect[1]
    say(f"  边框偏移               : x={border_x} y={border_y}")
    if border_x or border_y:
        say("  >> 有边框：option._get_center() 目前用窗口矩形算中心，会偏这么多。")

    if not (style & WS_CAPTION):
        say("  >> 无标题栏：无边框窗口或独占全屏。")
        say("     独占全屏会盖住任何悬浮面板（功能 3），也常让 PrintWindow 失败。")
    else:
        say("  >> 有标题栏：普通窗口模式。")

    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    info = win32api.GetMonitorInfo(monitor)
    say(f"  所在显示器             : {info.get('Device')}  work={info.get('Work')}")
    say(f"  是否主显示器           : {bool(info.get('Flags'))}")
    return hwnd


# --------------------------------------------------------------------------
# 2. DPI
# --------------------------------------------------------------------------

def probe_dpi(hwnd):
    section("2. DPI / 缩放")
    user32 = ctypes.windll.user32
    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
        say(f"  进程 DPI 感知等级 : {awareness.value}  (0=不感知 1=系统 2=每显示器)")
        if awareness.value == 0:
            say("  >> 不感知：多显示器不同缩放时，坐标和截图尺寸都会错。")
    except Exception as e:
        say(f"  GetProcessDpiAwareness 失败: {e}")
    if hwnd:
        try:
            say(f"  窗口 DPI          : {user32.GetDpiForWindow(hwnd)}  (96 = 100%)")
        except Exception as e:
            say(f"  GetDpiForWindow 失败: {e}")

    say(f"  虚拟桌面 : origin=({user32.GetSystemMetrics(76)},{user32.GetSystemMetrics(77)}) "
        f"size={user32.GetSystemMetrics(78)}x{user32.GetSystemMetrics(79)}")
    say("  >> origin 为负说明有显示器在主屏左侧/上方 —— pyautogui 的 region 截图会踩坑。")


# --------------------------------------------------------------------------
# 3. 截图后端
# --------------------------------------------------------------------------

def probe_capture(hwnd):
    section("3. 截图后端 —— 这是功能 1 的关键问题")
    from opencv import is_blank_frame
    from window_capture import _capture_printwindow

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")

    say("  [PrintWindow] 后台截图，不抢焦点 —— 我们想要的那条路")
    try:
        img = _capture_printwindow(hwnd)
    except Exception as e:
        say(f"    抛异常: {e!r}")
        img = None

    if img is None:
        say("    返回 None —— 失败。")
        say("    >> 需要改用 Windows.Graphics.Capture (WGC)。")
    else:
        path = os.path.join(OUT_DIR, f"printwindow-{stamp}.png")
        img.save(path)
        blank = is_blank_frame(img)
        say(f"    返回 {img.size[0]}x{img.size[1]}，已存到 {path}")
        say(f"    空白帧判定: {blank}")
        if blank:
            say("    >> 全黑：PrintWindow 拿不到这个 D3D 窗口。需要 WGC。")
        else:
            say("    >> 有真实画面。PrintWindow 够用，功能 1 不需要 WGC。")
            say("       请打开图片确认是游戏画面，而不是一张灰底。")
    return img


# --------------------------------------------------------------------------
# 4. 存档文件
# --------------------------------------------------------------------------

def probe_save():
    section("4. 存档文件 —— 功能 3B 的关键问题")
    roots = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Steam\userdata"),
        os.path.expandvars(r"%ProgramFiles%\Steam\userdata"),
        r"C:\Steam\userdata",
        os.path.expandvars(r"%USERPROFILE%\Steam\userdata"),
    ]
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for steam_id in os.listdir(root):
            remote = os.path.join(root, steam_id, "1090670", "remote")
            if os.path.isdir(remote):
                for name in os.listdir(remote):
                    found.append(os.path.join(remote, name))

    if not found:
        say("  没找到存档目录。手动找找：")
        say(r"    <Steam>\userdata\<SteamID>\1090670\remote\ ")
        return

    for path in found:
        size = os.path.getsize(path)
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        say(f"  {path}")
        say(f"    大小 {size} 字节   最后修改 {mtime:%Y-%m-%d %H:%M:%S}")
        try:
            with open(path, "rb") as f:
                head = f.read(52)
            if len(head) >= 12:
                import struct
                main_ver, steam_id = struct.unpack_from("<iQ", head, 0)
                say(f"    main_version={main_ver}  steam_id={steam_id}")
                say("    >> 表头解析正常，是 SaveGameFile 容器，可以读。")
        except OSError as e:
            say(f"    读取失败: {e}")

    say()
    say("  ** 请在农刷时再跑一次这个脚本，比较「最后修改」时间。 **")
    say("  每打完一关就变 = 面板可以按关刷新（想要的结果）。")
    say("  只在退出游戏时变 = 只能做开局/收工两次对比。")


# --------------------------------------------------------------------------
# 5. 虚拟手柄
# --------------------------------------------------------------------------

def probe_gamepad(do_test):
    section("5. 虚拟手柄 —— 这是功能 4 的方案")
    try:
        import vgamepad
    except ImportError:
        say("  未安装 vgamepad。")
        say("    pip install vgamepad     # 会一并装 ViGEmBus 驱动，需要管理员确认")
        say("  >> 装好后重跑本脚本。")
        return

    say(f"  vgamepad 已安装: {getattr(vgamepad, '__version__', '版本未知')}")
    try:
        pad = vgamepad.VX360Gamepad()
    except Exception as e:
        say(f"  创建虚拟手柄失败: {e!r}")
        say("  >> ViGEmBus 驱动没装好。")
        return
    say("  >> 虚拟 Xbox360 手柄创建成功，系统已识别。")
    say("     它读的是设备状态，不是窗口消息 —— 天然不需要焦点，也不碰鼠标键盘。")

    if not do_test:
        say()
        say("  加 --gamepad-test 可以真的按一下按钮（会向游戏发输入）。")
        return

    say()
    say("  即将按 5 秒左摇杆向前。请把游戏切到前台，看角色是否移动。")
    say("  然后 Alt-Tab 切走，再跑一次 —— 后台还动，就说明整件事成立。")
    input("  按 Enter 开始，Ctrl-C 取消... ")
    try:
        pad.left_joystick_float(x_value_float=0.0, y_value_float=1.0)
        pad.update()
        for i in range(5, 0, -1):
            print(f"    ...{i}", end="\r", flush=True)
            time.sleep(1)
    finally:
        pad.reset()
        pad.update()
    say("  已松开。角色动了吗？动了 = 功能 4 有解。")


def main():
    parser = argparse.ArgumentParser(description="gbfr_auto Windows 现场探测")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="游戏窗口标题关键字")
    parser.add_argument("--gamepad-test", action="store_true",
                        help="真的发一次手柄输入（默认只检测能否创建）")
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        print("这个脚本只能在 Windows 上跑。")
        return 1

    say(f"gbfr_auto Windows 探测   {datetime.now():%Y-%m-%d %H:%M:%S}")
    say(f"Python {sys.version.split()[0]}   {sys.platform}")

    hwnd = probe_window(args.title)
    probe_dpi(hwnd)
    if hwnd:
        probe_capture(hwnd)
    else:
        section("3. 截图后端")
        say("  跳过：没找到游戏窗口。")
    probe_save()
    probe_gamepad(args.gamepad_test)

    section("完成")
    os.makedirs(OUT_DIR, exist_ok=True)
    report = os.path.join(OUT_DIR, "probe-report.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")
    say(f"报告已写入 {report}")
    say(f"截图在 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
