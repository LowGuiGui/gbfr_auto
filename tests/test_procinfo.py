# -*- coding: utf-8 -*-
"""procinfo.py 的判定逻辑测试。

模块枚举本身要 Windows 才能跑，但"看到这些 DLL 意味着什么"是纯逻辑 —— 而结论
恰恰出在那一段。真机上只跑一次，所以判定必须在这里就是对的。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import procinfo  # noqa: E402

GAME = r"C:\Games\GBFR\granblue_fantasy_relink.exe"


def _paths(*names):
    return [GAME] + [rf"C:\Windows\System32\{n}" for n in names]


class TestModuleNames:
    def test_lowercases_and_strips_directories(self):
        assert procinfo.module_names([r"C:\Windows\System32\XInput1_4.dll"]) == \
            ["xinput1_4.dll"]

    def test_handles_an_empty_list(self):
        assert procinfo.module_names([]) == []


class TestClassifyModules:
    def test_finds_classic_xinput(self):
        found = procinfo.classify_modules(_paths("xinput1_3.dll"))
        assert found == [("XInput", "strong", ["xinput1_3.dll"])]

    def test_is_case_insensitive(self):
        """GetModuleFileNameExW 返回的大小写不受我们控制。"""
        found = procinfo.classify_modules([r"C:\W\S\XINPUT1_4.DLL"])
        assert found == [("XInput", "strong", ["xinput1_4.dll"])]

    def test_finds_winrt_gamepad(self):
        found = procinfo.classify_modules(_paths("Windows.Gaming.Input.dll"))
        assert [label for label, _s, _h in found] == ["Windows.Gaming.Input"]

    def test_reports_several_backends_at_once(self):
        found = procinfo.classify_modules(
            _paths("xinput1_4.dll", "dinput8.dll", "hid.dll"))
        assert [label for label, _s, _h in found] == \
            ["XInput", "DirectInput", "Raw Input / HID"]

    def test_hid_is_marked_weak(self):
        """hid.dll 什么进程都加载，不能当证据用。"""
        found = procinfo.classify_modules(_paths("hid.dll"))
        assert found == [("Raw Input / HID", "weak", ["hid.dll"])]

    def test_unrelated_modules_are_ignored(self):
        assert procinfo.classify_modules(_paths("kernel32.dll", "d3d12.dll")) == []

    def test_collects_every_matching_variant(self):
        found = procinfo.classify_modules(_paths("xinput1_4.dll", "xinput1_3.dll"))
        assert found[0][2] == ["xinput1_3.dll", "xinput1_4.dll"]


class TestBackendVerdict:
    def _verdict(self, *names):
        return procinfo.backend_verdict(procinfo.classify_modules(_paths(*names)))[0]

    def test_classic_xinput_means_the_self_test_applies(self):
        assert self._verdict("xinput1_4.dll") == "xinput"

    def test_winrt_means_the_self_test_does_not_transfer(self):
        assert self._verdict("Windows.Gaming.Input.dll") == "modern"

    def test_uwp_shim_counts_as_modern(self):
        """xinputuap 转发到 WGI，失焦行为要按 WGI 推理，不是经典 XInput。"""
        assert self._verdict("xinputuap.dll") == "modern"

    def test_gameinput_counts_as_modern(self):
        assert self._verdict("gameinput.dll") == "modern"

    def test_both_families_is_left_open(self):
        """Steam overlay 之类会往游戏里塞 xinput，不能因此断定游戏在读它。"""
        assert self._verdict("xinput1_4.dll", "Windows.Gaming.Input.dll") == "both"

    def test_directinput_alone_is_other(self):
        assert self._verdict("dinput8.dll") == "other"

    def test_only_hid_is_not_evidence(self):
        assert self._verdict("hid.dll") == "weak-only"

    def test_nothing_recognised(self):
        assert self._verdict("kernel32.dll") == "none"

    def test_every_explanation_is_ascii(self):
        """探测器输出必须纯 ASCII。"""
        cases = [(), ("hid.dll",), ("xinput1_4.dll",), ("dinput8.dll",),
                 ("Windows.Gaming.Input.dll",),
                 ("xinput1_4.dll", "Windows.Gaming.Input.dll")]
        for names in cases:
            found = procinfo.classify_modules(_paths(*names))
            procinfo.backend_verdict(found)[1].encode("ascii")


class TestErrorText:
    def test_names_access_denied(self):
        text = procinfo._last_error_text(procinfo.ERROR_ACCESS_DENIED)
        assert "access denied" in text

    def test_unknown_code_still_readable(self):
        assert "1234" in procinfo._last_error_text(1234)

    def test_is_ascii(self):
        for code in (5, 87, 299, 1234):
            procinfo._last_error_text(code).encode("ascii")


class TestPlatformBoundary:
    """CI 跑两遍：ubuntu 带桩，windows-latest **不带**桩。

    所以这里只能断言两个平台都成立的契约 —— "永远不抛异常，要么给结果要么给
    原因"。断言"在 Windows 上也返回 None"是错的，第一次提交就是这么挂的。
    """

    def test_pid_for_window_never_raises(self):
        result = procinfo.pid_for_window(12345)
        assert result is None or isinstance(result, int)

    def test_process_modules_gives_a_result_or_a_reason(self):
        paths, error, exe = procinfo.process_modules(4321)
        assert (paths is None) != (error is None), "要么拿到模块，要么拿到原因"
        if error is not None:
            assert error, "原因不能是空串"
            error.encode("ascii")
        else:
            assert isinstance(paths, list)

    def test_off_windows_says_so(self):
        if sys.platform.startswith("win"):
            pytest.skip("这条只描述非 Windows 上的行为")
        assert procinfo.process_modules(4321)[1] == "not running on Windows"
