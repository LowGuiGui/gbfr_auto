# -*- coding: utf-8 -*-
"""提权的三种结果必须能被区分（#1）。

原来 run_as_admin() 返回一个 bool，而 False 同时表示两件相反的事：

    "已经用管理员重开了，本进程该正常退出"   -> exit 0 是对的
    "提权失败了"                             -> 必须非零

调用方两种都 exit(0)。更糟的是当时**根本不看 ShellExecuteW 的返回值**，所以
用户在 UAC 弹窗上点"否"和成功重开完全无法区分 —— 这才是"失败却报告成功"的
真正来源，退出码只是它的表现。
"""

import ctypes
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


@pytest.fixture
def shell(monkeypatch):
    """装一个假的 ShellExecuteW，并记录它收到了什么。"""
    calls = []

    class FakeShellExecute:
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return self.result

    fake = FakeShellExecute()
    fake.result = 42
    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake,
                        raising=False)
    monkeypatch.setattr(main, "is_admin", lambda: False)
    fake.calls = calls
    return fake


class TestAlreadyAdmin:
    def test_returns_already_and_does_not_relaunch(self, shell, monkeypatch):
        monkeypatch.setattr(main, "is_admin", lambda: True)
        assert main.run_as_admin() == main.ELEVATION_ALREADY
        assert shell.calls == [], "本来就是管理员，不该再去重开一次"


class TestRelaunched:
    def test_success_is_reported_as_relaunched(self, shell):
        shell.result = 42          # 任何 > 32 的值都是成功
        assert main.run_as_admin() == main.ELEVATION_RELAUNCHED

    def test_just_above_the_threshold(self, shell):
        shell.result = 33
        assert main.run_as_admin() == main.ELEVATION_RELAUNCHED

    def test_it_asks_for_runas(self, shell):
        main.run_as_admin()
        assert shell.calls[0][1] == "runas"


class TestFailed:
    def test_refused_uac_prompt_is_a_failure(self, shell):
        """SE_ERR_ACCESSDENIED。原来这条和成功重开返回完全一样的东西。"""
        shell.result = 5
        assert main.run_as_admin() == main.ELEVATION_FAILED

    def test_exactly_the_threshold_is_still_a_failure(self, shell):
        """文档说的是 > 32 才算成功，32 本身是 ERROR_DLL_NOT_FOUND。"""
        shell.result = 32
        assert main.run_as_admin() == main.ELEVATION_FAILED

    def test_zero_is_a_failure(self, shell):
        shell.result = 0
        assert main.run_as_admin() == main.ELEVATION_FAILED

    def test_none_is_treated_as_zero_not_as_success(self, shell):
        """ctypes 的指针返回值在空指针时是 None，不是 0。"""
        shell.result = None
        assert main.run_as_admin() == main.ELEVATION_FAILED

    def test_an_exception_is_a_failure(self, shell, monkeypatch):
        def boom(*a):
            raise OSError("shell32 is unhappy")

        monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", boom,
                            raising=False)
        assert main.run_as_admin() == main.ELEVATION_FAILED

    def test_the_reason_is_logged(self, shell, caplog):
        """启动器只看得到退出码，日志是唯一能说明"为什么"的地方。"""
        shell.result = 5
        with caplog.at_level("ERROR"):
            main.run_as_admin()
        assert any("5" in r.getMessage() for r in caplog.records)


class TestReturnTypeIsNotTruncated:
    def test_restype_is_pointer_wide(self, shell):
        """HINSTANCE 在 64 位上是指针宽度，默认 c_int 会截断高位。

        错误码都很小，截断后照样 <= 32，所以这条不是在防一个已知的错误 —— 它
        是在防把判断建立在截断行为上。
        """
        main.run_as_admin()
        assert shell.restype is ctypes.c_void_p


class TestThreeOutcomesAreDistinct:
    def test_no_two_outcomes_share_a_value(self):
        """这三个值的全部意义就是彼此不同。合并任意两个就退回原来的 bug。"""
        outcomes = {main.ELEVATION_ALREADY,
                    main.ELEVATION_RELAUNCHED,
                    main.ELEVATION_FAILED}
        assert len(outcomes) == 3

    def test_none_of_them_is_falsy(self):
        """曾经这里是 bool，于是 `if not run_as_admin()` 把两种结果混为一谈。

        全部为真值，任何人想再写 `if not run_as_admin()` 都会立刻发现不对。
        """
        assert all((main.ELEVATION_ALREADY,
                    main.ELEVATION_RELAUNCHED,
                    main.ELEVATION_FAILED))
