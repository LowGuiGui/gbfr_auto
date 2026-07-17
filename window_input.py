# -*- coding: utf-8 -*-
"""
窗口级输入模拟
使用 PostMessage/SendMessage 向指定窗口发送键盘鼠标消息
不抢占系统焦点，不影响用户操作其他程序
"""

import ctypes
from ctypes import wintypes

import win32con
import win32gui

user32 = ctypes.windll.user32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEMOVE = 0x0200

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010
MK_SHIFT = 0x0004
MK_CONTROL = 0x0008


def _makelparam(x, y):
    return ctypes.c_long((y << 16) | (x & 0xFFFF)).value


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


def key_press(hwnd, key):
    vk = _to_vk(key)
    lparam = 0x00000001
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam)


def key_release(hwnd, key):
    vk = _to_vk(key)
    lparam = 0xC0000001
    user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam)


def key_tap(hwnd, key):
    key_press(hwnd, key)
    key_release(hwnd, key)


def mouse_move(hwnd, x, y):
    lparam = _makelparam(x, y)
    user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)


def mouse_press(hwnd, x, y, button="left"):
    lparam = _makelparam(x, y)
    if button == "left":
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    elif button == "right":
        user32.PostMessageW(hwnd, WM_RBUTTONDOWN, MK_RBUTTON, lparam)
    elif button == "middle":
        user32.PostMessageW(hwnd, WM_MBUTTONDOWN, MK_MBUTTON, lparam)


def mouse_release(hwnd, x, y, button="left"):
    lparam = _makelparam(x, y)
    if button == "left":
        user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    elif button == "right":
        user32.PostMessageW(hwnd, WM_RBUTTONUP, 0, lparam)
    elif button == "middle":
        user32.PostMessageW(hwnd, WM_MBUTTONUP, 0, lparam)


def mouse_click(hwnd, x, y, button="left"):
    mouse_press(hwnd, x, y, button)
    mouse_release(hwnd, x, y, button)


class WindowInput:
    def __init__(self, hwnd_or_title=None):
        self._hwnd = None
        if hwnd_or_title is not None:
            self.set_target(hwnd_or_title)

    def set_target(self, hwnd_or_title):
        if isinstance(hwnd_or_title, int):
            self._hwnd = hwnd_or_title
        else:
            from window_capture import find_window
            self._hwnd = find_window(hwnd_or_title)

    @property
    def hwnd(self):
        return self._hwnd

    def is_ready(self):
        return self._hwnd is not None and win32gui.IsWindow(self._hwnd)

    def key_press(self, key):
        if self.is_ready():
            key_press(self._hwnd, key)

    def key_release(self, key):
        if self.is_ready():
            key_release(self._hwnd, key)

    def key_tap(self, key):
        if self.is_ready():
            key_tap(self._hwnd, key)

    def mouse_press(self, x, y, button="left"):
        if self.is_ready():
            mouse_press(self._hwnd, x, y, button)

    def mouse_release(self, x, y, button="left"):
        if self.is_ready():
            mouse_release(self._hwnd, x, y, button)

    def mouse_click(self, x, y, button="left"):
        if self.is_ready():
            mouse_click(self._hwnd, x, y, button)
