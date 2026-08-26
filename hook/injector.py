# -*- coding: utf-8 -*-
"""
DLL 注入 + 命名管道通信
用于将 gbfr_hook.dll 注入到目标游戏进程，并通过命名管道发送键鼠命令
"""

import os
import ctypes
import threading
from ctypes import wintypes
import win32process
import win32gui
import win32con
import win32api
import win32event
import win32file
import win32pipe
import win32security
import pywintypes

from applog import get_logger

log = get_logger(__name__)

PIPE_NAME = r"\\.\pipe\gbfr_hook"


def hwnd_to_pid(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        log.warning("取窗口 PID 失败 (hwnd=%s)", hwnd, exc_info=True)
        return None


def find_pid_by_title(title_substring):
    found = []
    def _enum(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title_substring.lower() in title.lower():
            pid = hwnd_to_pid(hwnd)
            if pid:
                found.append(pid)
        return True
    win32gui.EnumWindows(_enum, None)
    return found[0] if found else None


def inject_dll(pid, dll_path):
    dll_path = os.path.abspath(dll_path)
    if not os.path.exists(dll_path):
        raise FileNotFoundError(f"DLL not found: {dll_path}")

    PROCESS_ALL_ACCESS = 0x1F0FFF
    MEM_COMMIT_RESERVE = 0x3000
    PAGE_READWRITE = 0x04

    h_process = win32api.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not h_process:
        raise RuntimeError(f"OpenProcess failed (pid={pid})")

    try:
        dll_data = dll_path.encode("utf-16-le") + b"\x00\x00"
        p_remote = win32process.VirtualAllocEx(
            h_process, 0, len(dll_data),
            MEM_COMMIT_RESERVE, PAGE_READWRITE
        )
        if not p_remote:
            raise RuntimeError("VirtualAllocEx failed")

        written = win32process.WriteProcessMemory(h_process, p_remote, dll_data)
        if written != len(dll_data):
            raise RuntimeError(f"WriteProcessMemory wrote {written}/{len(dll_data)} bytes")

        h_kernel32 = win32api.GetModuleHandle("kernel32.dll")
        load_lib = win32api.GetProcAddress(h_kernel32, "LoadLibraryW")
        if not load_lib:
            raise RuntimeError("GetProcAddress LoadLibraryW failed")

        h_thread, _ = win32process.CreateRemoteThread(
            h_process, None, 0, load_lib, p_remote, 0
        )
        if not h_thread:
            raise RuntimeError("CreateRemoteThread failed")

        win32event.WaitForSingleObject(h_thread, 5000)
        exit_code = win32process.GetExitCodeThread(h_thread)
        if exit_code == 0:
            raise RuntimeError(
                "LoadLibraryW 返回 NULL (DLL 加载失败, 可能路径含中文或权限不足)"
            )
        win32api.CloseHandle(h_thread)
    finally:
        win32api.CloseHandle(h_process)


# --- SPOOF_STATS 的解析与解读（#45）---------------------------------------
#
# DLL 回的是一行： STATS on=1 fg=42 active=0 focus=0 kill=3 act=3 actapp=1
#
# 这几个计数器是**外部探测器永远看不到的那一面**：游戏究竟是轮询"我在前台吗"，
# 还是等窗口消息通知它。两条路的修法不同，而且如果两边都是 0，说明 IAT 补丁根本
# 没打中 —— 那才是最需要立刻知道的情况。
#
# 纯函数，放在这里是因为它属于协议；Linux 上可完整测试。

def parse_stats(text):
    """把 STATS 行拆成 dict。不是 STATS 行就返回 None。"""
    if not text:
        return None
    text = text.strip()
    if not text.startswith("STATS"):
        return None
    out = {}
    for token in text.split()[1:]:
        key, sep, value = token.partition("=")
        if not sep:
            continue
        try:
            out[key] = int(value)
        except ValueError:
            continue
    return out or None


def stats_verdict(stats):
    """计数器说明游戏靠什么察觉失焦。返回 (代号, 一行 ASCII 说明)。"""
    if not stats:
        return ("no-data", "No counters came back. The DLL may not be connected.")

    poll = stats.get("fg", 0) + stats.get("active", 0) + stats.get("focus", 0)
    msgs = stats.get("kill", 0) + stats.get("act", 0) + stats.get("actapp", 0)

    if poll == 0 and msgs == 0:
        return ("no-hooks-hit",
                "Nothing was intercepted at all. Either the game never lost focus "
                "during the test, or the IAT patch missed -- the focus logic may "
                "live in a DLL rather than the exe, or be resolved through "
                "GetProcAddress. The hook needs widening before the spoof can work.")
    if poll and not msgs:
        return ("polls",
                "The game POLLS for focus (GetForegroundWindow / GetActiveWindow / "
                "GetFocus). The IAT patch is the part that matters; the window-proc "
                "subclass is not carrying this.")
    if msgs and not poll:
        return ("messages",
                "The game is TOLD by window messages (WM_KILLFOCUS / WM_ACTIVATE / "
                "WM_ACTIVATEAPP). The subclass is the part that matters; the IAT "
                "patch is not carrying this.")
    return ("both",
            "Both paths fired. Covering both was the right call -- neither one "
            "alone would have been enough to be sure.")


class HookClient:
    """命名管道服务端，等待注入的 DLL 连接并向其发送命令"""

    def __init__(self):
        self._pipe = None
        self._connected = False
        self._thread = None
        self._last_error = None
        self._overlapped = None

    def _create_server(self):
        try:
            sd = win32security.SECURITY_DESCRIPTOR()
            sd.SetSecurityDescriptorDacl(1, None, 0)
            sa = pywintypes.SECURITY_ATTRIBUTES()
            sa.bInheritHandle = 0
            sa.SECURITY_DESCRIPTOR = sd

            self._pipe = win32pipe.CreateNamedPipe(
                PIPE_NAME,
                win32con.PIPE_ACCESS_DUPLEX | win32con.FILE_FLAG_OVERLAPPED,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                5, 65536, 65536, 0, sa
            )
        except pywintypes.error as e:
            self._last_error = f"CreateNamedPipe 失败: {e}"
            self._pipe = None
        except Exception as e:
            self._last_error = f"CreateNamedPipe 异常: {e}"
            self._pipe = None
            log.exception("CreateNamedPipe 异常")
        return self._pipe is not None

    def create_server(self):
        if self._pipe is not None:
            return True
        return self._create_server()

    def start_listening(self):
        if self._pipe is None:
            if not self._create_server():
                return False
        self._overlapped = pywintypes.OVERLAPPED()
        self._overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            win32pipe.ConnectNamedPipe(self._pipe, self._overlapped)
        except pywintypes.error as e:
            if e.winerror == 536:  # ERROR_PIPE_CONNECTED
                self._connected = True
                win32api.CloseHandle(self._overlapped.hEvent)
                self._overlapped = None
                return True
            self._last_error = f"ConnectNamedPipe 失败: {e}"
            win32api.CloseHandle(self._overlapped.hEvent)
            self._overlapped = None
            try:
                win32file.CloseHandle(self._pipe)
            except Exception:
                log.debug("关闭管道句柄失败", exc_info=True)
            self._pipe = None
            return False
        return True

    def wait_for_connection(self, timeout_ms=5000):
        if self._connected:
            return True
        if self._overlapped is None:
            self._last_error = "未启动监听 (请先调用 start_listening)"
            return False

        wait_result = win32event.WaitForSingleObject(self._overlapped.hEvent, timeout_ms)
        h_event = self._overlapped.hEvent
        self._overlapped = None
        win32api.CloseHandle(h_event)

        if wait_result == win32con.WAIT_OBJECT_0:
            self._connected = True
            return True
        else:
            try:
                win32file.CloseHandle(self._pipe)
            except Exception:
                log.debug("关闭管道句柄失败", exc_info=True)
            self._pipe = None
            if wait_result == win32con.WAIT_TIMEOUT:
                self._last_error = "等待命名管道连接超时"
            else:
                self._last_error = f"等待命名管道失败 (code={wait_result})"
            return False

    def is_connected(self):
        return self._connected

    @property
    def last_error(self):
        return self._last_error

    def connect(self, timeout_ms=5000):
        if self._connected:
            return True
        if not self.start_listening():
            return False
        return self.wait_for_connection(timeout_ms)

    def disconnect(self):
        if self._pipe:
            try:
                win32pipe.DisconnectNamedPipe(self._pipe)
            except Exception:
                log.debug("断开命名管道失败", exc_info=True)
            try:
                win32file.CloseHandle(self._pipe)
            except Exception:
                log.debug("关闭管道句柄失败", exc_info=True)
            self._pipe = None
        self._connected = False
        self._thread = None

    def _send(self, cmd):
        if not self._connected or not self._pipe:
            return False
        try:
            data = (cmd + "\n").encode("utf-8")
            win32file.WriteFile(self._pipe, data)
            return True
        except Exception:
            # 调用方多半不看返回值：这里静默失败就等于"按键没发出去，连接也断了"，
            # 而界面上什么都不会显示。
            log.warning("发送指令失败，注入连接已断开: %s", cmd, exc_info=True)
            self.disconnect()
            return False

    def key_press(self, vk_or_name):
        return self._send(f"KEY_DOWN:{vk_or_name}")

    def key_release(self, vk_or_name):
        return self._send(f"KEY_UP:{vk_or_name}")

    def mouse_press(self, x, y, button="left"):
        return self._send(f"MOUSE_DOWN:{x},{y},{button}")

    def mouse_release(self, x, y, button="left"):
        return self._send(f"MOUSE_UP:{x},{y},{button}")

    def ping(self):
        if not self._pipe:
            return False
        try:
            win32file.WriteFile(self._pipe, b"PING\n")
            resp, _ = win32file.ReadFile(self._pipe, 32)
            return b"PONG" in resp
        except Exception:
            log.debug("PING 探活失败，判定连接已断", exc_info=True)
            self.disconnect()
            return False

    # --- 焦点伪装（#45）-----------------------------------------------------
    #
    # 实测结论（PLANNING.md §5.3）：Windows 并没有按前台把 XInput 清零，游戏
    # 手柄读得好好的，是**游戏自己在失焦时把自己暂停了**。所以要做的不是换一种
    # 送输入的方式，而是让游戏察觉不到自己进了后台。
    #
    # 默认关闭。注入本身不改变游戏的任何行为 —— 这一点是刻意保留的。

    def spoof_on(self, hwnd):
        """让游戏相信自己一直是前台窗口。

        hwnd 由这边传过去：注入方本来就知道自己盯的是哪个窗口，让 DLL 在进程
        内部去猜只会更差。
        """
        return self._send(f"SPOOF_ON:{int(hwnd)}")

    def spoof_off(self):
        """恢复真相。DLL 卸载时也会自动做一次。"""
        return self._send("SPOOF_OFF")

    def spoof_stats(self):
        """问 DLL 各个钩子被调用了多少次。拿不到返回 None。

        这是**外部探测器永远看不到的那一面**：游戏到底是靠轮询
        (GetForegroundWindow / GetActiveWindow / GetFocus) 还是靠窗口消息
        (WM_KILLFOCUS / WM_ACTIVATE / WM_ACTIVATEAPP) 察觉失焦。计数器在伪装
        关闭时也照常累加，所以这条在"只观察、不改行为"的模式下就能用。

        某一项一直是 0，就说明那条路径不是答案。
        """
        if not self._pipe:
            return None
        try:
            win32file.WriteFile(self._pipe, b"SPOOF_STATS\n")
            resp, _ = win32file.ReadFile(self._pipe, 256)
            return resp.decode("ascii", "replace").strip()
        except Exception:
            log.debug("SPOOF_STATS 读取失败", exc_info=True)
            self.disconnect()
            return None
