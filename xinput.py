# -*- coding: utf-8 -*-
"""XInput 的最小 ctypes 封装 —— 只为回答一个问题。

**失焦的时候，Windows 会不会把手柄状态清零？**

这个问题值得单独一个模块，因为文档和实测互相矛盾，而 #45 整个方案选哪条路全看
它的答案：

微软在 `XInputEnable` 的文档里写着（Windows 10 or later）：

    Deprecated, as game controller input is automatically enabled/disabled by
    the system based on the application window focus.

而"disabled"在同一页的定义是：

    If enable is FALSE, XInput will only send neutral data in response to
    XInputGetState (all buttons up, axes centered, and triggers at 0).

合起来读就是：**失焦时系统会喂给进程一份中立的手柄数据**，跟游戏自己想不想收
没有关系。但微软自家 Q&A 上又有开发者实测说 `XInputGetState` 失焦后照样返回真
实数据，只有震动停了。两边对不上 —— 可能跟具体是哪个 xinput DLL 有关，也可能
跟 Windows 版本有关。

所以不能靠读文档定论，只能在 Howard 的机器上量一次。量法见 `sample_focus()`：
推着摇杆连续采样，同时记录"当时我们是不是前台进程"，最后交叉对比。

**这个测试不需要游戏在跑。** 只要有虚拟手柄就行。

本模块只依赖标准库，任何平台都能 import —— Windows 专有的东西全部延迟到函数
内部。这样 Linux 上跑得了单元测试，PyInstaller 也更容易静态分析到它。
"""

import ctypes
import os
import time
from ctypes import Structure, c_short, c_ubyte, c_ulong, c_ushort

ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167

# 从新到旧。1_4 是 Win8+ 系统自带的；1_3 来自 DirectX SDK 重发行包，很多游戏还
# 在用；9_1_0 是最老的兼容层。游戏用哪个不一定，而且**不同 DLL 的失焦行为可能
# 就是不一样的** —— 这正是文档和实测对不上的候选原因之一，所以逐个都要试。
DLL_CANDIDATES = ("xinput1_4.dll", "xinput1_3.dll", "XInput9_1_0.dll", "xinputuap.dll")

MAX_USER_COUNT = 4

# 摇杆推满是 32767。中立判定给一点余量：真手柄静止时也不会正好是 0，虚拟手柄
# 倒是会，但这个函数不该假设对面一定是虚拟的。
NEUTRAL_TOLERANCE = 4000
TRIGGER_TOLERANCE = 16


class XINPUT_GAMEPAD(Structure):
    _fields_ = [
        ("wButtons", c_ushort),
        ("bLeftTrigger", c_ubyte),
        ("bRightTrigger", c_ubyte),
        ("sThumbLX", c_short),
        ("sThumbLY", c_short),
        ("sThumbRX", c_short),
        ("sThumbRY", c_short),
    ]


class XINPUT_STATE(Structure):
    _fields_ = [("dwPacketNumber", c_ulong), ("Gamepad", XINPUT_GAMEPAD)]


class Reading(object):
    """一次采样的快照。

    刻意不直接传 ctypes 结构体出去：那是一块会被下次调用覆盖的内存，存进列表里
    再回头看就全是最后一次的值了。这个 bug 很难查，所以这里直接拷成普通属性。
    """

    __slots__ = ("packet", "buttons", "left_trigger", "right_trigger",
                 "lx", "ly", "rx", "ry")

    def __init__(self, packet, buttons, left_trigger, right_trigger, lx, ly, rx, ry):
        self.packet = packet
        self.buttons = buttons
        self.left_trigger = left_trigger
        self.right_trigger = right_trigger
        self.lx = lx
        self.ly = ly
        self.rx = rx
        self.ry = ry

    @classmethod
    def from_state(cls, state):
        pad = state.Gamepad
        return cls(int(state.dwPacketNumber), int(pad.wButtons),
                   int(pad.bLeftTrigger), int(pad.bRightTrigger),
                   int(pad.sThumbLX), int(pad.sThumbLY),
                   int(pad.sThumbRX), int(pad.sThumbRY))

    def __repr__(self):
        return (f"Reading(packet={self.packet} buttons=0x{self.buttons:04X} "
                f"L=({self.lx},{self.ly}) R=({self.rx},{self.ry}) "
                f"LT={self.left_trigger} RT={self.right_trigger})")


def is_neutral(reading):
    """所有按键抬起、摇杆居中、扳机归零 —— 也就是文档里 disabled 的定义。"""
    if reading is None:
        return True
    if reading.buttons != 0:
        return False
    if reading.left_trigger > TRIGGER_TOLERANCE or reading.right_trigger > TRIGGER_TOLERANCE:
        return False
    return all(abs(v) <= NEUTRAL_TOLERANCE
               for v in (reading.lx, reading.ly, reading.rx, reading.ry))


def describe(reading):
    """一行 ASCII 描述。探测器的输出必须是纯 ASCII，见 windows_probe 的模块注释。"""
    if reading is None:
        return "not connected"
    return (f"buttons=0x{reading.buttons:04X} "
            f"L=({reading.lx:+6d},{reading.ly:+6d}) "
            f"R=({reading.rx:+6d},{reading.ry:+6d}) "
            f"LT={reading.left_trigger:3d} RT={reading.right_trigger:3d}"
            + ("   NEUTRAL" if is_neutral(reading) else "   LIVE"))


def load_library(name):
    """载入一个 xinput DLL。失败返回 None —— 候选表里本来就有可能不存在的。"""
    try:
        return ctypes.WinDLL(name)
    except (OSError, AttributeError):
        # AttributeError: 非 Windows 上 ctypes 没有 WinDLL
        return None


def available_libraries(candidates=DLL_CANDIDATES):
    """候选表里哪些真的能载入。返回 [(名字, 句柄), ...]，保持候选表的顺序。"""
    found = []
    for name in candidates:
        dll = load_library(name)
        if dll is not None:
            found.append((name, dll))
    return found


def read(dll, index=0):
    """读一个手柄槽位。

    返回 Reading；槽位没接手柄返回 None。其他错误码也返回 None —— 对本模块要回答
    的问题来说，"读不到"和"没接"没有区别。
    """
    state = XINPUT_STATE()
    try:
        code = dll.XInputGetState(c_ulong(index), ctypes.byref(state))
    except (OSError, AttributeError):
        return None
    if code != ERROR_SUCCESS:
        return None
    return Reading.from_state(state)


def connected_slots(dll, count=MAX_USER_COUNT):
    """哪几个槽位有手柄。虚拟手柄插上以后通常落在 0。"""
    return [i for i in range(count) if read(dll, i) is not None]


def responding_slot(dll, slots, attempts=5, interval=0.1, sleep=time.sleep):
    """在推着摇杆的前提下，哪个槽位真的报告了非中立数据。

    **不能盲取 slots[0]。** ViGEm 拔掉设备不是同步的：前一段测试的手柄在系统里
    还没消失时，新插的会落到下一个槽位，而 0 号留着一个读数恒为中立的幽灵。真机
    上就是这样把一次测量作废的 —— `docs/test2/test a4` 那份报告里 slots=[0, 1]，
    读了 0 号，于是 120 个采样全中立，判定成 no-input，整段白跑。

    要在**摇杆已经推下去之后**调用，否则每个槽位都是中立的，什么也分不出来。
    多试几次是因为设备刚插上时状态未必立刻可读。

    找不到返回 None —— 这时候任何读数都不该被当成结论。
    """
    for _ in range(attempts):
        for index in slots:
            reading = read(dll, index)
            if reading is not None and not is_neutral(reading):
                return index
        sleep(interval)
    return None


# ---------------------------------------------------------------------------
# 前台判定
# ---------------------------------------------------------------------------

def _own_pid():
    return os.getpid()


def _user32():
    try:
        return ctypes.WinDLL("user32")
    except (OSError, AttributeError):
        return None


def foreground_window():
    """当前前台窗口的 hwnd。拿不到返回 None。"""
    user32 = _user32()
    if user32 is None:
        return None
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    return user32.GetForegroundWindow() or None


def foreground_pid():
    """当前前台窗口属于哪个进程。拿不到返回 None。"""
    user32 = _user32()
    if user32 is None:
        return None
    hwnd = foreground_window()
    if not hwnd:
        return None
    pid = c_ulong(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return int(pid.value) or None


def baseline_watcher(baseline_hwnd):
    """返回一个判定"前台还是不是当初那个窗口"的函数。

    为什么不直接比 PID：探测器多半是从终端里跑的，而控制台窗口属于
    conhost/Windows Terminal，**不属于我们的进程**。拿 PID 比，会得出"我们从来
    没在前台过"，整个测试就退化成一句 inconclusive。

    比 hwnd 就没这个问题：测试开始那一刻前台是哪个窗口，那就是基准，之后只问
    "还是不是它"。用户点走了没有，这个问题它答得准，而且不关心窗口归谁所有。
    """
    def check():
        fg = foreground_window()
        if fg is None:
            return None
        return fg == baseline_hwnd
    return check


def we_are_foreground():
    """前台窗口是不是我们自己的。

    比对 PID 而不是 hwnd：探测器可能是控制台、可能被打包、可能有不止一个窗口，
    PID 是唯一稳定的身份。拿不到前台信息时返回 None（"不知道"），调用方必须把
    这种采样丢掉而不是当成 False —— 否则会凭空造出"失焦"的证据。
    """
    fg = foreground_pid()
    if fg is None:
        return None
    return fg == _own_pid()


# ---------------------------------------------------------------------------
# 核心测量
# ---------------------------------------------------------------------------

class Sample(object):
    """一次采样：当时前台是不是基准窗口、是不是我们自己的进程，读到了什么。

    own 是对照列，不参与结论。它回答的是"这次测量里，我们这个进程有没有真的
    当过前台" —— 如果一次都没有，那 XInput 的失焦门（如果存在）可能自始至终都
    是关着的，结论要打折扣。见 focus_caveat()。
    """

    __slots__ = ("elapsed", "ours", "reading", "own")

    def __init__(self, elapsed, ours, reading, own=None):
        self.elapsed = elapsed
        self.ours = ours
        self.reading = reading
        self.own = own


def sample_focus(dll, index=0, seconds=8.0, interval=0.1, clock=time.monotonic,
                 sleep=time.sleep, focus=we_are_foreground, own=we_are_foreground):
    """推着摇杆连续采样，同时记录当时是不是前台。

    不去操纵焦点 —— `SetForegroundWindow` 有一堆前台锁的限制，成不成要看运气，
    失败了还悄无声息。让用户自己去点别的窗口，我们只**观察**，反而既可靠又诚实：
    每一条采样都自带"当时到底是不是前台"的记录，事后交叉对比就行。

    clock/sleep/focus 可注入，纯粹是为了单元测试能在 Linux 上跑。
    """
    samples = []
    start = clock()
    while True:
        elapsed = clock() - start
        if elapsed > seconds:
            break
        samples.append(Sample(elapsed, focus(), read(dll, index), own()))
        sleep(interval)
    return samples


def summarize(samples):
    """把采样交叉汇总成 #45 需要的那张表。

    纯函数，没有 ctypes、没有时间、没有 Windows —— 判定逻辑全在这里，所以判定
    逻辑可以在 Linux 上被完整测试。
    """
    buckets = {
        "focused_live": 0, "focused_neutral": 0,
        "unfocused_live": 0, "unfocused_neutral": 0,
        "unknown": 0, "own_process_foreground": 0,
    }
    for s in samples:
        if s.own:
            buckets["own_process_foreground"] += 1
        if s.ours is None:
            buckets["unknown"] += 1
            continue
        where = "focused" if s.ours else "unfocused"
        what = "neutral" if is_neutral(s.reading) else "live"
        buckets[f"{where}_{what}"] += 1
    return buckets


def verdict(buckets):
    """从汇总里读出结论。返回 (代号, 一行 ASCII 说明)。

    只在证据足够时下结论。样本不够就说不够 —— 这个测试存在的理由就是别人凭推理
    下了结论，我们不该重蹈覆辙。
    """
    focused = buckets["focused_live"] + buckets["focused_neutral"]
    unfocused = buckets["unfocused_live"] + buckets["unfocused_neutral"]

    if focused == 0:
        return ("inconclusive",
                "Never saw a focused sample -- the stick was never read while this "
                "window was in front.")
    if unfocused == 0:
        return ("inconclusive",
                "Never saw an unfocused sample -- you did not click away, so the "
                "question was not tested.")
    if buckets["focused_live"] == 0:
        return ("no-input",
                "The pad read neutral even while focused: the stick was not "
                "actually pushed, or the pad is not on this slot. Test invalid.")

    if buckets["unfocused_live"] == 0:
        return ("os-gate",
                "CONFIRMED: XInput went neutral whenever this process was not in "
                "front. Windows is the gate, not the game -- hooking "
                "GetForegroundWindow inside the game would not help. See PLANNING.md 5.1 hypothesis B.")
    if buckets["unfocused_neutral"] == 0:
        return ("no-os-gate",
                "XInput kept returning live data while unfocused. Windows is NOT "
                "gating on this machine, so the gate is inside the game. "
                "See PLANNING.md 5.1 hypothesis A.")
    return ("mixed",
            "Unfocused samples were partly live and partly neutral. Something is "
            "gating intermittently -- report the raw counts, do not guess.")


def focus_caveat(buckets, code):
    """结论要不要加注。不需要就返回 None。

    **这个函数第一版写反了，而且是真机结果回来之后才发现的。** 推理写在这里，
    免得再翻一次。

    从终端里跑探测器时，控制台窗口属于 conhost / Windows Terminal，不属于我们
    这个进程 —— 也就是说我们**从头到尾都不是前台**。那么：

      读到的全是 live（no-os-gate）：一个从来不是前台的进程照样读到了真实手柄
        数据。这恰恰是"不存在前台门"**最强**的证据，不是最弱的。第一版把它当成
        可疑，正好反了。
      失焦段全是中立（os-gate）：这才可疑。"聚焦"那一段是按窗口基准判的，不是按
        "我们是不是前台"判的，而我们从来都不是 —— 于是"因为失焦所以中立"和
        "一直都是中立"分不开。

    其余判定（inconclusive / no-input / mixed）本身就已经说明测量无效，再加一条
    注解只会稀释重点。
    """
    if buckets.get("own_process_foreground", 0) > 0:
        return None
    if code == "no-os-gate":
        return ("This process never owned the foreground window -- normal when the "
                "probe is run from a terminal. That makes this result STRONGER, not "
                "weaker: a process that was never in front still read live pad data, "
                "which is exactly what a foreground gate would have prevented.")
    if code == "os-gate":
        return ("WARNING: this process never owned the foreground window, so the "
                "focused rows were measured against whichever window was in front "
                "when the test started, not against us being in front. 'Neutral "
                "because unfocused' and 'neutral all along' cannot be told apart "
                "from this run. Re-run by double-clicking the packaged exe before "
                "trusting it.")
    return None
