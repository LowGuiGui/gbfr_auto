# -*- coding: utf-8 -*-
"""#2 —— 看门狗和注入线程必须对"现在到底是哪个模式"有一致的说法。

原来的形状：看门狗 15 秒后把界面切回兼容模式，但工作线程是 daemon、没人能取消。
线程要是随后才成功，钩子就是活的，而界面显示兼容模式 —— 两条输入路径同时存在，
且没有任何地方看得出哪条是真的。

这里借用真实的 _on_inject_finished，因为判断全在那一个方法里。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class FakeOption:
    def __init__(self, disable_raises=None):
        self.calls = []
        self._disable_raises = disable_raises

    def disable_inject_mode(self):
        self.calls.append("disable_inject")
        if self._disable_raises:
            raise self._disable_raises

    def set_fallback_mode(self):
        self.calls.append("set_fallback")


class FakeApp:
    """借用真实方法，避开需要 Tk root 的 App 构造。"""

    _on_inject_finished = main.App._on_inject_finished

    def __init__(self, wanted, attempt_option=None):
        self._inject_wanted = wanted
        self._option = attempt_option or FakeOption()
        self.messages = []
        self.enabled = []

    def log(self, msg):
        self.messages.append(msg)

    def _on_inject_enabled(self, log_on_switch):
        self.enabled.append(log_on_switch)


class TestResultStillWanted:
    """没超时的正常路径不能被这次修复弄坏。"""

    def test_success_enables_inject_mode(self):
        app = FakeApp(wanted=7)
        app._on_inject_finished(True, attempt=7, log_on_switch=True)
        assert app.enabled == [True]
        assert app._option.calls == [], "还想要的结果不该被拆掉"

    def test_failure_does_nothing_here(self):
        """失败走 _on_inject_failed，不归这个方法管。"""
        app = FakeApp(wanted=7)
        app._on_inject_finished(False, attempt=7, log_on_switch=True)
        assert app.enabled == []
        assert app._option.calls == []


class TestLateSuccess:
    """#2 的核心：看门狗已经放弃，线程却成功了。"""

    def test_a_late_success_is_torn_down(self):
        app = FakeApp(wanted=0)          # 看门狗超时后清零
        app._on_inject_finished(True, attempt=7, log_on_switch=True)
        assert app._option.calls == ["disable_inject", "set_fallback"], \
            "界面已经是兼容模式，实际状态必须跟上"
        assert app.enabled == [], "绝不能在这条路径上宣布注入已启用"

    def test_the_user_is_told(self):
        """悄悄拆掉和悄悄留着一样糟 —— 两者都让人对着错误的状态排查。"""
        app = FakeApp(wanted=0)
        app._on_inject_finished(True, attempt=7, log_on_switch=True)
        assert any("超时之后" in m for m in app.messages)

    def test_superseded_by_a_newer_attempt_is_also_torn_down(self):
        """用户切走又切回来：wanted 变成新编号，旧线程的结果同样不能要。"""
        app = FakeApp(wanted=9)
        app._on_inject_finished(True, attempt=7, log_on_switch=True)
        assert app._option.calls == ["disable_inject", "set_fallback"]

    def test_a_late_failure_needs_no_teardown(self):
        app = FakeApp(wanted=0)
        app._on_inject_finished(False, attempt=7, log_on_switch=True)
        assert app._option.calls == []
        assert app.enabled == []

    def test_fallback_is_still_forced_if_teardown_throws(self):
        """管道可能已经死了。拆除失败也必须落到兼容模式，不能停在半路。"""
        app = FakeApp(wanted=0, attempt_option=FakeOption(
            disable_raises=RuntimeError("pipe already gone")))
        app._on_inject_finished(True, attempt=7, log_on_switch=True)
        assert "set_fallback" in app._option.calls


class TestCountersExist:
    def test_app_initialises_both_counters(self):
        """两个编号是这次修复的全部状态。少任何一个，竞态就回来了。"""
        source = (Path(main.__file__).read_text(encoding="utf-8"))
        assert "self._inject_attempt = 0" in source
        assert "self._inject_wanted = 0" in source

    def test_drain_loop_no_longer_gates_on_is_enabling_inject(self):
        """排空的续跑条件必须是"有没有被更新的尝试取代"。

        以 _is_enabling_inject 为准的话，看门狗一超时排空就停，线程晚到的结果
        永远没人看见 —— 也就没人去拆它。那正是原来的 bug。
        """
        source = Path(main.__file__).read_text(encoding="utf-8")
        assert "if self._inject_attempt == attempt:" in source
        assert "if self._is_enabling_inject:\n                    self.root.after" \
            not in source

    def test_watchdog_does_not_stop_the_drain(self):
        """看门狗原来往队列里塞 None 来结束排空。那正是要去掉的东西。"""
        source = Path(main.__file__).read_text(encoding="utf-8")
        watchdog = source[source.index("def _watchdog():"):]
        watchdog = watchdog[:watchdog.index("self.root.after(")]
        assert "log_queue.put(None)" not in watchdog
