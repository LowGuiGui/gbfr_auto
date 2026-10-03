# -*- coding: utf-8 -*-
"""
DLL 注入 + 命名管道通信
用于将 gbfr_hook.dll 注入到目标游戏进程，并通过命名管道发送键鼠命令
"""

import os
import time

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

# 管道是 PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED 建的，于是**后续每一次读写也
# 必须自带 OVERLAPPED**。微软对 ReadFile 的说法没有余地：句柄是用
# FILE_FLAG_OVERLAPPED 开的，lpOverlapped 就 "must not be NULL"，否则"函数可能
# 错误地报告读操作已完成"。
#
# 说清楚原来那样写会怎样，免得后人以为这是洁癖：kernel32 在拿到 STATUS_PENDING
# 而 lpOverlapped 为 NULL 时，会退回去 WaitForSingleObject(hFile, INFINITE)。
# 于是同一时刻只有一个 I/O 的时候它**能用**，代价是两条：
#
#   - 那个等待是 INFINITE。DLL 不回话，探测器就永远挂在那儿，没有超时可言。
#   - 句柄上一旦同时有第二个 I/O，等到的可能是别人的完成 —— 这正是文档说的
#     "错误地报告操作已完成"。app 那边有看门狗和工作线程，这不是假设。
ERROR_IO_PENDING = 997

# 要跟 gbfr_hook.c 里的 BUF_SIZE 对得上。
BUF_SIZE = 256

# 等一条答复最多多久。DLL 卡住的时候要能报"没答复"，而不是永远挂在读上面。
REPLY_TIMEOUT_MS = 2000

# DLL 一连上管道就先写一行 HELLO。它不是任何一条指令的答复 —— 读答复的时候必须
# 跳过，否则第一次问什么都会拿到 HELLO。
GREETING = "HELLO"


def take_line(buffer):
    """从字节缓冲里切出一整行。返回 (行, 剩下的字节)；不够一行则行为 None。

    管道是字节模式（PIPE_TYPE_BYTE），一次读回来可能是半行，也可能是好几行粘在
    一起。两种都得处理：不然答复要么被截断，要么被后面粘着的东西带跑。
    """
    if not buffer:
        return None, b""
    index = buffer.find(b"\n")
    if index < 0:
        return None, buffer
    line = buffer[:index].decode("ascii", "replace").strip()
    return line, buffer[index + 1:]


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
# DLL 回的是一行：
#   STATS on=1 iat=3 sub=1 fg=42 active=0 focus=0 kill=3 act=3 actapp=1
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

    # iat / sub 说的是钩子**装上了没有**，和它被调用过几次是两回事。分开看，才能
    # 把"游戏不走这些 API"和"我们的钩子根本没装上"区分开 —— 两者的下一步完全不同，
    # 而全零的计数器长得一模一样。缺这两项就是老 DLL，那就不下这个结论。
    iat = stats.get("iat")
    sub = stats.get("sub")
    if iat is not None and sub is not None and iat == 0 and sub == 0:
        return ("not-installed",
                "No hook is in place at all: the IAT patch matched nothing and the "
                "window proc was never subclassed. Zero counters therefore say "
                "nothing about the game -- fix the install first. "
                "%TEMP%\\gbfr_hook.log records which of the two failed and why.")

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


def delivery_verdict(sent, stats):
    """指令到底有没有走到 DLL 手里。返回 (代号, 一行 ASCII 说明)；判断不了返回 None。

    Python 这边最多只能说"写调用返回了成功"，而那和"DLL 收到并执行了"不是同一个
    命题：管道收下了字节、DLL 却没解析出来，从这边看完全一样。cmds 是在管道**另
    一头**数出来的，所以它是唯一能把"我们没发"和"发了但没到"分开的证据。

    这正是 #45 那次的教训的一般形式：注入模式一旦坏掉，表现是脚本安静地什么都不
    做，而不是报错。
    """
    if not stats or "cmds" not in stats:
        return None
    cmds = stats.get("cmds", 0)
    bad = stats.get("bad", 0)
    if sent > 0 and cmds == 0:
        return ("not-delivered",
                f"We sent {sent} command(s) and the DLL executed none of them. "
                "The pipe took the bytes and nothing acted on them, so every "
                "keypress sent through inject mode is going nowhere -- and "
                "nothing anywhere would have said so.")
    if bad > 0:
        return ("garbled",
                f"The DLL could not parse {bad} line(s) it received. Commands "
                "are arriving damaged, or the two sides disagree on a name. "
                "%TEMP%\\gbfr_hook.log records each rejected line.")
    return ("delivered",
            f"The DLL executed {cmds} command(s) and rejected none.")


class HookClient:
    """命名管道服务端，等待注入的 DLL 连接并向其发送命令"""

    def __init__(self):
        self._pipe = None
        self._connected = False
        self._thread = None
        self._last_error = None
        self._overlapped = None
        # 字节管道的收包缓冲。一次读回来可能是半行，也可能是好几行粘在一起，
        # 读剩下的那半行必须留到下一次读，不能丢。
        self._rx = b""
        # 我们往管道上写成功了多少条。单独看它没有意义 —— 要跟 DLL 报回来的
        # cmds 对着看，才知道"发出去"和"到了"是不是同一回事。见 delivery_verdict。
        self._sent = 0

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
        self._rx = b""

    # --- 管道 I/O -----------------------------------------------------------
    #
    # 这一层是 2026-08-25 那次真机运行里"section 9 拿不到任何计数器"的原因所在。
    #
    # 真凶只有一个，而且是确定的：win32file.ReadFile 返回的是 (hr, data)，代码
    # 却按 (data, _) 解包。resp 拿到的是那个整数 hr，resp.decode(...) 抛
    # AttributeError，被 except 吞掉 —— 对外表现就是干净的一句"拿不到计数器"。
    # ping() 同病：`b"PONG" in 0` 抛 TypeError，于是活着的连接被判成死的。
    #
    # 另外两条不是那次的元凶，但都是真的，都得修：
    #
    #   - 异步句柄上按同步方式读写。它**看起来**能用（见文件头 ERROR_IO_PENDING
    #     处的说明），代价是没有超时，以及句柄上同时有第二个 I/O 时会串线。
    #   - 字节管道没有分行。一次读回来可能是半行，也可能是好几行粘在一起。
    #
    # 分清楚这三条要紧：把"看起来能用但很脆"说成"当时就是它坏的"，下一个人就会
    # 去查一个根本不存在的故障。

    def _overlapped_io(self, start, timeout_ms):
        """发起一次 overlapped I/O 并等它真正完成。返回字节数；失败或超时返回 None。

        start(ov) 负责发起，返回 pywin32 给回来的 hr。
        """
        if not self._pipe:
            return None
        try:
            ov = pywintypes.OVERLAPPED()
            ov.hEvent = win32event.CreateEvent(None, True, False, None)
        except Exception:
            log.debug("创建 OVERLAPPED 失败", exc_info=True)
            return None
        try:
            hr = start(ov)
            if hr not in (0, ERROR_IO_PENDING):
                self._last_error = f"管道 I/O 失败 (hr={hr})"
                return None
            if win32event.WaitForSingleObject(
                    ov.hEvent, timeout_ms) != win32con.WAIT_OBJECT_0:
                # 超时也不能直接走人：这次 I/O 还挂在管道和那块缓冲上，放着不管，
                # 它会在缓冲被回收之后才写进去，而下一次读也会拿到错位的数据。
                # CancelIo 只是"请求取消"，还得等它真的结束。
                try:
                    win32file.CancelIo(self._pipe)
                    win32file.GetOverlappedResult(self._pipe, ov, True)
                except Exception:
                    log.debug("取消超时的管道 I/O", exc_info=True)
                self._last_error = "等管道 I/O 超时"
                return None
            return win32file.GetOverlappedResult(self._pipe, ov, False)
        except Exception:
            log.debug("管道 I/O 异常", exc_info=True)
            return None
        finally:
            try:
                win32api.CloseHandle(ov.hEvent)
            except Exception:
                log.debug("关闭 OVERLAPPED 事件失败", exc_info=True)

    def _write_raw(self, data, timeout_ms=REPLY_TIMEOUT_MS):
        """往管道写一段字节。写不出去就等于连接已经没了。"""
        # WriteFile 返回 (errCode, nBytesWritten)，要的是第一个。
        ok = self._overlapped_io(
            lambda ov: win32file.WriteFile(self._pipe, data, ov)[0],
            timeout_ms) is not None
        if ok:
            self._sent += 1
        return ok

    @property
    def sent_count(self):
        """这个连接上写成功过多少条指令。delivery_verdict 的分母。"""
        return self._sent

    def _read_raw(self, timeout_ms):
        """从管道读一段字节。超时或出错返回 None。"""
        if not self._pipe:
            return None
        try:
            buf = win32file.AllocateReadBuffer(BUF_SIZE)
        except Exception:
            log.debug("分配读缓冲失败", exc_info=True)
            return None
        # ReadFile 返回 (hr, buffer)，要的是第一个。原来这里按 (data, _) 解包，
        # 于是每次都把那个整数 hr 当字节串用 —— 就是 section 9 的空手而归。
        count = self._overlapped_io(
            lambda ov: win32file.ReadFile(self._pipe, buf, ov)[0], timeout_ms)
        if not count:
            return None
        return bytes(buf[:count])

    def _await_reply(self, prefix, timeout_ms=REPLY_TIMEOUT_MS):
        """读到第一条以 prefix 开头的整行为止，中间的行丢掉。拿不到返回 None。

        丢是必须的：DLL 一连上管道就先写一行 HELLO，那不是任何指令的答复。原来
        把管道上紧接着的一段字节直接当答复，所以第一次问什么都会撞上 HELLO。
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            line, self._rx = take_line(self._rx)
            if line is not None:
                if line.startswith(prefix):
                    return line
                if line and line != GREETING:
                    log.debug("丢弃管道上一条不相干的行: %s", line)
                continue
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                self._last_error = f"等 {prefix} 答复超时"
                return None
            chunk = self._read_raw(remaining_ms)
            if not chunk:
                return None
            self._rx += chunk

    def _send(self, cmd):
        if not self._connected or not self._pipe:
            return False
        if self._write_raw((cmd + "\n").encode("utf-8")):
            return True
        # 调用方多半不看返回值：这里静默失败就等于"按键没发出去，连接也断了"，
        # 而界面上什么都不会显示。
        log.warning("发送指令失败，注入连接已断开: %s (%s)", cmd, self._last_error)
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

    def ping(self, timeout_ms=REPLY_TIMEOUT_MS):
        if not self._pipe:
            return False
        if not self._write_raw(b"PING\n"):
            log.debug("PING 发不出去，判定连接已断: %s", self._last_error)
            self.disconnect()
            return False
        # 只认 PONG 那一行。DLL 刚连上时发的 HELLO 也躺在管道里，原来那句
        # `b"PONG" in resp` 撞上它就会把一条活着的连接判成死的。
        if self._await_reply("PONG", timeout_ms) is None:
            log.debug("PING 没等到 PONG，判定连接已断: %s", self._last_error)
            self.disconnect()
            return False
        return True

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

    def spoof_watch(self, hwnd):
        """只观察：把窗口子类化装上，但**不**打开伪装。

        没有这一条，stage 1 根本测不出东西。子类化原来只在 SPOOF_ON 里装，而
        stage 1 从不发 SPOOF_ON —— kill / act / actapp 三个计数器于是在结构上
        永远是 0，"游戏靠窗口消息察觉失焦"这个答案压根没机会出现，判定只可能落
        在 polls 或 no-hooks-hit 上。那是一个只会给出一种答案的测量。

        装子类化本身不改变游戏行为：伪装关着的时候 my_WndProc 只计数，每条消息
        都照常转给原来的窗口过程。所以"注入本身什么都不改"这条仍然成立。
        """
        return self._send(f"SPOOF_WATCH:{int(hwnd)}")

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
        if not self._write_raw(b"SPOOF_STATS\n"):
            log.debug("SPOOF_STATS 发不出去: %s", self._last_error)
            self.disconnect()
            return None
        # 只认 STATS 开头的那一行。管道上还躺着 DLL 刚连上时写的 HELLO，原来是
        # 把紧接着的一段字节整个当答复，于是永远读到 HELLO 而不是计数器。
        return self._await_reply("STATS")
