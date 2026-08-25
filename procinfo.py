# -*- coding: utf-8 -*-
"""顺着窗口找到进程，看它加载了哪些输入相关的 DLL。

回答 #45 的第二个问题：**游戏到底用哪套 API 读手柄。**

为什么这件事重要：`xinput.py` 量的是"Windows 会不会在失焦时把 XInput 清零"。
但那个结论只有在游戏确实走 XInput 的时候才适用。如果它走的是
`Windows.Gaming.Input` 或者 DirectInput，那 XInput 的测量结果对它毫无意义，
修法也完全不同（见 PLANNING.md §5.1 的三行假设表）。

**为什么是看运行时加载的模块，而不是解析 PE 导入表：** WinRT 那套（WGI）是靠
`RoGetActivationFactory` 按类名字符串激活的，`Windows.Gaming.Input.dll` 压根不会
出现在导入表里 —— 但它会在运行时被加载进来。看模块列表两种都能抓到，解析导入表
反而会漏掉最想抓的那一种。

代价是需要能打开目标进程。游戏若以管理员身份运行（Reloaded-II 常见）而探测器
不是，`OpenProcess` 会被拒 —— 那种情况下如实报告并让用户以管理员再跑一次，不要
假装什么都没发生。

只依赖标准库，任何平台都能 import：Windows 专有的东西全在函数内部。
"""

import ctypes
import ntpath

# OpenProcess 需要的权限。刻意用 LIMITED_INFORMATION 而不是 QUERY_INFORMATION：
# 前者对受保护/高完整性进程的成功率更高，而我们只要模块列表。
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010

LIST_MODULES_ALL = 0x03

ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
ERROR_PARTIAL_COPY = 299

MAX_PATH_LONG = 32768

# (显示名, 证据强度, 模块名...)
#
# 强度不是装饰。hid.dll 几乎每个进程都会加载 —— 拿它当"游戏在用 RawInput"的
# 证据是错的，所以标成 weak，报告里也照实说。
INPUT_BACKENDS = (
    ("XInput", "strong", (
        "xinput1_4.dll", "xinput1_3.dll", "xinput1_2.dll",
        "xinput1_1.dll", "xinput9_1_0.dll",
    )),
    # xinputuap 是 XInput 的 UWP 实现，内部转发到 WGI —— 见到它意味着失焦行为
    # 要按 WGI 那一套推理，而不是按经典 XInput。
    ("XInput (UWP shim, forwards to WGI)", "strong", ("xinputuap.dll",)),
    ("Windows.Gaming.Input", "strong", ("windows.gaming.input.dll",)),
    ("GameInput", "strong", ("gameinput.dll",)),
    ("DirectInput", "strong", ("dinput8.dll", "dinput.dll")),
    ("Raw Input / HID", "weak", ("hid.dll",)),
)

_XINPUT_CLASSIC = "XInput"
_MODERN = ("XInput (UWP shim, forwards to WGI)", "Windows.Gaming.Input", "GameInput")


def _last_error_text(code):
    """把 Win32 错误码翻成一行 ASCII。"""
    known = {
        ERROR_ACCESS_DENIED: "access denied",
        ERROR_INVALID_PARAMETER: "invalid parameter (process gone?)",
        ERROR_PARTIAL_COPY: "partial copy (32/64-bit mismatch?)",
    }
    return f"error {code} ({known.get(code, 'see winerror.h')})"


def pid_for_window(hwnd):
    """窗口属于哪个进程。拿不到返回 None。"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except (OSError, AttributeError):
        return None
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return int(pid.value) or None


def process_image_path(handle, kernel32):
    """进程的完整 exe 路径。失败返回 None —— 这是锦上添花，不是必需品。"""
    size = ctypes.c_ulong(MAX_PATH_LONG)
    buf = ctypes.create_unicode_buffer(MAX_PATH_LONG)
    try:
        ok = kernel32.QueryFullProcessImageNameW(
            handle, ctypes.c_ulong(0), buf, ctypes.byref(size))
    except (OSError, AttributeError):
        return None
    return buf.value if ok else None


def process_modules(pid):
    """列出进程加载的所有模块的完整路径。

    返回 (paths, error, exe_path)。成功时 error 是 None；失败时 paths 是 None
    且 error 是一行 ASCII 说明。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (OSError, AttributeError):
        return None, "not running on Windows", None

    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        code = ctypes.get_last_error()
        hint = ""
        if code == ERROR_ACCESS_DENIED:
            hint = " -- the game is running at a higher privilege level than this probe"
        return None, f"OpenProcess failed: {_last_error_text(code)}{hint}", None

    try:
        exe = process_image_path(ctypes.c_void_p(handle), kernel32)
        paths, error = _enum_modules(ctypes.c_void_p(handle), kernel32)
        return paths, error, exe
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _enum_modules(handle, kernel32):
    """EnumProcessModulesEx 的两趟调用。

    HMODULE 是指针，64 位上是 8 字节 —— 用 DWORD 数组接会把高位截掉，得到一堆
    垃圾句柄。所以数组元素必须是 c_void_p。
    """
    enum = getattr(kernel32, "K32EnumProcessModulesEx", None)
    get_name = getattr(kernel32, "K32GetModuleFileNameExW", None)
    if enum is None or get_name is None:
        try:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
        except (OSError, AttributeError):
            return None, "neither kernel32 K32* nor psapi is available"
        enum = getattr(psapi, "EnumProcessModulesEx", None)
        get_name = getattr(psapi, "GetModuleFileNameExW", None)
        if enum is None or get_name is None:
            return None, "EnumProcessModulesEx is not available"

    count = 256
    for _attempt in range(3):
        array = (ctypes.c_void_p * count)()
        needed = ctypes.c_ulong(0)
        ok = enum(handle, ctypes.byref(array), ctypes.sizeof(array),
                  ctypes.byref(needed), LIST_MODULES_ALL)
        if not ok:
            return None, f"EnumProcessModulesEx failed: {_last_error_text(ctypes.get_last_error())}"
        returned = needed.value // ctypes.sizeof(ctypes.c_void_p)
        if returned <= count:
            break
        count = returned + 32          # 又长出来一些也不至于再转一圈
    else:
        return None, "module list kept growing; gave up"

    paths = []
    buf = ctypes.create_unicode_buffer(MAX_PATH_LONG)
    for i in range(min(returned, count)):
        if not array[i]:
            continue
        if get_name(handle, ctypes.c_void_p(array[i]), buf, MAX_PATH_LONG):
            paths.append(buf.value)
    return paths, None


# ---------------------------------------------------------------------------
# 判定 —— 纯函数，Linux 上可完整测试
# ---------------------------------------------------------------------------

def module_names(paths):
    """完整路径 -> 小写文件名。比较一律在这个形式上做。

    用 ntpath 而不是 os.path：路径全部来自 GetModuleFileNameExW，永远是 Windows
    形式，而 os.path 在 Linux 上不把反斜杠当分隔符 —— 于是整条判定链在开发机上
    就退化成"什么都匹配不上"，还测不出来。这不是假设，是写完第一版就撞上的。
    """
    return [ntpath.basename(p).lower() for p in paths]


def classify_modules(paths, table=INPUT_BACKENDS):
    """哪些输入后端出现了。

    返回 [(显示名, 强度, [命中的模块名...]), ...]，保持 table 的顺序，只含有
    命中的项。
    """
    present = set(module_names(paths))
    found = []
    for label, strength, wanted in table:
        hits = sorted(name for name in wanted if name in present)
        if hits:
            found.append((label, strength, hits))
    return found


def backend_verdict(found):
    """从命中结果读出对 #45 的意义。返回 (代号, 一行 ASCII 说明)。

    刻意不"选一个赢家"。Steam overlay、各种录屏工具都会往游戏里塞 xinput，所以
    同时看到好几套是常态。报告全部，说明每一种意味着什么，让人去判断。
    """
    strong = [label for label, strength, _hits in found if strength == "strong"]
    if not strong:
        weak = [label for label, _s, _h in found]
        if weak:
            return ("weak-only",
                    "Only weak evidence (" + ", ".join(weak) + "). hid.dll is loaded "
                    "by almost everything, so it proves nothing on its own.")
        return ("none",
                "No input DLL recognised. The game may resolve them lazily, or may "
                "read devices through a path this table does not cover.")

    modern = [label for label in strong if label in _MODERN]
    classic = _XINPUT_CLASSIC in strong

    if classic and not modern:
        return ("xinput",
                "Classic XInput only. The xinput focus self-test in this report "
                "applies directly to the game.")
    if modern and not classic:
        return ("modern",
                "Uses " + ", ".join(modern) + ". The classic-XInput self-test does "
                "NOT transfer -- these have their own focus behaviour.")
    if modern and classic:
        return ("both",
                "Both classic XInput and " + ", ".join(modern) + " are loaded. "
                "Another overlay may have injected one of them; which one the game "
                "actually reads is still open.")
    return ("other",
            "Found " + ", ".join(strong) + " but no XInput-family module.")
