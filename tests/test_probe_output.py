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
            "RETRY_ATTEMPTS": 3,
            "loaded_driver_path": staticmethod(lambda: r"C:\Windows\System32\drivers\ViGEmBus.sys"),
            "bus_device_instances": staticmethod(lambda: ["ROOT&0000"]),
            "other_vigem_users": staticmethod(lambda: []),
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
            "RETRY_ATTEMPTS": 3,
            "loaded_driver_path": staticmethod(lambda: r"C:\Windows\System32\drivers\ViGEmBus.sys"),
            "bus_device_instances": staticmethod(lambda: ["ROOT&0000"]),
            "other_vigem_users": staticmethod(lambda: []),
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

    def test_a_present_service_warns_against_installing_again(self, tmp_path, monkeypatch):
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
            "RETRY_ATTEMPTS": 3,
            "loaded_driver_path": staticmethod(lambda: r"C:\Windows\System32\drivers\ViGEmBus.sys"),
            "bus_device_instances": staticmethod(lambda: ["ROOT&0000"]),
            "other_vigem_users": staticmethod(lambda: []),
        })
        monkeypatch.setattr(probe, "vigem", fake)
        probe.probe_gamepad(False)
        text = open(path, encoding="utf-8-sig").read()
        # 可能已经装好了。这时再跑一次 --create-device-node 会多一个重复设备节点。
        assert "DO NOT run the manual install commands" in text
        assert "duplicate device node" in text
        # 警告里提到命令名是可以的（那是在解释为什么别跑）；不能出现的是
        # 那段"照着敲"的安装指引本身。
        assert "Only if the installer refuses" not in text
        assert "Download " not in text
        assert "Device Manager" in text


class TestWindowModeIsReportedWithTheGamepadTest:
    """手柄测试的结论完全取决于窗口模式。

    社区那条"后台也能收手柄输入"的说法明确限定在"全屏窗口"（无边框）模式。
    Howard 在普通窗口模式下测出失焦不动 —— 那并没有推翻它，只是测了另一件事。
    报告必须自己说清楚当时是哪种模式。
    """

    def test_windowed_is_labelled_as_such(self, monkeypatch):
        import types
        monkeypatch.setitem(sys.modules, "win32api", types.SimpleNamespace(
            GetWindowLong=lambda h, i: probe.WS_CAPTION))
        monkeypatch.setitem(sys.modules, "win32gui", types.SimpleNamespace(
            GetWindowRect=lambda h: (0, 0, 100, 100)))
        assert "WINDOWED" in probe.window_mode_label(1234)

    def test_borderless_is_labelled_with_its_size(self, monkeypatch):
        import types
        monkeypatch.setitem(sys.modules, "win32api", types.SimpleNamespace(
            GetWindowLong=lambda h, i: probe.WS_POPUP))
        monkeypatch.setitem(sys.modules, "win32gui", types.SimpleNamespace(
            GetWindowRect=lambda h: (0, 0, 3840, 2160)))
        label = probe.window_mode_label(5678)
        assert "borderless" in label and "3840x2160" in label

    def test_no_window_is_not_a_crash(self):
        assert "unknown" in probe.window_mode_label(None)

    def test_the_test_instructions_name_the_mode_to_retry_in(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()

        class FakePad:
            attempts_used = 1
            def connect(self): pass
            def close(self): pass
            def left_stick_forward(self): pass
            def neutral(self): pass

        fake = type("V", (), {
            "client_dll_path": staticmethod(lambda: __file__),
            "driver_installed": staticmethod(lambda: (True, "1.22.0")),
            "driver_service_present": staticmethod(lambda: True),
            "loaded_driver_path": staticmethod(lambda: "x.sys"),
            "bus_device_instances": staticmethod(lambda: ["ROOT\\SYSTEM\\0001"]),
            "other_vigem_users": staticmethod(lambda: []),
            "VirtualGamepad": FakePad,
            "RETRY_ATTEMPTS": 3,
            "DRIVER_VERSION": "1.22.0",
            "DRIVER_DOWNLOAD_URL": "https://example/x.exe",
        })
        monkeypatch.setattr(probe, "vigem", fake)
        monkeypatch.setattr(probe, "window_mode_label", lambda h: "ordinary WINDOWED (has a title bar)")
        monkeypatch.setattr("builtins.input", lambda *a: "")
        monkeypatch.setattr(probe.time, "sleep", lambda s: None)
        probe.probe_gamepad(True, hwnd=1)
        text = open(path, encoding="utf-8-sig").read()
        assert "ordinary WINDOWED" in text, "报告要记下当时的窗口模式"
        assert "Full Screen Window" in text, "要指明该在哪种模式下重测"
        assert "proves nothing either way" in text

    def test_a_healthy_connection_does_not_blame_cotenants(self, tmp_path, monkeypatch):
        """连上了就别再说"两份安装是下面那个失败的常见原因" —— 下面没有失败。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(sys, "frozen", raising=False)
        path = probe.open_report()

        class FakePad:
            attempts_used = 1
            def connect(self): pass
            def close(self): pass

        fake = type("V", (), {
            "client_dll_path": staticmethod(lambda: __file__),
            "driver_installed": staticmethod(lambda: (True, "1.22.0")),
            "driver_service_present": staticmethod(lambda: True),
            "loaded_driver_path": staticmethod(lambda: "x.sys"),
            "bus_device_instances": staticmethod(lambda: ["ROOT\\SYSTEM\\0001"]),
            "other_vigem_users": staticmethod(lambda: [("SunshineService", "Sunshine", 2)]),
            "VirtualGamepad": FakePad,
            "RETRY_ATTEMPTS": 3,
            "DRIVER_VERSION": "1.22.0",
            "DRIVER_DOWNLOAD_URL": "https://example/x.exe",
        })
        monkeypatch.setattr(probe, "vigem", fake)
        probe.probe_gamepad(False, hwnd=1)
        text = open(path, encoding="utf-8-sig").read()
        assert "SunshineService" in text, "还是要列出来，只是不该当成故障原因"
        assert "usual cause of the failure" not in text
        assert "net stop" not in text


class _FakeXInputDLL:
    """XInputGetState 的替身：槽位 0 永远报告"摇杆推满"。"""

    def XInputGetState(self, index, buf):
        import xinput as _x
        slot = index.value if hasattr(index, "value") else int(index)
        if slot != 0:
            return _x.ERROR_DEVICE_NOT_CONNECTED
        buf._obj.dwPacketNumber = 1
        buf._obj.Gamepad.sThumbLY = 32767
        return _x.ERROR_SUCCESS


class _FakePad:
    def __init__(self):
        self.closed = False
        self.pushed = False

    def connect(self):
        pass

    def left_stick_forward(self):
        self.pushed = True

    def neutral(self):
        pass

    def close(self):
        self.closed = True


class TestInputBackendSection:
    """第 6 段（#45 指纹）在 Linux 上也要能整段跑完而不炸。"""

    def test_no_window_is_skipped_not_crashed(self, capsys):
        assert probe.probe_input_backend(None) is None
        assert "Skipped" in capsys.readouterr().out

    def test_unreadable_process_tells_the_user_what_to_do(self, capsys, monkeypatch):
        """游戏以管理员跑、探测器没有时的真实路径。"""
        monkeypatch.setattr(probe.procinfo, "pid_for_window", lambda h: 4242)
        monkeypatch.setattr(probe.procinfo, "process_modules",
                            lambda pid: (None, "OpenProcess failed: error 5 (access denied)", None))
        assert probe.probe_input_backend(1234) is None
        out = capsys.readouterr().out
        assert "access denied" in out
        assert "Run as administrator" in out

    def test_reports_the_backend_and_a_verdict(self, capsys, monkeypatch):
        monkeypatch.setattr(probe.procinfo, "pid_for_window", lambda h: 4242)
        monkeypatch.setattr(probe.procinfo, "process_modules", lambda pid: (
            [r"C:\g\gbfr.exe", r"C:\W\S\xinput1_4.dll", r"C:\W\S\hid.dll"],
            None, r"C:\g\gbfr.exe"))
        assert probe.probe_input_backend(1234) == "xinput"
        out = capsys.readouterr().out
        assert "xinput1_4.dll" in out
        assert "verdict: [xinput]" in out

    def test_section_output_is_ascii(self, capsys, monkeypatch):
        monkeypatch.setattr(probe.procinfo, "pid_for_window", lambda h: 4242)
        monkeypatch.setattr(probe.procinfo, "process_modules", lambda pid: (
            [r"C:\W\S\Windows.Gaming.Input.dll"], None, r"C:\g\gbfr.exe"))
        probe.probe_input_backend(1234)
        capsys.readouterr().out.encode("ascii")


class TestXInputFocusSection:
    """第 7 段是 #45 唯一的决定性测量，所以它的每条分支都要在这里走一遍。"""

    def test_no_xinput_dll_stops_cleanly(self, capsys, monkeypatch):
        monkeypatch.setattr(probe.xinput, "available_libraries", lambda: [])
        probe.probe_xinput_focus(True)
        assert "No XInput DLL" in capsys.readouterr().out

    def test_without_the_flag_it_only_explains_itself(self, capsys, monkeypatch):
        monkeypatch.setattr(probe.xinput, "available_libraries",
                            lambda: [("xinput1_4.dll", object())])
        probe.probe_xinput_focus(False)
        out = capsys.readouterr().out
        assert "--xinput-test" in out

    def _arm(self, monkeypatch):
        """把第 7 段接到假 DLL、假手柄、假前台窗口上。

        刻意不在这里桩 sample_focus：每个用例关心的采样结果都不一样，由用例自己
        给，helper 只负责让这一段能走到采样那一步。
        """
        pad = _FakePad()
        monkeypatch.setattr(probe.xinput, "available_libraries",
                            lambda: [("xinput1_4.dll", _FakeXInputDLL())])
        monkeypatch.setattr(probe.xinput, "foreground_window", lambda: 777)
        monkeypatch.setattr(probe.vigem, "VirtualGamepad", lambda: pad)
        monkeypatch.setattr("builtins.input", lambda *a: "")
        return pad

    def test_os_gate_is_reported_when_unfocused_reads_neutral(self, capsys, monkeypatch):
        """摇杆一直推着，但失焦时读到中立 —— 就是文档描述的那个门。"""
        pad = self._arm(monkeypatch)

        # 失焦后让 DLL 返回中立，模拟 Windows 把状态清零
        import xinput as _x
        samples = [_x.Sample(0, True, _x.Reading(1, 0, 0, 0, 0, 32767, 0, 0), False),
                   _x.Sample(1, False, _x.Reading(2, 0, 0, 0, 0, 0, 0, 0), False)]
        monkeypatch.setattr(probe.xinput, "sample_focus", lambda dll, **kw: samples)
        monkeypatch.setattr(probe.xinput, "connected_slots", lambda dll: [0])

        probe.probe_xinput_focus(True)
        out = capsys.readouterr().out
        assert "verdict: [os-gate]" in out
        assert pad.closed, "虚拟手柄必须拔掉，否则摇杆会一直推着留在系统里"

    def test_pad_is_unplugged_even_if_sampling_explodes(self, capsys, monkeypatch):
        """采样炸了也必须拔手柄 —— 不然摇杆推满的虚拟设备就留在系统里了。"""
        pad = self._arm(monkeypatch)
        monkeypatch.setattr(probe.xinput, "connected_slots", lambda dll: [0])

        def boom(dll, **kw):
            raise RuntimeError("sampling died")

        monkeypatch.setattr(probe.xinput, "sample_focus", boom)
        with pytest.raises(RuntimeError):
            probe.probe_xinput_focus(True)
        assert pad.closed

    def test_invisible_pad_stops_before_measuring(self, capsys, monkeypatch):
        """手柄插上了但 XInput 看不见，此时任何读数都没有意义。"""
        pad = self._arm(monkeypatch)
        monkeypatch.setattr(probe.xinput, "connected_slots", lambda dll: [])
        probe.probe_xinput_focus(True)
        out = capsys.readouterr().out
        assert "XInput cannot see it" in out
        assert pad.closed

    def test_section_output_is_ascii(self, capsys, monkeypatch):
        import xinput as _x
        self._arm(monkeypatch)
        monkeypatch.setattr(probe.xinput, "connected_slots", lambda dll: [0])
        monkeypatch.setattr(probe.xinput, "sample_focus", lambda dll, **kw: [
            _x.Sample(0, True, _x.Reading(1, 0, 0, 0, 0, 32767, 0, 0), False),
            _x.Sample(1, False, _x.Reading(2, 0, 0, 0, 0, 0, 0, 0), False)])
        probe.probe_xinput_focus(True)
        capsys.readouterr().out.encode("ascii")


def _stats(mean, count=15):
    return {"count": count, "mean": mean, "max": mean, "median": mean, "dropped": 0}


class TestFocusBehaviourSection:
    """第 8 段要区分"游戏停了"和"游戏在跑但不理输入"。判错方向比没测更糟。"""

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch):
        """倒计时有两次 5 秒，测试里一秒都不能真睡。"""
        monkeypatch.setattr(probe.time, "sleep", lambda _s: None)

    def test_no_window_is_skipped(self, capsys):
        probe.probe_focus_behaviour(True, None)
        assert "game window was not found" in capsys.readouterr().out

    def test_without_the_flag_it_only_explains_itself(self, capsys):
        probe.probe_focus_behaviour(False, 1234)
        out = capsys.readouterr().out
        assert "--focus-test" in out
        assert "IN A QUEST" in out

    def _arm(self, monkeypatch, phases, focus_sequence):
        pad = _FakePad()
        monkeypatch.setattr("builtins.input", lambda *a: "")
        monkeypatch.setattr(probe.vigem, "VirtualGamepad", lambda: pad)
        states = iter(focus_sequence)
        monkeypatch.setattr(probe, "_game_is_focused", lambda hwnd: next(states))
        results = iter(phases)
        monkeypatch.setattr(probe, "_capture_deltas",
                            lambda hwnd, **kw: (next(results), 0))
        return pad

    def test_frozen_game_is_named_and_input_is_left_moot(self, capsys, monkeypatch):
        """1.1 的防挂机暂停应该长这样：失焦后画面完全不动。"""
        pad = self._arm(monkeypatch,
                        [_stats(20.0), _stats(0.05), _stats(0.05)],
                        [True, False])
        probe.probe_focus_behaviour(True, 1234)
        out = capsys.readouterr().out
        assert "motion verdict: [frozen]" in out
        assert "input verdict : [moot]" in out
        assert pad.closed

    def test_running_but_ignoring_input(self, capsys, monkeypatch):
        pad = self._arm(monkeypatch,
                        [_stats(20.0), _stats(19.0), _stats(19.2)],
                        [True, False])
        probe.probe_focus_behaviour(True, 1234)
        out = capsys.readouterr().out
        assert "motion verdict: [running]" in out
        assert "input verdict : [ignored]" in out
        assert pad.closed

    def test_input_getting_through_is_reported(self, capsys, monkeypatch):
        self._arm(monkeypatch,
                  [_stats(20.0), _stats(18.0), _stats(40.0)],
                  [True, False])
        probe.probe_focus_behaviour(True, 1234)
        assert "input verdict : [reaching]" in capsys.readouterr().out

    def test_stops_if_the_game_is_not_in_front_for_the_baseline(self, capsys, monkeypatch):
        """基准段测错了，后面两段就毫无意义 —— 不如当场停下。"""
        self._arm(monkeypatch, [_stats(20.0)], [False])
        probe.probe_focus_behaviour(True, 1234)
        out = capsys.readouterr().out
        assert "NOT in front" in out
        assert "motion verdict" not in out

    def test_stops_if_the_user_never_clicked_away(self, capsys, monkeypatch):
        self._arm(monkeypatch, [_stats(20.0)], [True, True])
        probe.probe_focus_behaviour(True, 1234)
        out = capsys.readouterr().out
        assert "still in front" in out
        assert "motion verdict" not in out

    def test_unknown_focus_does_not_block_the_run(self, capsys, monkeypatch):
        """拿不到前台信息时不该硬停 —— 只有明确的 False/True 才是错。"""
        self._arm(monkeypatch,
                  [_stats(20.0), _stats(0.05), _stats(0.05)],
                  [None, None])
        probe.probe_focus_behaviour(True, 1234)
        assert "motion verdict" in capsys.readouterr().out

    def test_motion_verdict_survives_a_missing_gamepad(self, capsys, monkeypatch):
        """手柄坏了只该丢掉输入那一半，动静那一半照样成立。"""
        monkeypatch.setattr("builtins.input", lambda *a: "")
        states = iter([True, False])
        monkeypatch.setattr(probe, "_game_is_focused", lambda hwnd: next(states))
        results = iter([_stats(20.0), _stats(0.05)])
        monkeypatch.setattr(probe, "_capture_deltas",
                            lambda hwnd, **kw: (next(results), 0))
        monkeypatch.setattr(probe, "vigem", None)

        probe.probe_focus_behaviour(True, 1234)
        out = capsys.readouterr().out
        assert "Phase 3 skipped" in out
        assert "motion verdict: [frozen]" in out
        assert "input verdict" not in out

    def test_pad_is_unplugged_even_if_phase_three_explodes(self, capsys, monkeypatch):
        pad = _FakePad()
        monkeypatch.setattr("builtins.input", lambda *a: "")
        monkeypatch.setattr(probe.vigem, "VirtualGamepad", lambda: pad)
        states = iter([True, False])
        monkeypatch.setattr(probe, "_game_is_focused", lambda hwnd: next(states))

        calls = {"n": 0}

        def capture(hwnd, **kw):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise RuntimeError("capture died")
            return (_stats(20.0), 0)

        monkeypatch.setattr(probe, "_capture_deltas", capture)
        with pytest.raises(RuntimeError):
            probe.probe_focus_behaviour(True, 1234)
        assert pad.closed, "摇杆推着的虚拟手柄绝不能留在系统里"

    def test_section_output_is_ascii(self, capsys, monkeypatch):
        self._arm(monkeypatch,
                  [_stats(20.0), _stats(6.0), _stats(30.0)],
                  [True, False])
        probe.probe_focus_behaviour(True, 1234)
        capsys.readouterr().out.encode("ascii")


class _SlotDLL:
    """按槽位给不同读数的假 XInput。用来重现"幽灵手柄占着 0 号"。"""

    def __init__(self, live_slots, present_slots):
        self.live = set(live_slots)
        self.present = set(present_slots)

    def XInputGetState(self, index, buf):
        import xinput as _x
        slot = index.value if hasattr(index, "value") else int(index)
        if slot not in self.present:
            return _x.ERROR_DEVICE_NOT_CONNECTED
        buf._obj.dwPacketNumber = 1
        buf._obj.Gamepad.sThumbLY = 32767 if slot in self.live else 0
        return _x.ERROR_SUCCESS


class TestSlotSelection:
    """真机上 slots=[0, 1]、幽灵在 0 号，整段测量被判成 no-input 而作废。"""

    def _arm(self, monkeypatch, dll):
        pad = _FakePad()
        monkeypatch.setattr(probe.xinput, "available_libraries",
                            lambda: [("xinput1_4.dll", dll)])
        monkeypatch.setattr(probe.xinput, "foreground_window", lambda: 777)
        monkeypatch.setattr(probe.vigem, "VirtualGamepad", lambda: pad)
        monkeypatch.setattr("builtins.input", lambda *a: "")
        monkeypatch.setattr(probe.time, "sleep", lambda _s: None)
        return pad

    def test_skips_the_ghost_and_measures_the_live_slot(self, capsys, monkeypatch):
        dll = _SlotDLL(live_slots=[1], present_slots=[0, 1])
        pad = self._arm(monkeypatch, dll)
        recorded = {}

        def capture(d, **kw):
            recorded["index"] = kw["index"]
            return []

        monkeypatch.setattr(probe.xinput, "sample_focus", capture)
        probe.probe_xinput_focus(True)
        out = capsys.readouterr().out
        assert recorded["index"] == 1, "必须量有反应的那个槽位"
        assert "Measuring slot 1, not 0" in out
        assert pad.closed

    def test_normal_case_says_nothing_special(self, capsys, monkeypatch):
        dll = _SlotDLL(live_slots=[0], present_slots=[0])
        self._arm(monkeypatch, dll)
        monkeypatch.setattr(probe.xinput, "sample_focus", lambda d, **kw: [])
        probe.probe_xinput_focus(True)
        out = capsys.readouterr().out
        assert "measuring slot 0" in out
        assert "not 0" not in out

    def test_stops_when_no_slot_responds_to_the_stick(self, capsys, monkeypatch):
        """全中立时测下去只会得到一个假的 no-input 判定。"""
        dll = _SlotDLL(live_slots=[], present_slots=[0, 1])
        pad = self._arm(monkeypatch, dll)
        called = {"n": 0}

        def count(d, **kw):
            called["n"] += 1
            return []

        monkeypatch.setattr(probe.xinput, "sample_focus", count)
        probe.probe_xinput_focus(True)
        out = capsys.readouterr().out
        assert "no slot reports it" in out
        assert "finished unplugging" in out
        assert called["n"] == 0, "不该在没有有效槽位时还去采样"
        assert pad.closed

    def test_the_stick_goes_down_before_the_slot_is_chosen(self, monkeypatch):
        """顺序是这个修复的全部 —— 没推之前每个槽位都是中立的。"""
        order = []

        class Watching(_SlotDLL):
            def XInputGetState(self, index, buf):
                order.append("read")
                return super().XInputGetState(index, buf)

        dll = Watching(live_slots=[0], present_slots=[0])
        pad = self._arm(monkeypatch, dll)
        original = pad.left_stick_forward

        def note():
            order.append("stick")
            original()

        pad.left_stick_forward = note
        monkeypatch.setattr(probe.xinput, "sample_focus", lambda d, **kw: [])
        probe.probe_xinput_focus(True)
        assert "stick" in order, "摇杆必须被推下去"
        assert order.index("stick") < len(order) - 1
        assert order[order.index("stick") + 1] == "read", "推完立刻挑槽位"


class TestCaveatDirection:
    def test_no_os_gate_is_strengthened_not_questioned(self, capsys, monkeypatch):
        """真机第一次跑就撞上这条：报告让人去复测一个已经成立的结论。"""
        import xinput as _x
        dll = _SlotDLL(live_slots=[0], present_slots=[0])
        pad = _FakePad()
        monkeypatch.setattr(probe.xinput, "available_libraries",
                            lambda: [("xinput1_4.dll", dll)])
        monkeypatch.setattr(probe.xinput, "foreground_window", lambda: 777)
        monkeypatch.setattr(probe.vigem, "VirtualGamepad", lambda: pad)
        monkeypatch.setattr("builtins.input", lambda *a: "")
        monkeypatch.setattr(probe.time, "sleep", lambda _s: None)

        live = _x.Reading(1, 0, 0, 0, 0, 32767, 0, 0)
        monkeypatch.setattr(probe.xinput, "sample_focus", lambda d, **kw: [
            _x.Sample(0, True, live, False), _x.Sample(1, False, live, False)])

        probe.probe_xinput_focus(True)
        out = capsys.readouterr().out
        assert "verdict: [no-os-gate]" in out
        assert "STRONGER" in out
        assert "WARNING" not in out


class _FakeWI:
    """WindowInput 的替身，记录被调用的顺序。"""

    def __init__(self, stats="STATS on=0 iat=3 sub=1 cmds=2 bad=0 fg=30 "
                              "active=0 focus=0 kill=0 act=0 actapp=0",
                 inject_ok=True, inject_raises=None, watch_ok=True,
                 commands_sent=2):
        self.commands_sent = commands_sent
        self.calls = []
        self._stats = stats
        self._inject_ok = inject_ok
        self._inject_raises = inject_raises
        self._watch_ok = watch_ok

    def set_target(self, hwnd):
        self.calls.append(("target", hwnd))

    def enable_inject(self, progress_cb=None):
        self.calls.append(("inject",))
        if self._inject_raises:
            raise self._inject_raises
        return self._inject_ok

    def focus_spoof_stats(self):
        self.calls.append(("stats",))
        return self._stats

    def watch_focus_events(self):
        self.calls.append(("watch",))
        return self._watch_ok

    def enable_focus_spoof(self):
        self.calls.append(("spoof_on",))
        return True

    def disable_focus_spoof(self):
        self.calls.append(("spoof_off",))
        return True


class TestFocusHookSection:
    """第 9 段是整个探测器里唯一会**改变游戏进程**的部分。守住它的门。"""

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch):
        monkeypatch.setattr(probe.time, "sleep", lambda _s: None)

    def _arm(self, monkeypatch, wi, answers):
        replies = iter(answers)
        monkeypatch.setattr("builtins.input", lambda *a: next(replies, ""))
        monkeypatch.setattr(probe.window_input, "WindowInput", lambda: wi)
        return wi

    def test_without_the_flag_it_warns_before_anything_happens(self, capsys):
        probe.probe_focus_hook(False, 1234)
        out = capsys.readouterr().out
        assert "INJECTS A DLL" in out
        assert "cannot be unloaded" in out
        assert "--focus-hook-test" in out

    def test_no_window_is_skipped(self, capsys):
        probe.probe_focus_hook(True, None)
        assert "window was not found" in capsys.readouterr().out

    @pytest.mark.parametrize("answer", ["", "y", "yes", "INJECT ME", "no", "n"])
    def test_only_the_exact_word_inject_proceeds(self, capsys, monkeypatch, answer):
        """安全属性：除了正好输入 inject，任何输入都不能注入。

        'y' 也不行 —— 这一步和别处的 y/N 提示不是一回事，手滑不该把 DLL 塞进
        游戏进程。
        """
        wi = _FakeWI()
        self._arm(monkeypatch, wi, [answer])
        probe.probe_focus_hook(True, 1234)
        assert wi.calls == [], f"{answer!r} 不该导致任何动作"
        assert "Skipped" in capsys.readouterr().out

    def test_the_word_inject_is_accepted_case_insensitively(self, monkeypatch):
        wi = _FakeWI()
        self._arm(monkeypatch, wi, ["  Inject  ", ""])
        probe.probe_focus_hook(True, 1234)
        assert ("inject",) in wi.calls

    def test_injection_failure_points_at_elevation(self, capsys, monkeypatch):
        wi = _FakeWI(inject_raises=OSError("access is denied"))
        self._arm(monkeypatch, wi, ["inject"])
        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "Run as administrator" in out
        assert ("stats",) not in wi.calls

    def test_stage1_reports_the_mechanism(self, capsys, monkeypatch):
        wi = _FakeWI(stats="STATS on=0 fg=120 active=0 focus=0 kill=0 act=0 actapp=0")
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "mechanism: [polls]" in out
        assert "GetForegroundWindow=120" in out

    def test_message_driven_game_is_named(self, capsys, monkeypatch):
        wi = _FakeWI(stats="STATS on=0 fg=0 active=0 focus=0 kill=4 act=4 actapp=2")
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        assert "mechanism: [messages]" in capsys.readouterr().out

    def test_nothing_intercepted_stops_before_stage2(self, capsys, monkeypatch):
        """两边都是 0 时 stage 2 什么也证明不了 —— 不该白跑一遍。"""
        wi = _FakeWI(stats="STATS on=0 fg=0 active=0 focus=0 kill=0 act=0 actapp=0")
        self._arm(monkeypatch, wi, ["inject", "", "y"])
        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "mechanism: [no-hooks-hit]" in out
        assert "would prove nothing" in out
        assert ("spoof_on",) not in wi.calls

    def test_spoofing_is_turned_off_even_when_stage1_explodes(self, monkeypatch):
        """伪装开着而用户不知道，是这一段最坏的结局。"""
        wi = _FakeWI()
        self._arm(monkeypatch, wi, ["inject"])

        def boom(*a, **kw):
            raise RuntimeError("stage 1 died")

        monkeypatch.setattr(probe, "_focus_hook_stage1", boom)
        with pytest.raises(RuntimeError):
            probe.probe_focus_hook(True, 1234)
        assert ("spoof_off",) in wi.calls

    def test_stage2_measures_and_reports(self, capsys, monkeypatch):
        wi = _FakeWI(stats="STATS on=0 fg=99 active=0 focus=0 kill=0 act=0 actapp=0")
        self._arm(monkeypatch, wi, ["inject", "", "y"])
        focus = iter([True, False])
        monkeypatch.setattr(probe, "_game_is_focused", lambda h: next(focus, False))
        phases = iter([_stats(20.0), _stats(19.0)])
        monkeypatch.setattr(probe, "_capture_deltas", lambda h, **kw: (next(phases), 0))

        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert ("spoof_on",) in wi.calls
        assert "verdict: [running]" in out
        assert "#45 solved" in out
        assert ("spoof_off",) in wi.calls

    def test_stage2_says_so_when_the_spoof_did_not_work(self, capsys, monkeypatch):
        wi = _FakeWI(stats="STATS on=0 fg=99 active=0 focus=0 kill=0 act=0 actapp=0")
        self._arm(monkeypatch, wi, ["inject", "", "y"])
        focus = iter([True, False])
        monkeypatch.setattr(probe, "_game_is_focused", lambda h: next(focus, False))
        phases = iter([_stats(20.0), _stats(0.4)])
        monkeypatch.setattr(probe, "_capture_deltas", lambda h, **kw: (next(phases), 0))

        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "verdict: [frozen]" in out
        assert "did not stop the pause" in out

    def test_the_observer_is_armed_before_the_user_alt_tabs(self, monkeypatch):
        """顺序是硬要求。消息计数器靠窗口子类化，而子类化是 watch 装上的 ——
        在用户 alt-tab 之后才装，那三个计数器就只能是 0，判定于是永远落在
        polls 或 no-hooks-hit 上，无论游戏实际在做什么。
        """
        wi = _FakeWI()
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        names = [c[0] for c in wi.calls]
        assert names.index("watch") < names.index("stats")

    def test_arming_does_not_turn_spoofing_on(self, monkeypatch):
        """stage 1 说了"什么都不改"，那就一条 SPOOF_ON 都不能发。"""
        wi = _FakeWI()
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        assert ("spoof_on",) not in wi.calls

    def test_a_failed_arming_is_reported_not_swallowed(self, capsys, monkeypatch):
        """装不上就是"消息那一半没在测"。不说，读报告的人会把 0 当成结论。"""
        wi = _FakeWI(watch_ok=False)
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "Could not arm" in out
        assert "the message ones cannot" in out

    def test_the_report_says_whether_commands_landed(self, capsys, monkeypatch):
        """Python 只能说"写成功了"。cmds 是在管道另一头数的 —— 只有它能说明
        指令到底有没有到。"""
        wi = _FakeWI()
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        assert "delivery: [delivered]" in capsys.readouterr().out

    def test_commands_that_never_arrived_are_called_out(self, capsys, monkeypatch):
        """注入模式最坏的形态：管道收下了字节，DLL 一条都没执行，而没有任何地方
        会说这件事。"""
        wi = _FakeWI(stats="STATS on=0 iat=3 sub=1 cmds=0 bad=0 fg=30 active=0 "
                           "focus=0 kill=0 act=0 actapp=0",
                     commands_sent=7)
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "delivery: [not-delivered]" in out
        assert "going nowhere" in out

    def test_garbled_commands_are_called_out(self, capsys, monkeypatch):
        wi = _FakeWI(stats="STATS on=0 iat=3 sub=1 cmds=5 bad=2 fg=30 active=0 "
                           "focus=0 kill=0 act=0 actapp=0")
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        assert "delivery: [garbled]" in capsys.readouterr().out

    def test_the_report_says_whether_the_hooks_went_in(self, capsys, monkeypatch):
        wi = _FakeWI()
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "IAT patches=3/3" in out
        assert "window proc subclassed=yes" in out

    def test_hooks_that_never_installed_stop_before_stage2(self, capsys, monkeypatch):
        """全零的计数器在这里不是关于游戏的证据 —— 钩子压根没进去。
        照旧跑 stage 2 只会得到一个看起来像结论的东西。"""
        wi = _FakeWI(stats="STATS on=0 iat=0 sub=0 fg=0 active=0 focus=0 "
                           "kill=0 act=0 actapp=0")
        self._arm(monkeypatch, wi, ["inject", "", "y"])
        probe.probe_focus_hook(True, 1234)
        out = capsys.readouterr().out
        assert "mechanism: [not-installed]" in out
        assert ("spoof_on",) not in wi.calls

    def test_section_output_is_ascii(self, capsys, monkeypatch):
        wi = _FakeWI(stats="STATS on=0 iat=0 sub=0 fg=99 active=0 focus=0 "
                           "kill=2 act=0 actapp=0")
        self._arm(monkeypatch, wi, ["inject", "", "n"])
        probe.probe_focus_hook(True, 1234)
        capsys.readouterr().out.encode("ascii")
