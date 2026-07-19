# -*- coding: utf-8 -*-
"""
窗口级输入模拟（两种模式）
  fallback : 切前台 + pynput 系统级模拟（游戏有效，会抢焦点）
  inject   : DLL 注入目标进程 + 命名管道（游戏后台有效，不抢焦点，需编译 hook/gbfr_hook.dll）
"""

import time
import ctypes
from ctypes import wintypes

import win32con
import win32gui
from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Controller as MouseController, Button

user32 = ctypes.windll.user32

_kc = KeyboardController()
_mc = MouseController()

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040


def _to_vk(key):
    if isinstance(key, int):
        return key
    key_lower = str(key).lower()
    if len(key_lower) == 1:
        return ord(key_lower.upper())
    special = {
        "enter": win32con.VK_RETURN,
        "return": win32con.VK_RETURN,
        "space": win32con.VK_SPACE,
        "esc": win32con.VK_ESCAPE,
        "escape": win32con.VK_ESCAPE,
        "tab": win32con.VK_TAB,
        "backspace": win32con.VK_BACK,
        "back": win32con.VK_BACK,
        "delete": win32con.VK_DELETE,
        "del": win32con.VK_DELETE,
        "insert": win32con.VK_INSERT,
        "home": win32con.VK_HOME,
        "end": win32con.VK_END,
        "left": win32con.VK_LEFT,
        "right": win32con.VK_RIGHT,
        "up": win32con.VK_UP,
        "down": win32con.VK_DOWN,
        "shift": win32con.VK_SHIFT,
        "ctrl": win32con.VK_CONTROL,
        "control": win32con.VK_CONTROL,
        "alt": win32con.VK_MENU,
        "menu": win32con.VK_MENU,
        "f1": win32con.VK_F1,
        "f2": win32con.VK_F2,
        "f3": win32con.VK_F3,
        "f4": win32con.VK_F4,
        "f5": win32con.VK_F5,
        "f6": win32con.VK_F6,
        "f7": win32con.VK_F7,
        "f8": win32con.VK_F8,
        "f9": win32con.VK_F9,
        "f10": win32con.VK_F10,
        "f11": win32con.VK_F11,
        "f12": win32con.VK_F12,
    }
    if key_lower in special:
        return special[key_lower]
    return ord(key_lower.upper())


def _to_pynput_key(key):
    if isinstance(key, Key):
        return key
    key_lower = str(key).lower()
    special = {
        "enter": Key.enter,
        "return": Key.enter,
        "space": Key.space,
        "esc": Key.esc,
        "escape": Key.esc,
        "tab": Key.tab,
        "backspace": Key.backspace,
        "back": Key.backspace,
        "delete": Key.delete,
        "del": Key.delete,
        "insert": Key.insert,
        "home": Key.home,
        "end": Key.end,
        "left": Key.left,
        "right": Key.right,
        "up": Key.up,
        "down": Key.down,
        "shift": Key.shift,
        "ctrl": Key.ctrl,
        "control": Key.ctrl,
        "alt": Key.alt,
        "menu": Key.alt,
        "caps_lock": Key.caps_lock,
    }
    for i in range(1, 13):
        special[f"f{i}"] = getattr(Key, f"f{i}")
    if key_lower in special:
        return special[key_lower]
    if len(key_lower) == 1:
        return key_lower
    return key


def _to_pynput_button(button):
    mapping = {
        "left": Button.left,
        "right": Button.right,
        "middle": Button.middle,
    }
    return mapping.get(button, Button.left)


def _sys_key(key, is_down):
    pkey = _to_pynput_key(key)
    if is_down:
        _kc.press(pkey)
    else:
        _kc.release(pkey)


def _sys_mouse(x, y, button, is_down):
    _mc.position = (x, y)
    pbtn = _to_pynput_button(button)
    if is_down:
        _mc.press(pbtn)
    else:
        _mc.release(pbtn)


class WindowInput:
    MODE_FALLBACK = "fallback"
    MODE_INJECT = "inject"

    def __init__(self, hwnd_or_title=None):
        self._hwnd = None
        self._mode = self.MODE_FALLBACK
        self._hook_client = None
        if hwnd_or_title is not None:
            self.set_target(hwnd_or_title)

    def set_target(self, hwnd_or_title):
        if hwnd_or_title is None:
            self._hwnd = None
            return
        if isinstance(hwnd_or_title, int):
            self._hwnd = hwnd_or_title
        else:
            from window_capture import find_window
            self._hwnd = find_window(hwnd_or_title)

    @property
    def hwnd(self):
        return self._hwnd

    def is_ready(self):
        if self._hwnd is None or not win32gui.IsWindow(self._hwnd):
            return False
        if self._mode == self.MODE_INJECT:
            return self._hook_client is not None and self._hook_client.is_connected()
        return True

    def has_window(self):
        return self._hwnd is not None and win32gui.IsWindow(self._hwnd)

    def _bring_to_front(self):
        try:
            if win32gui.IsIconic(self._hwnd):
                win32gui.ShowWindow(self._hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(self._hwnd)
            time.sleep(0.05)
        except Exception:
            pass

    def _screen_pos(self, x, y):
        rect = win32gui.GetWindowRect(self._hwnd)
        return rect[0] + x, rect[1] + y

    def key_press(self, key):
        if not self.is_ready():
            return
        if self._mode == self.MODE_INJECT:
            self._hook_client.key_press(_to_vk(key))
        else:
            self._bring_to_front()
            _sys_key(key, True)

    def key_release(self, key):
        if not self.is_ready():
            return
        if self._mode == self.MODE_INJECT:
            self._hook_client.key_release(_to_vk(key))
        else:
            _sys_key(key, False)

    def key_tap(self, key):
        self.key_press(key)
        time.sleep(0.05)
        self.key_release(key)

    def mouse_press(self, x, y, button="left"):
        if not self.is_ready():
            return
        if self._mode == self.MODE_INJECT:
            sx, sy = self._screen_pos(x, y)
            self._hook_client.mouse_press(sx, sy, button)
        else:
            self._bring_to_front()
            sx, sy = self._screen_pos(x, y)
            _sys_mouse(sx, sy, button, True)

    def mouse_release(self, x, y, button="left"):
        if not self.is_ready():
            return
        if self._mode == self.MODE_INJECT:
            sx, sy = self._screen_pos(x, y)
            self._hook_client.mouse_release(sx, sy, button)
        else:
            sx, sy = self._screen_pos(x, y)
            _sys_mouse(sx, sy, button, False)

    def mouse_click(self, x, y, button="left"):
        self.mouse_press(x, y, button)
        time.sleep(0.05)
        self.mouse_release(x, y, button)

    def enable_fallback(self):
        self._mode = self.MODE_FALLBACK

    @property
    def mode(self):
        return self._mode

    def enable_inject(self, dll_path=None, progress_cb=None):
        import os
        import sys
        import time as _time
        from hook.injector import (
            HookClient, inject_dll, hwnd_to_pid,
        )

        def _log(msg):
            if progress_cb:
                progress_cb(msg)

        if self._mode == self.MODE_INJECT and self._hook_client is not None:
            if self._hook_client.is_connected():
                _log("注入模式已就绪，无需重复启用")
                return True
            self._hook_client.disconnect()

        if self._hwnd is None:
            raise RuntimeError("请先设置目标窗口 (set_target)")

        _log(f"目标窗口句柄: {self._hwnd}")

        pid = hwnd_to_pid(self._hwnd)
        if not pid:
            raise RuntimeError(f"无法获取窗口 PID (hwnd={self._hwnd})")
        _log(f"目标进程 PID: {pid}")

        if dll_path is None:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0])) \
                if getattr(sys, "frozen", False) \
                else os.path.dirname(os.path.abspath(__file__))
            dll_path = os.path.join(base_dir, "hook", "gbfr_hook.dll")

        if not os.path.exists(dll_path):
            raise FileNotFoundError(
                f"找不到 gbfr_hook.dll: {dll_path}\n"
                f"请运行 hook/build.bat 编译 DLL"
            )
        _log(f"DLL 路径: {dll_path}")

        self._hook_client = HookClient()
        _log("正在创建命名管道服务器...")
        if not self._hook_client.start_listening():
            err = self._hook_client.last_error
            self._hook_client = None
            raise RuntimeError(f"创建命名管道失败: {err or '未知错误'}")
        _log("命名管道服务器已启动，开始监听连接...")

        _log("正在注入 DLL 到目标进程...")
        inject_dll(pid, dll_path)
        _log("DLL 注入完成，正在等待管道连接...")

        if not self._hook_client.wait_for_connection(timeout_ms=5000):
            err = self._hook_client.last_error
            self._hook_client.disconnect()
            self._hook_client = None
            raise RuntimeError(
                f"DLL 注入成功，但命名管道连接失败: {err or '未知错误'}\n"
                f"(DLL 可能未正确启动，或管道名称不匹配)"
            )
        _log("命名管道连接成功，注入模式已就绪")
        self._mode = self.MODE_INJECT
        return True

    def disable_inject(self):
        if self._hook_client:
            self._hook_client.disconnect()
            self._hook_client = None
        self._mode = self.MODE_FALLBACK
