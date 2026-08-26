# -*- coding: utf-8 -*-
"""焦点伪装的 Python 侧协议（#45）。

C 那半边只能在 Windows 上跑，Linux 这边能守住的是**协议**：发出去的指令字面量
对不对、失败路径会不会静默、断开之前有没有先把伪装关掉。

指令字面量值得单独测：DLL 那边是 `_stricmp` 逐条比字符串，拼错一个字母不会报错，
只会静静地什么都不做 —— 而真机上一次测试要十几分钟。
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load_injector():
    for name in ("win32file", "win32pipe", "win32api", "win32con", "win32event",
                 "win32security", "win32process", "pywintypes", "win32gui"):
        sys.modules.setdefault(name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location(
        "gbfr_injector", str(REPO / "hook" / "injector.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


injector = _load_injector()


class _FakeWin32:
    """照 pywin32 的**真实**签名做的最小桩。

    照真接口做，而不是照代码的期望做 —— 这两者不一致正是 2026-08-25 那次真机
    运行里 section 9 空手而归的原因：win32file.ReadFile 返回的是 (hr, data)，
    代码却按 (data, _) 解包。原来的测试把 ReadFile 桩成返回 (data, None)，
    照着错误的期望写的桩，于是它一路绿灯，还把这个 bug 焊在了原地。
    """

    WAIT_OBJECT_0 = 0

    class OVERLAPPED:
        def __init__(self):
            self.hEvent = None

    def __init__(self, chunks=(), write_raises=None):
        self.chunks = list(chunks)
        self.written = []
        self.reads_with_overlapped = 0
        self.writes_with_overlapped = 0
        self.calls_without_overlapped = 0
        self.write_raises = write_raises
        self._last_count = 0

    def CreateEvent(self, sa, manual, initial, name):
        return "event"

    def WaitForSingleObject(self, handle, timeout_ms):
        return self.WAIT_OBJECT_0

    def CloseHandle(self, handle):
        pass

    def AllocateReadBuffer(self, size):
        return bytearray(size)

    def ReadFile(self, pipe, buf, ol=None):
        if ol is None:
            self.calls_without_overlapped += 1
        else:
            self.reads_with_overlapped += 1
        chunk = self.chunks.pop(0) if self.chunks else b""
        buf[:len(chunk)] = chunk
        self._last_count = len(chunk)
        return (0, buf)                 # pywin32: (hr, data)

    def WriteFile(self, pipe, data, ol=None):
        if self.write_raises:
            raise self.write_raises
        if ol is None:
            self.calls_without_overlapped += 1
        else:
            self.writes_with_overlapped += 1
        self.written.append(bytes(data))
        self._last_count = len(data)
        return (0, len(data))           # pywin32: (errCode, nBytesWritten)

    def GetOverlappedResult(self, pipe, ol, wait):
        return self._last_count


@pytest.fixture
def win32(monkeypatch):
    """把 injector 的 win32 依赖换成上面那套忠实的桩。"""
    def _install(chunks=(), write_raises=None):
        fake = _FakeWin32(chunks, write_raises)
        monkeypatch.setattr(injector.pywintypes, "OVERLAPPED",
                            _FakeWin32.OVERLAPPED, raising=False)
        monkeypatch.setattr(injector.win32con, "WAIT_OBJECT_0", 0, raising=False)
        for module, names in (
            (injector.win32event, ("CreateEvent", "WaitForSingleObject")),
            (injector.win32api, ("CloseHandle",)),
            (injector.win32file, ("AllocateReadBuffer", "ReadFile", "WriteFile",
                                  "GetOverlappedResult")),
        ):
            for name in names:
                monkeypatch.setattr(module, name, getattr(fake, name), raising=False)
        monkeypatch.setattr(injector.win32file, "CancelIo",
                            lambda pipe: None, raising=False)
        return fake
    return _install


@pytest.fixture
def wired():
    """一个真的走 _send / _read_raw / _await_reply 的 HookClient。"""
    c = injector.HookClient.__new__(injector.HookClient)
    c._connected = True
    c._pipe = object()
    c._last_error = None
    c._rx = b""
    return c


@pytest.fixture
def client():
    """一个只记录指令、不碰管道的 HookClient。"""
    c = injector.HookClient.__new__(injector.HookClient)
    c._connected = True
    c._pipe = object()
    c._last_error = None
    c._rx = b""
    c.sent = []

    def fake_send(cmd):
        c.sent.append(cmd)
        return True

    c._send = fake_send
    return c


class TestSpoofCommands:
    def test_spoof_on_carries_the_hwnd(self, client):
        assert client.spoof_on(123456) is True
        assert client.sent == ["SPOOF_ON:123456"]

    def test_hwnd_is_sent_as_a_plain_integer(self, client):
        """DLL 那边用 _strtoui64 解析。传个 PyHANDLE 或 '0x1e240' 过去就废了。"""
        class HandleLike:
            def __int__(self):
                return 999

        client.spoof_on(HandleLike())
        assert client.sent == ["SPOOF_ON:999"]

    def test_spoof_off_takes_no_argument(self, client):
        client.spoof_off()
        assert client.sent == ["SPOOF_OFF"]

    def test_spoof_watch_carries_the_hwnd(self, client):
        assert client.spoof_watch(4242) is True
        assert client.sent == ["SPOOF_WATCH:4242"]

    def test_spoof_watch_is_not_spoof_on(self, client):
        """只观察就是只观察。发成 SPOOF_ON 会在用户以为什么都没变的时候开始骗
        游戏 —— 那是这一段最坏的结局。"""
        client.spoof_watch(4242)
        assert not any(c.startswith("SPOOF_ON") for c in client.sent)

    def test_command_names_match_the_dll(self):
        """字面量必须和 gbfr_hook.c 里的 _stricmp 分支一字不差。

        拼错不会报错，只会静静地什么都不做，然后在真机上浪费一次测试。
        """
        source = (REPO / "hook" / "gbfr_hook.c").read_text(encoding="utf-8")
        for command in ("SPOOF_ON", "SPOOF_OFF", "SPOOF_STATS", "SPOOF_WATCH"):
            assert f'_stricmp(line, "{command}")' in source, \
                f"{command} 在 DLL 里没有对应的分支"


class TestDllShape:
    """C 那半边在 Linux 上跑不了，但有几条形状是能守住的 —— 而它们各自都对应
    一个"测量结果看起来正常、其实不可能为真"的失败。"""

    SOURCE = (REPO / "hook" / "gbfr_hook.c").read_text(encoding="utf-8")

    def test_stats_reports_whether_the_hooks_went_in(self):
        """iat / sub 缺席的话，全零的计数器有两种读法："游戏不走这条路"和
        "钩子根本没装上" —— 两者的下一步完全相反。"""
        assert "iat=%ld sub=%d" in self.SOURCE

    def test_the_watch_branch_does_not_turn_spoofing_on(self):
        watch = self.SOURCE[self.SOURCE.index('_stricmp(line, "SPOOF_WATCH")'):]
        watch = watch[:watch.index('_stricmp(line, "SPOOF_ON")')]
        assert "subclass_window(hwnd)" in watch, "不装子类化就没有消息计数器"
        assert "InterlockedExchange(&g_spoofOn, 1)" not in watch, \
            "只观察的那条路径绝不能打开伪装"

    def test_the_read_loop_reads_past_what_it_already_has(self):
        """半行留在缓冲区开头，下一次 ReadFile 却仍从 buf[0] 读 —— 那就是把刚
        保住的半行盖掉。拆成两次到达的指令会被悄悄改坏，而不是报错。"""
        assert "ReadFile(g_hPipe, buf + used" in self.SOURCE
        assert "ReadFile(g_hPipe, buf, sizeof(buf) - 1" not in self.SOURCE


class TestSpoofStats:
    LINE = b"STATS on=1 iat=3 sub=1 fg=42 active=0 focus=0 kill=3 act=3 actapp=1\n"

    def test_returns_the_dll_reply(self, wired, win32):
        win32([b"HELLO\n", self.LINE])
        assert wired.spoof_stats().startswith("STATS on=1 iat=3")

    def test_it_asks_before_it_reads(self, wired, win32):
        fake = win32([self.LINE])
        wired.spoof_stats()
        assert fake.written == [b"SPOOF_STATS\n"]

    def test_returns_none_without_a_pipe(self, client):
        client._pipe = None
        assert client.spoof_stats() is None

    def test_a_broken_pipe_disconnects_instead_of_raising(self, wired, win32):
        win32(write_raises=OSError("pipe is gone"))
        calls = []
        wired.disconnect = lambda: calls.append("disconnect")
        assert wired.spoof_stats() is None
        assert calls == ["disconnect"], "断了就该把连接状态改掉"


class TestPywin32Contract:
    """守住 pywin32 的真实返回形状和异步句柄的调用约定。

    返回形状那一条是 2026-08-25 那次 `raw: None` 的**确定**原因，见
    gbfr-probe-report.txt 的 section 9。

    OVERLAPPED 那一条不是那次的元凶：lpOverlapped 为 NULL 时 kernel32 会退回去
    WaitForSingleObject(hFile, INFINITE)，所以句柄上只有一个 I/O 的时候它看起来
    是好的。守它是因为那个 INFINITE 意味着根本没有超时，而且句柄上一旦有第二个
    I/O 就会串线 —— 文档说的"错误地报告操作已完成"。
    """

    def test_read_takes_the_data_half_not_the_hr_half(self, wired, win32):
        """ReadFile 返回 (hr, data)。取错一半，拿到的是整数 0。"""
        win32([b"STATS on=0 iat=3 sub=1 fg=7\n"])
        got = wired._read_raw(50)
        assert isinstance(got, bytes), "拿到的应该是字节，不是那个 hr"
        assert got.startswith(b"STATS")

    def test_every_read_carries_an_overlapped(self, wired, win32):
        """句柄是 FILE_FLAG_OVERLAPPED 开的，微软文档：lpOverlapped
        "must not be NULL"，否则函数"可能错误地报告读操作已完成"。"""
        fake = win32([b"PONG\n"])
        wired._read_raw(50)
        assert fake.reads_with_overlapped == 1
        assert fake.calls_without_overlapped == 0

    def test_every_write_carries_an_overlapped(self, wired, win32):
        fake = win32()
        wired._write_raw(b"PING\n")
        assert fake.writes_with_overlapped == 1
        assert fake.calls_without_overlapped == 0


class TestReplyFraming:
    """字节管道上的分行。DLL 一连上就先发 HELLO，它不是任何指令的答复。"""

    def test_the_greeting_is_not_mistaken_for_a_reply(self, wired, win32):
        """原来的形状：管道上第一段字节就是 HELLO，于是问什么都拿到 HELLO，
        parse_stats 一看不是 STATS 就返回 None —— 报告上那句 `raw: None`。"""
        win32([b"HELLO\n", b"STATS on=0 iat=3 sub=1 fg=9\n"])
        assert wired.spoof_stats().startswith("STATS")

    def test_a_greeting_glued_to_the_reply_is_still_skipped(self, wired, win32):
        """两条挤在一次读里回来，也得挑出 STATS 那一条。"""
        win32([b"HELLO\nSTATS on=0 iat=3 sub=1 fg=9\n"])
        assert wired.spoof_stats().startswith("STATS")

    def test_a_reply_split_across_two_reads_is_reassembled(self, wired, win32):
        win32([b"STATS on=0 iat=3 ", b"sub=1 fg=9 kill=2\n"])
        stats = injector.parse_stats(wired.spoof_stats())
        assert stats["fg"] == 9 and stats["kill"] == 2

    def test_bytes_after_the_reply_survive_for_next_time(self, wired, win32):
        """粘在后面的半条不能丢：下一次读要从它接着来。"""
        win32([b"STATS on=0 fg=1\nSTATS on=0 fg=2\n"])
        assert "fg=1" in wired.spoof_stats()
        assert "fg=2" in wired.spoof_stats()

    def test_silence_gives_none_rather_than_a_wrong_answer(self, wired, win32):
        win32([])
        assert wired.spoof_stats() is None

    def test_ping_sees_pong_from_behind_the_greeting(self, wired, win32):
        """原来那句 `b"PONG" in resp` 撞上 HELLO，会把活着的连接判成死的。"""
        win32([b"HELLO\n", b"PONG\n"])
        assert wired.ping() is True

    def test_ping_without_an_answer_is_a_dead_connection(self, wired, win32):
        win32([b"HELLO\n"])
        wired.disconnect = lambda: None
        assert wired.ping() is False


class TestTakeLine:
    def test_splits_off_one_line_and_keeps_the_rest(self):
        line, rest = injector.take_line(b"STATS fg=1\nSTATS fg=2\n")
        assert line == "STATS fg=1"
        assert rest == b"STATS fg=2\n"

    def test_an_incomplete_line_is_held_back_whole(self):
        line, rest = injector.take_line(b"STATS fg=")
        assert line is None
        assert rest == b"STATS fg=", "半行必须原样留着，等后面的字节补齐"

    def test_trailing_cr_is_stripped(self):
        assert injector.take_line(b"PONG\r\n")[0] == "PONG"

    def test_empty_buffer(self):
        assert injector.take_line(b"") == (None, b"")
        assert injector.take_line(None) == (None, b"")


class TestWindowInputSurface:
    """WindowInput 的转发层：守住"注入之前不许伪装"和"断开之前先关伪装"。"""

    @pytest.fixture
    def wi(self):
        from window_input import WindowInput
        w = WindowInput()
        w._hwnd = 4242
        return w

    def _fake_client(self):
        calls = []

        class Fake:
            def spoof_on(self, hwnd):
                calls.append(("on", hwnd))
                return True

            def spoof_off(self):
                calls.append(("off",))
                return True

            def spoof_watch(self, hwnd):
                calls.append(("watch", hwnd))
                return True

            def spoof_stats(self):
                calls.append(("stats",))
                return "STATS on=0"

            def disconnect(self):
                calls.append(("disconnect",))

        return Fake(), calls

    def test_enable_passes_the_target_hwnd(self, wi):
        fake, calls = self._fake_client()
        wi._hook_client = fake
        assert wi.enable_focus_spoof() is True
        assert calls == [("on", 4242)]

    def test_enable_refuses_without_injection(self, wi):
        wi._hook_client = None
        assert wi.enable_focus_spoof() is False

    def test_enable_refuses_without_a_window(self, wi):
        fake, calls = self._fake_client()
        wi._hook_client = fake
        wi._hwnd = None
        assert wi.enable_focus_spoof() is False
        assert calls == [], "没有窗口就不该发指令"

    def test_disable_inject_stops_spoofing_first(self, wi):
        """顺序是硬要求：断开之后就没有通道再叫它恢复了。"""
        fake, calls = self._fake_client()
        wi._hook_client = fake
        wi.disable_inject()
        assert calls == [("off",), ("disconnect",)]

    def test_disable_inject_still_disconnects_if_spoof_off_throws(self, wi):
        calls = []

        class Grumpy:
            def spoof_off(self):
                raise RuntimeError("pipe already dead")

            def disconnect(self):
                calls.append("disconnect")

        wi._hook_client = Grumpy()
        wi.disable_inject()
        assert calls == ["disconnect"], "关伪装失败也必须断开，否则句柄泄漏"
        assert wi._hook_client is None

    def test_stats_without_injection_is_none(self, wi):
        wi._hook_client = None
        assert wi.focus_spoof_stats() is None

    def test_watch_arms_the_observer_without_spoofing(self, wi):
        """这一条是"只观察"模式能不能测出东西的全部：消息计数器靠窗口子类化，
        而子类化原来只在 enable_focus_spoof 里才装。"""
        fake, calls = self._fake_client()
        wi._hook_client = fake
        assert wi.watch_focus_events() is True
        assert calls == [("watch", 4242)]
        assert not any(c[0] == "on" for c in calls), "只观察不能打开伪装"

    def test_watch_refuses_without_injection(self, wi):
        wi._hook_client = None
        assert wi.watch_focus_events() is False

    def test_watch_refuses_without_a_window(self, wi):
        fake, calls = self._fake_client()
        wi._hook_client = fake
        wi._hwnd = None
        assert wi.watch_focus_events() is False
        assert calls == [], "没有窗口就不该发指令"


class TestParseStats:
    LINE = "STATS on=1 fg=42 active=0 focus=0 kill=3 act=3 actapp=1"

    def test_parses_every_counter(self):
        stats = injector.parse_stats(self.LINE)
        assert stats == {"on": 1, "fg": 42, "active": 0, "focus": 0,
                         "kill": 3, "act": 3, "actapp": 1}

    def test_tolerates_surrounding_whitespace(self):
        assert injector.parse_stats("  " + self.LINE + "\r\n  ")["fg"] == 42

    def test_rejects_anything_that_is_not_a_stats_line(self):
        for junk in ("PONG", "", None, "HELLO\n", "on=1 fg=2"):
            assert injector.parse_stats(junk) is None

    def test_skips_malformed_tokens_instead_of_dying(self):
        """管道上收到半截数据是完全可能的，不该让整段测试崩掉。"""
        stats = injector.parse_stats("STATS fg=7 garbage active=x kill=2")
        assert stats == {"fg": 7, "kill": 2}

    def test_no_usable_tokens_is_none(self):
        assert injector.parse_stats("STATS") is None


class TestStatsVerdict:
    def _verdict(self, **counters):
        base = {"on": 0, "fg": 0, "active": 0, "focus": 0,
                "kill": 0, "act": 0, "actapp": 0}
        base.update(counters)
        return injector.stats_verdict(base)[0]

    def test_polling_game(self):
        assert self._verdict(fg=120) == "polls"

    def test_message_driven_game(self):
        assert self._verdict(kill=4, actapp=4) == "messages"

    def test_both_paths(self):
        assert self._verdict(fg=90, kill=3) == "both"

    def test_nothing_intercepted_is_the_alarming_one(self):
        """两边都是 0 = IAT 补丁没打中。这是最需要立刻知道的情况。"""
        code, text = self._verdict(), injector.stats_verdict(
            {"fg": 0, "kill": 0})[1]
        assert code == "no-hooks-hit"
        assert "widening" in text

    def test_missing_stats(self):
        assert injector.stats_verdict(None)[0] == "no-data"

    def test_hooks_that_never_went_in_are_not_evidence_about_the_game(self):
        """装上了没有，和被调了几次，是两个问题。全零的计数器长得一模一样，
        但"游戏不走这条路"和"钩子没进去"的下一步完全相反。"""
        code, text = injector.stats_verdict(
            {"iat": 0, "sub": 0, "fg": 0, "kill": 0})
        assert code == "not-installed"
        assert "say nothing about the game" in text

    def test_a_working_install_with_zero_counters_still_blames_the_game(self):
        assert self._verdict(iat=3, sub=1) == "no-hooks-hit"

    def test_a_partial_install_is_not_called_uninstalled(self):
        """子类化装上了，IAT 没打中：消息计数器仍然是可信的。"""
        assert self._verdict(iat=0, sub=1, kill=3) == "messages"

    def test_an_older_dll_without_the_fields_keeps_the_old_verdict(self):
        """老 DLL 不报 iat/sub。缺了就当"不知道"，不硬下结论。"""
        assert injector.stats_verdict({"fg": 0, "kill": 0})[0] == "no-hooks-hit"

    def test_the_on_flag_does_not_affect_the_verdict(self):
        """on 只是状态，不是证据 —— 计数器在伪装关着时也照样累加。"""
        assert self._verdict(on=1, fg=5) == self._verdict(on=0, fg=5)

    def test_every_explanation_is_ascii(self):
        for counters in ({}, {"fg": 1}, {"kill": 1}, {"fg": 1, "kill": 1}):
            base = {"fg": 0, "active": 0, "focus": 0,
                    "kill": 0, "act": 0, "actapp": 0}
            base.update(counters)
            injector.stats_verdict(base)[1].encode("ascii")
        injector.stats_verdict(None)[1].encode("ascii")
