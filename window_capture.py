# -*- coding: utf-8 -*-
"""
窗口截图工具
支持按窗口标题查找并截取指定窗口区域
即使窗口被切换到后台也能截图
"""

import ctypes
from ctypes import wintypes

import pyautogui
import win32con
import win32gui
from PIL import Image

from applog import get_logger
from opencv import is_blank_frame

log = get_logger(__name__)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 2


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_ulong),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_short),
        ("biBitCount", ctypes.c_short),
        ("biCompression", ctypes.c_ulong),
        ("biSizeImage", ctypes.c_ulong),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_ulong),
        ("biClrImportant", ctypes.c_ulong),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def list_window_titles():
    titles = []

    def _enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                titles.append(buf.value)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    return titles


def find_window(title_substring):
    result = {"hwnd": None}
    target = title_substring.lower()

    def _enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if target in buf.value.lower():
                    result["hwnd"] = hwnd
                    return False
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    return result["hwnd"]


def get_window_rect(hwnd):
    rect = RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def _capture_printwindow(hwnd, region=None):
    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    if region is not None:
        x_off, y_off, w, h = region
        src_x = x_off
        src_y = y_off
        width = min(w, width - x_off)
        height = min(h, height - y_off)
    else:
        src_x = 0
        src_y = 0

    if width <= 0 or height <= 0:
        return None

    hwndDC = win32gui.GetWindowDC(hwnd)
    hdcMem = gdi32.CreateCompatibleDC(hwndDC)
    hBitmap = gdi32.CreateCompatibleBitmap(hwndDC, width, height)
    hOld = gdi32.SelectObject(hdcMem, hBitmap)

    gdi32.BitBlt(hdcMem, 0, 0, width, height, hwndDC, src_x, src_y, win32con.SRCCOPY)

    ret = user32.PrintWindow(hwnd, hdcMem, PW_RENDERFULLCONTENT)

    img = None
    if ret:
        bmpinfo = BITMAPINFO()
        bmpinfo.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmpinfo.bmiHeader.biWidth = width
        bmpinfo.bmiHeader.biHeight = -height
        bmpinfo.bmiHeader.biPlanes = 1
        bmpinfo.bmiHeader.biBitCount = 32
        bmpinfo.bmiHeader.biCompression = 0

        buf_size = width * height * 4
        buf = (ctypes.c_ubyte * buf_size)()
        got_lines = gdi32.GetDIBits(
            hdcMem,
            hBitmap,
            0,
            height,
            buf,
            ctypes.byref(bmpinfo),
            0,
        )

        if got_lines > 0:
            raw_data = bytes(buf)
            img = Image.frombytes(
                "RGBA",
                (width, height),
                raw_data,
                "raw",
                "BGRA",
                0,
                1,
            ).convert("RGB")

    gdi32.SelectObject(hdcMem, hOld)
    gdi32.DeleteObject(hBitmap)
    gdi32.DeleteDC(hdcMem)
    win32gui.ReleaseDC(hwnd, hwndDC)

    return img


def _bring_to_front_and_capture(hwnd, region=None):
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # Windows 会按规则拒绝抢焦点，这是常态而不是故障，所以只记 debug。
        log.debug("置顶窗口失败 (hwnd=%s)，继续截图", hwnd, exc_info=True)

    import time
    time.sleep(0.2)

    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    if region is not None:
        x_off, y_off, w, h = region
        left += x_off
        top += y_off
        width = min(w, width - x_off)
        height = min(h, height - y_off)

    if width <= 0 or height <= 0:
        return None

    return pyautogui.screenshot(region=(left, top, width, height))


def capture_window(hwnd_or_title, region=None):
    hwnd = hwnd_or_title
    if isinstance(hwnd_or_title, str):
        hwnd = find_window(hwnd_or_title)
        if hwnd is None:
            return None

    img = _capture_printwindow(hwnd, region)
    if img is not None and not is_blank_frame(img):
        return img

    if img is not None:
        # PrintWindow 对 D3D 窗口经常"成功"返回一张全黑位图。原来的代码只在返回
        # None 时才回退，于是黑帧被当成有效画面一路传下去 —— 而低纹理模板配全黑
        # 画面会拿到 1.0 满分，最后是一次高置信度误判，不是"认不出页面"。
        log.warning("PrintWindow 返回空白帧 (hwnd=%s)，改用前台截图", hwnd)

    return _bring_to_front_and_capture(hwnd, region)


def capture(title_or_hwnd=None):
    if title_or_hwnd is None:
        return pyautogui.screenshot()
    return capture_window(title_or_hwnd)
