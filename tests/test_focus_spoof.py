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


@pytest.fixture
def client():
    """一个只记录指令、不碰管道的 HookClient。"""
    c = injector.HookClient.__new__(injector.HookClient)
    c._connected = True
    c._pipe = object()
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

    def test_command_names_match_the_dll(self):
        """字面量必须和 gbfr_hook.c 里的 _stricmp 分支一字不差。

        拼错不会报错，只会静静地什么都不做，然后在真机上浪费一次测试。
        """
        source = (REPO / "hook" / "gbfr_hook.c").read_text(encoding="utf-8")
        for command in ("SPOOF_ON", "SPOOF_OFF", "SPOOF_STATS"):
            assert f'_stricmp(line, "{command}")' in source, \
                f"{command} 在 DLL 里没有对应的分支"


class TestSpoofStats:
    def test_returns_the_dll_reply(self, client, monkeypatch):
        replies = {"read": b"STATS on=1 fg=42 active=0 focus=0 kill=3 act=3 actapp=1\n"}
        monkeypatch.setattr(injector.win32file, "WriteFile",
                            lambda p, d: None, raising=False)
        monkeypatch.setattr(injector.win32file, "ReadFile",
                            lambda p, n: (replies["read"], None), raising=False)
        assert client.spoof_stats().startswith("STATS on=1 fg=42")

    def test_returns_none_without_a_pipe(self, client):
        client._pipe = None
        assert client.spoof_stats() is None

    def test_a_broken_pipe_disconnects_instead_of_raising(self, client, monkeypatch):
        def boom(*a, **kw):
            raise OSError("pipe is gone")

        monkeypatch.setattr(injector.win32file, "WriteFile", boom, raising=False)
        calls = []
        client.disconnect = lambda: calls.append("disconnect")
        assert client.spoof_stats() is None
        assert calls == ["disconnect"], "断了就该把连接状态改掉"


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
