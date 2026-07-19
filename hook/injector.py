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

PIPE_NAME = r"\\.\pipe\gbfr_hook"


def hwnd_to_pid(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
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
                pass
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
                pass
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
                pass
            try:
                win32file.CloseHandle(self._pipe)
            except Exception:
                pass
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
            self.disconnect()
            return False
