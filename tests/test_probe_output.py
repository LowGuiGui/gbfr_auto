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
        assert "Interrupted" in text

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
        assert "Done" in text


def test_non_windows_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["gbfr-probe"])
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert probe.main() == 1
    assert "only runs on Windows" in capsys.readouterr().out


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
        assert "registry" in roots[0][1]

    def test_guesses_remain_as_fallback(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "winreg", None)
        roots = probe.steam_roots()
        assert roots, "注册表读不到时仍要有兜底路径"
        assert all(source == "common path" for _, source in roots)

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
        assert "gamepad test: yes" in (tmp_path / probe.REPORT_NAME).read_text(encoding="utf-8")

    def test_declining_is_recorded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "gbfr-probe.exe"))
        probe.open_report()
        monkeypatch.setattr("builtins.input", lambda *a: "")
        assert probe.ask_gamepad_test(self._args()) is False
        assert "gamepad test: skipped" in (tmp_path / probe.REPORT_NAME).read_text(encoding="utf-8")

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


class TestEncodingCannotKillTheProbe:
    """上一次的真实故障：英文版 Windows 控制台是 cp437/cp1252，print 一个中文字
    就抛 UnicodeEncodeError；而报错处理器自己也打中文，于是报错时又炸一次，异常
    逃出 except 和 finally，进程静默退出 —— "打了几行就没了，什么都没留下"。
    """

    def test_all_probe_output_is_ascii(self):
        """永久性护栏：输出串一旦混进非 ASCII，这条就红。"""
        import ast
        src = open(probe.__file__, encoding="utf-8").read()
        offenders = []
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in ("say", "print", "input"):
                continue
            for arg in node.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        if not sub.value.isascii():
                            offenders.append(sub.value)
        assert offenders == [], (
            "探测器的输出必须是 ASCII —— 非英文 Windows 的控制台代码页编不了别的。"
            f" 违规: {offenders[:3]}"
        )

    def test_say_survives_a_console_that_cannot_encode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()

        def hostile_print(*a, **k):
            raise UnicodeEncodeError("charmap", "x", 0, 1, "no")

        monkeypatch.setattr("builtins.print", hostile_print)
        probe.say("this must still reach the file")      # 不抛
        monkeypatch.undo()
        assert "this must still reach the file" in open(path, encoding="utf-8-sig").read()

    def test_the_file_is_written_before_the_console(self, tmp_path, monkeypatch):
        """顺序是刻意的：文件永远写得进去，控制台才是会炸的那端。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()
        seen = {}

        def check_print(*a, **k):
            seen["file_had_it"] = "ordering" in open(path, encoding="utf-8-sig").read()

        monkeypatch.setattr("builtins.print", check_print)
        probe.say("ordering")
        monkeypatch.undo()
        assert seen["file_had_it"], "print 之前就该落盘"

    def test_a_crash_is_reported_even_when_the_console_is_hostile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "argv", ["gbfr-probe"])

        def boom(args):
            raise RuntimeError("underlying failure")

        monkeypatch.setattr(probe, "run_all", boom)
        real_print = print

        def hostile_print(*a, **k):
            raise UnicodeEncodeError("charmap", "x", 0, 1, "no")

        monkeypatch.setattr("builtins.print", hostile_print)
        status = probe.main()
        monkeypatch.setattr("builtins.print", real_print)

        assert status == 1
        text = (tmp_path / probe.REPORT_NAME).read_text(encoding="utf-8-sig")
        assert "RuntimeError: underlying failure" in text

    def test_report_has_a_bom_so_notepad_renders_it(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()
        probe.say("x")
        probe._report.flush()
        assert open(path, "rb").read(3) == b"\xef\xbb\xbf"


class TestEmergencyDump:
    def test_it_writes_when_the_report_could_not_be_opened(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "executable", str(tmp_path / "gbfr-probe.exe"))
        path = probe.emergency_dump("Traceback...\nRuntimeError: early failure")
        assert path is not None
        assert "early failure" in open(path, encoding="ascii").read()

    def test_non_ascii_does_not_defeat_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "executable", str(tmp_path / "gbfr-probe.exe"))
        path = probe.emergency_dump("崩溃了 → crash")
        assert path is not None and "crash" in open(path, encoding="ascii").read()


class TestAMissingModuleMustNotKillTheProbe:
    """真实故障：vigem 没被打进 exe，模块级 import 在 main() 存在之前就炸了，
    用户看到一屏 PyInstaller 堆栈然后窗口关闭 —— 前面所有的兜底都够不着那里。
    """

    def test_the_import_is_guarded(self):
        """模块级导入必须被 try 包住，否则打包漏了任何一个模块就是硬崩。"""
        import ast
        src = open(probe.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for stmt in node.body:
                    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                        for alias in stmt.names:
                            guarded.add(alias.name)
        bare = set()
        for node in tree.body:                       # 只看模块级
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bare.add(alias.name)
        assert "vigem" not in bare, "vigem 必须在 try 里导入"
        assert "vigem" in guarded

    def test_the_gamepad_section_degrades_instead_of_crashing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(probe, "vigem", None)
        monkeypatch.setattr(probe, "_VIGEM_ERROR", "ModuleNotFoundError: No module named 'vigem'")
        path = probe.open_report()
        probe.probe_gamepad(False)                   # 不抛
        text = open(path, encoding="utf-8-sig").read()
        assert "failed to import" in text
        assert "Everything above still stands" in text

    def test_a_missing_module_is_named_in_the_imports_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(probe, "_VIGEM_ERROR", "ModuleNotFoundError: No module named 'vigem'")
        path = probe.open_report()
        assert probe.probe_imports() is False
        text = open(path, encoding="utf-8-sig").read()
        assert "[FAIL]" in text and "vigem" in text
        assert "not bundled" in text

    def test_the_repo_root_modules_are_checked_too(self, tmp_path, monkeypatch):
        """opencv / window_capture 也在仓库根，和 vigem 是同一个打包风险。"""
        import inspect
        src = inspect.getsource(probe.probe_imports)
        assert "opencv" in src and "window_capture" in src


def test_the_import_check_only_lists_what_the_probe_uses():
    """列一个探测器不 import 的模块，PyInstaller 就不会打包它，于是变成假 [FAIL]。

    win32process 正是这样进来的：只有 hook/injector.py 用它，而探测器不碰注入。
    CI 的冒烟测试第一次跑就抓到了。
    """
    import inspect
    src = inspect.getsource(probe)
    checked = set()
    import ast
    for node in ast.walk(ast.parse(inspect.getsource(probe.probe_imports))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.isidentifier():
                checked.add(node.value)
    for name in checked - {"vigem", "ok", "builtin"}:
        assert name in src, f"{name} 在检查列表里，但探测器根本没引用它"


class TestSaveDiscovery:
    """第一版只找 Steam userdata/<id>/1090670/remote，真机上一个都没找到。

    游戏真正写盘的地方是 %LOCALAPPDATA%\\GBFR\\Saved\\SaveGames；Steam 云那几份
    是同步副本。对功能 3B 来说本地那份才关键 —— 它的 mtime 才是真实信号。
    """

    def test_the_local_appdata_path_is_checked_first(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
        first_path, first_source = probe.save_locations()[0]
        # 不用 os.path.join 断言：%VAR% 只有 ntpath.expandvars 会展开，Linux 上
        # 这条路径保持原样，分隔符也是反斜杠。看组成部分就够了。
        assert first_path.endswith("GBFR" + "\\" + "Saved" + "\\" + "SaveGames")
        assert "where the game writes" in first_source

    def test_steam_cloud_mirrors_are_also_checked(self, monkeypatch, tmp_path):
        userdata = tmp_path / "userdata" / "12345678"
        userdata.mkdir(parents=True)
        monkeypatch.setattr(probe, "steam_roots", lambda: [(str(tmp_path), "test")])
        paths = [p for p, _ in probe.save_locations()]
        assert any("881020" in p for p in paths), "云端镜像路径也要查"
        assert any(probe.RELINK_APP_ID in p for p in paths)

    def test_a_missing_localappdata_does_not_explode(self, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setattr(probe, "steam_roots", lambda: [])
        assert probe.save_locations()          # 仍返回一条（未展开的）路径，不抛


class TestGamepadDetectionMustTryToConnect:
    """卸载项只有 MSI 安装才会写。用 nefconw 手动装（官方支持）不写，于是驱动
    装好了却被判成没装 —— 而第一版在那种情况下连试都不试就 return 了。
    """

    def test_a_missing_uninstall_entry_does_not_skip_the_attempt(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        probe.open_report()

        attempted = []

        class FakePad:
            def connect(self):
                attempted.append(True)
                raise RuntimeError("bus not found")
            def close(self):
                pass

        fake = type("V", (), {
            "client_dll_path": staticmethod(lambda: __file__),   # 存在即可
            "driver_installed": staticmethod(lambda: (False, None)),
            "driver_service_present": staticmethod(lambda: False),
            "VirtualGamepad": FakePad,
            "DRIVER_VERSION": "1.22.0",
            "DRIVER_DOWNLOAD_URL": "https://example/installer.exe",
        })
        monkeypatch.setattr(probe, "vigem", fake)
        probe.probe_gamepad(False)
        assert attempted, "注册表说没装也必须真的试一次连接"

    def test_the_report_explains_extract_is_not_install(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()

        class FakePad:
            def connect(self):
                raise RuntimeError("bus not found")
            def close(self):
                pass

        fake = type("V", (), {
            "client_dll_path": staticmethod(lambda: __file__),
            "driver_installed": staticmethod(lambda: (False, None)),
            "driver_service_present": staticmethod(lambda: False),
            "VirtualGamepad": FakePad,
            "DRIVER_VERSION": "1.22.0",
            "DRIVER_DOWNLOAD_URL": "https://example/installer.exe",
        })
        monkeypatch.setattr(probe, "vigem", fake)
        probe.probe_gamepad(False)
        text = open(path, encoding="utf-8-sig").read()
        # 主路径必须是官方安装程序；nefconw 只是安装程序跑不了时的兜底。
        assert "Do NOT pass /extract" in text
        assert "EXTRACTED payload, not an install" in text
        installer_at = text.index("Double-click it and let it install")
        nefcon_at = text.index("--create-device-node")
        assert installer_at < nefcon_at, "先给安装程序，再给手动命令"
        assert "Only if the installer refuses" in text
        assert "--install-driver" in text
        # 数值来自 ViGEmBus 自己的 INF，核对过
        assert r"Nefarius\ViGEmBus\Gen1" in text
        assert "4D36E97D-E325-11CE-BFC1-08002BE10318" in text

    def test_a_present_service_points_at_version_mismatch_instead(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()

        class FakePad:
            def connect(self):
                raise RuntimeError("connect failed")
            def close(self):
                pass

        fake = type("V", (), {
            "client_dll_path": staticmethod(lambda: __file__),
            "driver_installed": staticmethod(lambda: (False, None)),
            "driver_service_present": staticmethod(lambda: True),
            "VirtualGamepad": FakePad,
            "DRIVER_VERSION": "1.22.0",
            "DRIVER_DOWNLOAD_URL": "https://example/installer.exe",
        })
        monkeypatch.setattr(probe, "vigem", fake)
        probe.probe_gamepad(False)
        assert "version" in open(path, encoding="utf-8-sig").read()
