# -*- coding: utf-8 -*-
"""
纯 Python 实现的后台输入注入（无需 C 编译器 / 无需 DLL）

原理:
  1. 在目标进程中分配一段可执行内存
  2. 写入一小段 x64 shellcode（跳板）
  3. 每次按键时: 写入参数 → CreateRemoteThread 调用跳板
  4. 跳板在目标进程内调用 user32!keybd_event / mouse_event / SetCursorPos

适用: 游戏后台输入，不抢焦点（与 DLL 注入效果相同）
"""

import ctypes
import struct
import time
from ctypes import wintypes

import win32con
import win32gui
import win32process

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READ = 0x20


# x64 shellcode: 从 [RCX] 读取参数并调用目标函数
# 布局: RCX -> [bVk(4), bScan(4), dwFlags(4), dwExtraInfo(4), func_addr(8)]
# 对于 4 参数函数 (keybd_event / mouse_event):
#   ecx  = [rcx]       ; bVk / dwFlags
#   edx  = [rcx+4]     ; bScan / dx
#   r8d  = [rcx+8]     ; dwFlags / dy
#   r9d  = [rcx+12]    ; dwExtraInfo / dwFlags
#   call [rcx+16]      ; 目标函数
STUB_4ARG = bytes([
    0x8B, 0x01,             # mov    eax,[rcx]
    0x8B, 0x51, 0x04,       # mov    edx,[rcx+0x4]
    0x44, 0x8B, 0x41, 0x08, # mov    r8d,[rcx+0x8]
    0x44, 0x8B, 0x49, 0x0C, # mov    r9d,[rcx+0xC]
    0x48, 0x8B, 0x49, 0x10, # mov    rcx,[rcx+0x10]
    0xFF, 0xE1,              # jmp    rcx
])

# 3 参数函数 (SetCursorPos): [rcx] -> X, [rcx+4] -> Y, [rcx+8] -> func
STUB_2ARG = bytes([
    0x8B, 0x01,             # mov    eax,[rcx]
    0x8B, 0x51, 0x04,       # mov    edx,[rcx+0x4]
    0x48, 0x8B, 0x49, 0x08, # mov    rcx,[rcx+0x8]
    0xFF, 0xE1,              # jmp    rcx
])


class PureInjector:
    def __init__(self):
        self._h_process = None
        self._pid = None
        self._p_stub4 = None
        self._p_stub2 = None
        self._p_params = None

    def is_ready(self):
        return self._h_process is not None

    def attach(self, hwnd_or_pid):
        self.detach()
        if isinstance(hwnd_or_pid, int) and hwnd_or_pid > 65535:
            pid = hwnd_or_pid
        else:
            _, pid = win32process.GetWindowThreadProcessId(hwnd_or_pid)
        self._pid = pid
        self._h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not self._h_process:
            raise RuntimeError(f"OpenProcess failed (pid={pid})")
        self._p_stub4 = self._alloc_exec(STUB_4ARG)
        self._p_stub2 = self._alloc_exec(STUB_2ARG)
        self._p_params = kernel32.VirtualAllocEx(
            self._h_process, None, 256,
            MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        return True

    def detach(self):
        if self._h_process:
            for p in (self._p_stub4, self._p_stub2, self._p_params):
                if p:
                    kernel32.VirtualFreeEx(self._h_process, p, 0, MEM_RELEASE)
            kernel32.CloseHandle(self._h_process)
        self._h_process = None
        self._pid = None
        self._p_stub4 = None
        self._p_stub2 = None
        self._p_params = None

    def _alloc_exec(self, code_bytes):
        size = len(code_bytes)
        p = kernel32.VirtualAllocEx(
            self._h_process, None, size,
            MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not p:
            raise RuntimeError("VirtualAllocEx failed")
        written = ctypes.c_size_t(0)
        kernel32.WriteProcessMemory(
            self._h_process, p, code_bytes, size, ctypes.byref(written)
        )
        old_protect = ctypes.c_ulong(0)
        kernel32.VirtualProtectEx(
            self._h_process, p, size, PAGE_EXECUTE_READ, ctypes.byref(old_protect)
        )
        return p

    def _remote_call(self, stub_addr, param_bytes):
        kernel32.WriteProcessMemory(
            self._h_process, self._p_params, param_bytes,
            len(param_bytes), None
        )
        h_thread = kernel32.CreateRemoteThread(
            self._h_process, None, 0, stub_addr, self._p_params, 0, None
        )
        if not h_thread:
            return False
        kernel32.WaitForSingleObject(h_thread, 2000)
        kernel32.CloseHandle(h_thread)
        return True

    @staticmethod
    def _get_proc(dll_name, func_name):
        hmod = kernel32.GetModuleHandleW(dll_name)
        if not hmod:
            hmod = kernel32.LoadLibraryW(dll_name)
        return kernel32.GetProcAddress(hmod, func_name.encode("ascii"))

    def keybd_event(self, vk, scan=0, flags=0, extra=0):
        if not self._h_process:
            return False
        fn = self._get_proc("user32.dll", "keybd_event")
        params = struct.pack("<IIIIQ", vk & 0xFF, scan & 0xFF, flags & 0xFFFFFFFF,
                              extra & 0xFFFFFFFF, fn)
        return self._remote_call(self._p_stub4, params)

    def mouse_event(self, flags, dx=0, dy=0, data=0, extra=0):
        if not self._h_process:
            return False
        fn = self._get_proc("user32.dll", "mouse_event")
        params = struct.pack("<IIIIQ", flags & 0xFFFFFFFF, dx & 0xFFFFFFFF,
                              dy & 0xFFFFFFFF, data & 0xFFFFFFFF, fn)
        return self._remote_call(self._p_stub4, params)

    def set_cursor_pos(self, x, y):
        if not self._h_process:
            return False
        fn = self._get_proc("user32.dll", "SetCursorPos")
        params = struct.pack("<IIQ", x & 0xFFFFFFFF, y & 0xFFFFFFFF, fn)
        return self._remote_call(self._p_stub2, params)
