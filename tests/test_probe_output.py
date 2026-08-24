# -*- coding: utf-8 -*-
"""探测器的输出管道。

起因是一个真实故障：打包版双击运行后窗口一闪而过，什么文件都没留下。原因是
报告攒在内存里、最后才一次性写出，而 main() 没有任何异常兜底 —— 中间一崩，
进程直接结束，控制台关闭，线索归零。
"""

import os
import stat
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
)
import windows_probe as probe  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每条用例都从干净的全局状态开始。"""
    monkeypatch.setattr(probe, "_report", None, raising=False)
    monkeypatch.setattr(probe, "OUT_DIR", None, raising=False)
    yield
    if probe._report is not None:
        probe._report.close()
        probe._report = None


class TestOutputLocation:
    def test_source_run_uses_the_current_folder(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert probe.output_dir() == str(tmp_path)

    def test_frozen_run_uses_the_folder_holding_the_exe(self, tmp_path, monkeypatch):
        """双击时 cwd 可能是任何地方，写到 exe 旁边才找得到。"""
        exe = tmp_path / "gbfr-probe.exe"
        exe.write_bytes(b"")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        assert probe.output_dir() == str(tmp_path)

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod 在 Windows 上挡不住写入")
    def test_unwritable_location_falls_back(self, tmp_path, monkeypatch):
        readonly = tmp_path / "ro"
        readonly.mkdir()
        os.chmod(readonly, stat.S_IRUSR | stat.S_IXUSR)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(readonly / "gbfr-probe.exe"))
        try:
            assert probe.output_dir() != str(readonly)
        finally:
            os.chmod(readonly, stat.S_IRWXU)


class TestIncrementalWriting:
    def test_lines_hit_the_disk_immediately(self, tmp_path, monkeypatch):
        """崩溃前写下的每一行都必须已经落盘。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()
        probe.say("第一行")
        probe.say("第二行")
        # 还没关闭文件就去读
        assert "第一行" in open(path, encoding="utf-8").read()
        assert "第二行" in open(path, encoding="utf-8").read()

    def test_report_lands_in_the_folder_not_a_subfolder(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()
        assert os.path.dirname(path) == str(tmp_path)
        assert os.path.basename(path) == probe.REPORT_NAME

    def test_no_writable_location_still_prints(self, monkeypatch, capsys):
        monkeypatch.setattr(probe, "output_dir", lambda: None)
        assert probe.open_report() is None
        probe.say("依然要显示")
        assert "依然要显示" in capsys.readouterr().out


class TestCrashesStillProduceAReport:
    """这才是这个文件存在的理由。"""

    def _run(self, tmp_path, monkeypatch, failure):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "argv", ["gbfr-probe"])
        monkeypatch.setattr(probe, "run_all", failure)
        status = probe.main()
        return status, (tmp_path / probe.REPORT_NAME).read_text(encoding="utf-8")

    def test_an_exception_is_written_into_the_report(self, tmp_path, monkeypatch):
        def boom(args):
            probe.say("查到一半的东西")
            raise RuntimeError("win32 炸了")

        status, text = self._run(tmp_path, monkeypatch, boom)
        assert status == 1
        assert "查到一半的东西" in text, "崩溃前的发现必须保住"
        assert "RuntimeError: win32 炸了" in text
        assert "Traceback" in text

    def test_the_report_path_is_named_even_on_failure(self, tmp_path, monkeypatch):
        def boom(args):
            raise ValueError("nope")
        _, text = self._run(tmp_path, monkeypatch, boom)
        assert probe.REPORT_NAME in text

    def test_ctrl_c_keeps_what_was_found(self, tmp_path, monkeypatch):
        def interrupted(args):
            probe.say("窗口那一段跑完了")
            raise KeyboardInterrupt
        status, text = self._run(tmp_path, monkeypatch, interrupted)
        assert status == 130
        assert "窗口那一段跑完了" in text
        assert "已中断" in text

    def test_a_bare_systemexit_does_not_escape_silently(self, tmp_path, monkeypatch):
        """BaseException 而不是 Exception：SystemExit 也得留下痕迹。"""
        def bail(args):
            probe.say("走到这里")
            raise SystemExit(3)
        status, text = self._run(tmp_path, monkeypatch, bail)
        assert status == 1
        assert "走到这里" in text

    def test_success_writes_a_complete_report(self, tmp_path, monkeypatch):
        status, text = self._run(tmp_path, monkeypatch, lambda args: probe.say("一切正常"))
        assert status == 0
        assert "一切正常" in text
        assert "完成" in text


def test_non_windows_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["gbfr-probe"])
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert probe.main() == 1
    assert "只能在 Windows" in capsys.readouterr().out


class TestSteamDiscovery:
    """猜四条路径的话，Steam 装在 D: 的人直接得到"没找到"。"""

    def test_registry_values_come_first(self, monkeypatch):
        import types
        fake = types.ModuleType("winreg")
        fake.HKEY_CURRENT_USER = 1
        fake.HKEY_LOCAL_MACHINE = 2

        class Key:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def open_key(hive, path):
            if hive == 1:
                return Key()
            raise OSError("no such key")

        fake.OpenKey = open_key
        fake.QueryValueEx = lambda handle, name: (r"D:\SteamLibrary\Steam", 1)
        monkeypatch.setitem(sys.modules, "winreg", fake)

        roots = probe.steam_roots()
        assert roots[0][0] == os.path.normpath(r"D:\SteamLibrary\Steam")
        assert "注册表" in roots[0][1]

    def test_guesses_remain_as_fallback(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "winreg", None)
        roots = probe.steam_roots()
        assert roots, "注册表读不到时仍要有兜底路径"
        assert all(source == "常见路径" for _, source in roots)

    def test_duplicates_are_collapsed(self, monkeypatch):
        import types
        fake = types.ModuleType("winreg")
        fake.HKEY_CURRENT_USER = 1
        fake.HKEY_LOCAL_MACHINE = 2

        class Key:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        fake.OpenKey = lambda hive, path: Key()
        fake.QueryValueEx = lambda handle, name: (r"C:\Steam", 1)
        monkeypatch.setitem(sys.modules, "winreg", fake)

        paths = [p.lower() for p, _ in probe.steam_roots()]
        assert len(paths) == len(set(paths))

    def test_the_app_id_is_relinks(self):
        assert probe.RELINK_APP_ID == "1090670"


class TestDoubleClickUsability:
    """打包版双击运行时传不了参数，所有分叉都得能在程序里走完。"""

    def _args(self, **kw):
        import argparse
        ns = argparse.Namespace(title="Granblue", gamepad_test=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_source_run_never_prompts(self, monkeypatch):
        """命令行运行必须保持非交互，否则脚本化调用会卡住。"""
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("不该提问"))
        assert probe.ask_gamepad_test(self._args()) is False

    def test_frozen_run_asks(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "gbfr-probe.exe"))
        probe.open_report()
        monkeypatch.setattr("builtins.input", lambda *a: "y")
        assert probe.ask_gamepad_test(self._args()) is True
        assert "手柄测试: 要做" in (tmp_path / probe.REPORT_NAME).read_text(encoding="utf-8")

    def test_declining_is_recorded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "gbfr-probe.exe"))
        probe.open_report()
        monkeypatch.setattr("builtins.input", lambda *a: "")
        assert probe.ask_gamepad_test(self._args()) is False
        assert "手柄测试: 跳过" in (tmp_path / probe.REPORT_NAME).read_text(encoding="utf-8")

    def test_an_explicit_flag_is_not_re_asked(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("已经指定过了"))
        assert probe.ask_gamepad_test(self._args(gamepad_test=True)) is False

    def test_closed_stdin_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        def no_stdin(*a):
            raise EOFError
        monkeypatch.setattr("builtins.input", no_stdin)
        assert probe.ask_gamepad_test(self._args()) is False
