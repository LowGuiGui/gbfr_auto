# -*- coding: utf-8 -*-
"""xinput.py 的判定逻辑测试。

这个模块存在的全部意义是回答"失焦时 XInput 会不会被清零"，而结论是由
summarize() + verdict() 这两个纯函数给出的。所以它们必须在 Linux 上被完整测试
—— 真机上只跑一次，跑错了不会有人发现。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import xinput  # noqa: E402


def _reading(**kw):
    base = dict(packet=1, buttons=0, left_trigger=0, right_trigger=0,
                lx=0, ly=0, rx=0, ry=0)
    base.update(kw)
    return xinput.Reading(**base)


NEUTRAL = _reading()
PUSHED = _reading(ly=32767)


class TestIsNeutral:
    def test_all_zero_is_neutral(self):
        assert xinput.is_neutral(NEUTRAL)

    def test_missing_pad_counts_as_neutral(self):
        assert xinput.is_neutral(None)

    def test_pushed_stick_is_live(self):
        assert not xinput.is_neutral(PUSHED)

    def test_button_is_live(self):
        assert not xinput.is_neutral(_reading(buttons=0x1000))

    def test_trigger_is_live(self):
        assert not xinput.is_neutral(_reading(left_trigger=255))

    def test_small_drift_still_neutral(self):
        """真手柄静止时也不会正好是 0，否则每次采样都会被误判成 live。"""
        drift = xinput.NEUTRAL_TOLERANCE - 1
        assert xinput.is_neutral(_reading(lx=drift, ly=-drift))

    def test_just_past_tolerance_is_live(self):
        assert not xinput.is_neutral(_reading(lx=xinput.NEUTRAL_TOLERANCE + 1))


class TestDescribe:
    def test_output_is_ascii(self):
        """探测器的输出必须是纯 ASCII —— 英文 Windows 控制台会被非 ASCII 弄崩。"""
        for reading in (NEUTRAL, PUSHED, None):
            xinput.describe(reading).encode("ascii")

    def test_labels_the_verdict(self):
        assert "NEUTRAL" in xinput.describe(NEUTRAL)
        assert "LIVE" in xinput.describe(PUSHED)

    def test_missing_pad_is_named(self):
        assert "not connected" in xinput.describe(None)


class TestReadingSnapshot:
    def test_copies_out_of_the_ctypes_buffer(self):
        """ctypes 结构体是会被下次调用覆盖的内存，必须拷贝而不是引用。

        不拷的话，采样列表里每一条都会指向同一块内存，回头一看全是最后一次的值。
        """
        state = xinput.XINPUT_STATE()
        state.dwPacketNumber = 7
        state.Gamepad.sThumbLY = 32767
        snapshot = xinput.Reading.from_state(state)

        state.Gamepad.sThumbLY = 0          # 模拟下一次 XInputGetState 覆盖
        state.dwPacketNumber = 8

        assert snapshot.ly == 32767
        assert snapshot.packet == 7


class _FakeDLL:
    """XInputGetState 的替身。按槽位给不同结果。"""

    def __init__(self, per_slot):
        self.per_slot = per_slot
        self.calls = []

    def XInputGetState(self, index, buf):
        slot = index.value if hasattr(index, "value") else int(index)
        self.calls.append(slot)
        result = self.per_slot.get(slot)
        if result is None:
            return xinput.ERROR_DEVICE_NOT_CONNECTED
        buf._obj.dwPacketNumber = result[0]
        buf._obj.Gamepad.sThumbLY = result[1]
        return xinput.ERROR_SUCCESS


class TestRead:
    def test_returns_reading_for_connected_slot(self):
        dll = _FakeDLL({0: (3, 32767)})
        reading = xinput.read(dll, 0)
        assert reading.ly == 32767
        assert reading.packet == 3

    def test_returns_none_for_empty_slot(self):
        assert xinput.read(_FakeDLL({}), 2) is None

    def test_survives_a_dll_without_the_symbol(self):
        class Bare:
            def __getattr__(self, name):
                raise AttributeError(name)

        assert xinput.read(Bare(), 0) is None

    def test_connected_slots_lists_only_live_ones(self):
        dll = _FakeDLL({0: (1, 0), 3: (1, 0)})
        assert xinput.connected_slots(dll) == [0, 3]


class TestAvailableLibraries:
    def test_keeps_candidate_order_and_drops_missing(self, monkeypatch):
        loaded = {"xinput1_3.dll": "handle-13", "XInput9_1_0.dll": "handle-910"}
        monkeypatch.setattr(xinput, "load_library", lambda name: loaded.get(name))
        assert xinput.available_libraries() == [
            ("xinput1_3.dll", "handle-13"),
            ("XInput9_1_0.dll", "handle-910"),
        ]

    def test_load_library_never_raises(self):
        """两个平台都不许抛异常，但"该返回什么"是平台相关的。

        CI 会跑两遍：ubuntu 带桩，windows-latest **不带**桩。断言"返回 None"
        在 Windows 上必错 —— xinput1_4.dll 是系统自带的，本来就该载入成功。
        """
        result = xinput.load_library("xinput1_4.dll")
        if sys.platform.startswith("win"):
            assert result is not None
        else:
            assert result is None, "Linux 上 ctypes 没有 WinDLL，AttributeError 必须被吃掉"

    def test_load_library_returns_none_for_a_name_that_does_not_exist(self):
        """这条在两个平台上都成立，所以它才是真正的护栏。"""
        assert xinput.load_library("gbfr_not_a_real_xinput_dll.dll") is None


class TestSampleFocus:
    def test_records_focus_alongside_each_reading(self):
        ticks = iter([0.0, 0.0, 0.5, 1.0, 1.5])
        focus_states = iter([True, False, False])

        samples = xinput.sample_focus(
            _FakeDLL({0: (1, 32767)}), seconds=1.0, interval=0.5,
            clock=lambda: next(ticks), sleep=lambda _s: None,
            focus=lambda: next(focus_states),
        )

        assert [s.ours for s in samples] == [True, False, False]
        assert all(s.reading.ly == 32767 for s in samples)

    def test_stops_at_the_time_limit(self):
        ticks = iter([0.0, 0.0, 5.0])
        samples = xinput.sample_focus(
            _FakeDLL({0: (1, 0)}), seconds=1.0, interval=0.1,
            clock=lambda: next(ticks), sleep=lambda _s: None, focus=lambda: True,
        )
        assert len(samples) == 1


class TestSummarize:
    def _samples(self, spec):
        return [xinput.Sample(i, ours, reading)
                for i, (ours, reading) in enumerate(spec)]

    def test_cross_tabulates(self):
        buckets = xinput.summarize(self._samples([
            (True, PUSHED), (True, PUSHED),
            (False, NEUTRAL), (False, NEUTRAL), (False, NEUTRAL),
        ]))
        assert buckets["focused_live"] == 2
        assert buckets["unfocused_neutral"] == 3
        assert buckets["focused_neutral"] == 0
        assert buckets["unfocused_live"] == 0

    def test_unknown_focus_is_not_counted_as_unfocused(self):
        """拿不到前台信息时必须记成 unknown。

        当成 False 会凭空造出"失焦"的证据，而这正是整个测试要证明的那一栏。
        """
        buckets = xinput.summarize(self._samples([(None, NEUTRAL)]))
        assert buckets["unknown"] == 1
        assert buckets["unfocused_neutral"] == 0


class TestVerdict:
    def _verdict(self, **counts):
        buckets = {"focused_live": 0, "focused_neutral": 0, "unfocused_live": 0,
                   "unfocused_neutral": 0, "unknown": 0}
        buckets.update(counts)
        return xinput.verdict(buckets)[0]

    def test_os_gate_when_unfocused_is_always_neutral(self):
        assert self._verdict(focused_live=20, unfocused_neutral=20) == "os-gate"

    def test_no_os_gate_when_unfocused_stays_live(self):
        assert self._verdict(focused_live=20, unfocused_live=20) == "no-os-gate"

    def test_mixed_is_reported_not_rounded_off(self):
        assert self._verdict(focused_live=20, unfocused_live=5,
                             unfocused_neutral=15) == "mixed"

    def test_never_clicked_away_is_inconclusive(self):
        assert self._verdict(focused_live=20) == "inconclusive"

    def test_never_focused_is_inconclusive(self):
        assert self._verdict(unfocused_neutral=20) == "inconclusive"

    def test_stick_never_pushed_invalidates_the_test(self):
        """焦点时就是中立的，说明摇杆压根没推 —— 不能拿去证明失焦有影响。"""
        assert self._verdict(focused_neutral=20, unfocused_neutral=20) == "no-input"

    @pytest.mark.parametrize("counts", [
        dict(focused_live=20, unfocused_neutral=20),
        dict(focused_live=20, unfocused_live=20),
        dict(focused_live=20),
        dict(focused_neutral=5, unfocused_neutral=5),
    ])
    def test_explanation_is_ascii(self, counts):
        buckets = {"focused_live": 0, "focused_neutral": 0, "unfocused_live": 0,
                   "unfocused_neutral": 0, "unknown": 0}
        buckets.update(counts)
        xinput.verdict(buckets)[1].encode("ascii")


class TestBaselineWatcher:
    """焦点判定按 hwnd 比，不按 PID —— 从终端跑时控制台窗口不属于我们的进程。"""

    def test_true_while_the_same_window_is_in_front(self, monkeypatch):
        monkeypatch.setattr(xinput, "foreground_window", lambda: 4242)
        assert xinput.baseline_watcher(4242)() is True

    def test_false_once_focus_moves_away(self, monkeypatch):
        monkeypatch.setattr(xinput, "foreground_window", lambda: 9999)
        assert xinput.baseline_watcher(4242)() is False

    def test_unknown_when_there_is_no_foreground_window(self, monkeypatch):
        """必须是 None 而不是 False，否则会凭空造出'失焦'的样本。"""
        monkeypatch.setattr(xinput, "foreground_window", lambda: None)
        assert xinput.baseline_watcher(4242)() is None

    def test_foreground_window_never_raises(self):
        """Windows 上真的有前台窗口，所以只能断言类型，不能断言 None。"""
        result = xinput.foreground_window()
        if sys.platform.startswith("win"):
            assert result is None or isinstance(result, int)
        else:
            assert result is None


class TestOwnProcessControl:
    def test_summarize_counts_the_control_column(self):
        samples = [
            xinput.Sample(0, True, PUSHED, True),
            xinput.Sample(1, True, PUSHED, False),
            xinput.Sample(2, False, NEUTRAL, False),
        ]
        assert xinput.summarize(samples)["own_process_foreground"] == 1

    def test_control_column_does_not_change_the_verdict(self):
        """own 只是对照，不能参与结论。"""
        samples = [xinput.Sample(0, True, PUSHED, False),
                   xinput.Sample(1, False, NEUTRAL, False)]
        assert xinput.verdict(xinput.summarize(samples))[0] == "os-gate"


class TestFocusCaveat:
    def test_warns_when_we_never_owned_the_foreground(self):
        caveat = xinput.focus_caveat({"own_process_foreground": 0})
        assert caveat is not None
        caveat.encode("ascii")

    def test_silent_when_we_did(self):
        assert xinput.focus_caveat({"own_process_foreground": 5}) is None

    def test_missing_key_is_treated_as_never(self):
        assert xinput.focus_caveat({}) is not None
