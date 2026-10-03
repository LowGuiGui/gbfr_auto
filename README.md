简体中文 | [English](README.en.md)

# GBFR Auto

[![Project Status: Suspended – Initial development has started, but there has not yet been a stable, usable release; work has been stopped for the time being but the author(s) intend on resuming work.](https://www.repostatus.org/badges/latest/suspended.svg)](https://www.repostatus.org/#suspended)

> [!IMPORTANT]
> **Windows 版开发已于 2026-10-02 暂停，本仓库已归档为只读。**
>
> - 项目转向 Linux，后续开发在新仓库 [LowGuiGui/gbfr-auto-linux](https://github.com/LowGuiGui/gbfr-auto-linux) 进行。
> - Windows 版可能随时恢复，方法见[恢复开发](#恢复开发)。
> - **目前它不能正常使用**，所以没有发布任何可下载的版本。想继续研究的人请按[打包为可执行文件](#打包为可执行文件)自行构建。

> [!WARNING]
> **AI 编写声明。** 本 fork 相对上游的改动由 AI 编程助手（Anthropic 的 Claude Code）在仓库所有者的指示下编写，**没有经过人工逐行审查**。
>
> - 只有[功能状态](#功能状态)中标为 ✅ 的部分在真机上运行过；其余只有自动化测试，或从未运行。
> - 代码会向游戏进程注入 DLL 并改变游戏对窗口焦点的判断。运行前请自行审阅，风险自负。
>
> 详见[关于 AI 编写](#关于-ai-编写)。

碧蓝幻想：Relink（Granblue Fantasy: Relink）自动战斗脚本，基于图像识别与键鼠模拟实现全自动循环战斗。

本仓库是 [zhiyual/gbfr_auto](https://github.com/zhiyual/gbfr_auto) 的 fork。本中文说明为准，[英文版](README.en.md)是它的翻译。

## 功能状态

暂停时（2026-10-02）的真实状态。图例：

- ✅ 在所有者的真机上运行过
- 🧪 只有自动化测试
- ⚠️ 已构建，从未运行
- ⏳ 未完成
- 🐞 已知缺陷

| 功能 | 状态 | 依据 |
|---|---|---|
| 模板识别页面、战斗循环、结算再战（上游功能） | 🧪 | 本 fork 改动之后，所有者没有在真机上重新跑过完整循环；页面判定与派发有单元测试 |
| 环境自检工具 `gbfr-probe.exe` | ✅ | 2026-08-24 至 08-26 在所有者机器上多次运行，下面各项实测结论都来自它 |
| 后台截图（PrintWindow） | ✅ | 窗口化与无边框下都截到真实画面 |
| 虚拟 Xbox 手柄（ViGEmBus） | ✅ 仅前台 | 首次即连上，游戏有焦点时角色会动；游戏失焦后会自己暂停 |
| 焦点伪装：游戏失焦后不暂停（[#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)） | ✅ 仅探测器 | 2026-08-26 两次实测（探测器第 9 段）：开启后游戏失焦仍在运行，键盘和实体手柄输入都有效。**主程序从不开启它** |
| 注入通道读取回复的修复（[#65](https://github.com/LowGuiGui/gbfr_auto/pull/65)） | ✅ | 上面两次实测走的就是修复后的通道 |
| 键鼠 / 虚拟手柄两种操作方式、自动恢复、F12 急停（[#66](https://github.com/LowGuiGui/gbfr_auto/pull/66)） | ⚠️ | 自动化测试通过；从未对游戏运行（测试 G1–G3 没做）；手柄按键映射是猜的 |
| 配置文件、日志文件、诊断开关（分数记录、异常帧、空跑） | 🧪 | 单元测试 |
| 模板目录升级时不覆盖用户改过的模板 | 🧪 | 单元测试 |
| 提权失败时非零退出（#1）、注入看门狗竞态（#2） | 🧪 | 单元测试 |
| 中键点在客户区中心（[#46](https://github.com/LowGuiGui/gbfr_auto/issues/46) 前半） | 🧪 | 计算有单元测试；真机复核没做 |
| 主程序在手柄模式下开启焦点伪装 | ⏳ | 未开始，是后台刷本缺的最后一块 |
| 从存档读取物品数量（[#48](https://github.com/LowGuiGui/gbfr_auto/issues/48)） | ⏳ | 已确认存档可读、游戏过程中会写盘；解析器未写 |
| 多分辨率模板匹配（[#12](https://github.com/LowGuiGui/gbfr_auto/issues/12)） | ⏳ | 未开始 |
| 目标窗口留空（"全屏"）时不发送任何输入 | 🐞 | 继承自上游 dev 分支的重构：输入层必须有目标窗口 |
| 截图包含标题栏和边框（[#46](https://github.com/LowGuiGui/gbfr_auto/issues/46) 后半） | 🐞 | 实测截图 1942×1136，而客户区是 1920×1080 |
| 只做了系统 DPI 感知（[#47](https://github.com/LowGuiGui/gbfr_auto/issues/47)） | 🐞 | 跨不同缩放的显示器时坐标会出错 |
| 注入模式发出的仍是系统级键鼠事件 | 🐞 | 事件落到当前前台窗口，并会移动真实光标；它只是省掉了抢焦点这一步 |
| 游戏失焦时虚拟手柄会操作别的程序（[#53](https://github.com/LowGuiGui/gbfr_auto/issues/53)） | 🐞 | 最可能是 Steam 输入的"桌面布局"；验证测试（F2）没做 |
| 键鼠模式下开启焦点伪装会把光标锁在游戏窗口中央 | 🐞 | 2026-08-26 实测；所以伪装只允许在手柄模式下使用 |

## 功能特性

- **图像识别页面检测**：通过 OpenCV 模板匹配自动识别当前游戏页面（战斗中、结算、奖励、暂停等）
- **全自动战斗循环**：识别到战斗场景后按住 W 前进，并按住鼠标中键，持续战斗
- **结算自动处理**：战斗结束后自动切换到"再战"并确认
- **全局热键**：即使窗口失焦也能响应 F1 / F2 / F12
- **悬浮提示**：屏幕左上角显示运行状态
- **实时日志**：窗口内显示操作日志与战斗计数，同时写入日志文件
- **两种操作方式**（⚠️ 未经真机验证）：键盘鼠标，或虚拟 Xbox 手柄
- **配置文件**：`gbfr_auto.toml`

## 前置条件

- 确保游戏语言为简中，按键与锁定视角设置为默认
- 在"目标窗口"里选中游戏窗口。**不要留空**：留空时会截取全屏，但不会发送任何输入
- 确保游戏分辨率与模板图片匹配，否则可能识别失败
- 程序启动时会请求管理员权限

## 快捷键

| 按键 | 功能 |
|---|---|
| **F1** | 启动自动循环 |
| **F2** | 停止自动循环 |
| **F12** | 急停：松开所有按键、关闭焦点伪装并停止循环（⚠️ 未经真机验证） |

## 环境要求

- Windows 10 / 11（所有者实测 Windows 11 26200）
- Python 3.12+（numpy 2.5.2 要求 3.12 及以上；CI 用 3.13）

## 安装依赖

```bash
pip install -r requirements.txt
```

Windows 上会一并装上 pywin32。打包、测试和代码检查用的依赖在 `requirements-dev.txt`。

## 配置文件

首次运行时，会在程序旁生成一份带注释的 `gbfr_auto.toml`。请以 UTF-8 保存；用记事本的"ANSI"保存会让中文注释变成乱码。常用项：

| 配置 | 作用 |
|---|---|
| `[input] backend` | `"kmb"` 键鼠，或 `"pad"` 虚拟手柄 |
| `[input] mode` | 键鼠通道：`"fallback"`（抢焦点），或 `"inject"`（注入 DLL） |
| `[input] dry_run` | `true` 时照常识别并记录，但不发送任何输入 |
| `[detect] log_scores` / `save_anomaly_frames` | 记录每个模板的匹配分数 / 认不出页面时保存截图，调试识别用 |
| `[pad]` | 手柄按键映射。**默认值是猜的** |

## 环境自检工具

`gbfr-probe.exe` 用来一次性查清本机环境，不加参数时只读：不改游戏、不改存档、不改配置。它会检查：

- 游戏窗口是无边框、有边框还是独占全屏，边框偏移多少
- 进程的 DPI 感知等级、显示器布局
- 后台截图能不能拿到真实画面（截到的图会存下来供确认）
- 存档文件在哪、有多大、什么时候写的
- 游戏加载了哪些输入相关的模块

**先开游戏**，再运行它。结果写在 exe 同目录下的 `gbfr-probe-report.txt`，截图写成 `gbfr-probe-capture.png`。识别不正常时，先跑它。

| 参数 | 作用 |
|---|---|
| `--title "关键字"` | 窗口标题对不上时指定。找不到窗口时，它会列出所有可见窗口的标题 |
| `--gamepad-test` | 真的推动虚拟手柄的摇杆（会向游戏发送输入） |
| `--xinput-test` | 测量失焦时 Windows 会不会把 XInput 清零 |
| `--focus-test` | 测量游戏失焦后是暂停了，还是只是不理输入 |
| `--focus-hook-test` | **向游戏注入 hook DLL** 并测试焦点伪装（会先询问） |

需要读取游戏进程的部分要以管理员身份运行，因为用 Reloaded-II 启动的游戏以管理员权限运行。注入前请先重启游戏：DLL 无法卸载，旧版本会留在进程里，悄悄忽略新版本发出的指令。

报告内容是英文的：非中文版 Windows 的控制台代码页（cp437/cp1252）编不了中文，`print` 中文会直接抛异常。报告是逐行落盘的：即使中途出错，已经查到的部分和错误堆栈也都会留在文件里，把它整个贴出来即可。

## 模板图片

程序首次运行会把内置模板释放到可执行文件旁边的 `template/` 目录。游戏分辨率与模板不匹配时，可以直接替换这个目录里的图片。

升级到新版本时：

- 你没有改过的模板会自动更新；
- 你改过的模板**不会被覆盖**，新版内置模板会另存为同名 `.new` 文件放在旁边，需要时自行替换；
- `template/.bundled.json` 是程序用来分辨"这个文件有没有被改过"的记录，请勿手动编辑或删除。

## 编译 hook DLL（注入模式）

注入模式需要 `hook/gbfr_hook.dll`，由 `hook/gbfr_hook.c` 编译生成。该文件**不纳入版本控制**（二进制无法 diff、无法审阅），所以从源码运行前需要自行编译一次：

```bash
cd hook
build.bat
```

`build.bat` 会自动选择 Visual Studio Build Tools（`cl`）或 MinGW（`gcc`），两者任选其一即可。也可以在 Linux 上用 MinGW 交叉编译：

```bash
x86_64-w64-mingw32-gcc -shared -O2 -o hook/gbfr_hook.dll hook/gbfr_hook.c -luser32
```

没编译时程序照常运行，只有启用注入模式时会提示"找不到 gbfr_hook.dll"。本项目**没有发布版本**；CI 的构建产物里有编译好的 DLL，但只保留 90 天。

## 使用方法

1. 启动游戏并进入可重复战斗的界面
2. 运行脚本：

```bash
python main.py
```

3. 在"目标窗口"里选中游戏窗口，再选输入模式和操作方式
4. 按 **F1** 启动自动循环，屏幕左上角出现绿色提示表示已启动
5. 按 **F2** 停止循环，日志中会显示完成的战斗次数；按 **F12** 急停

## 打包为可执行文件

PyInstaller 不能交叉编译，只能在 Windows 上打包。经过验证的完整流程就是 CI 的 [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml)：在你自己的 fork 里手动运行它（workflow_dispatch），即可得到两个 exe 和 DLL。手动打包的步骤如下。

1. 安装依赖（含 PyInstaller）：

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

2. 打包主程序：

```bash
pyinstaller --onefile --windowed --name "GBFR Auto" --uac-admin --icon "icon.ico" --add-data "template;template" --add-data "icon.ico;." main.py
```

3. 把编译好的 DLL 放到 exe 旁边：`dist/hook/gbfr_hook.dll`。它必须在 exe 旁边，不能打进 exe 里。
4. 打包环境自检工具（可选）。先从 [vgamepad 0.1.0](https://pypi.org/project/vgamepad/0.1.0/) 的源码包（MIT 许可）里取出 `vgamepad/win/vigem/client/x64/ViGEmClient.dll`，放进 `vigem_bin/`，再运行：

```bash
pyinstaller --noconfirm --onefile --console --name "gbfr-probe" --icon "icon.ico" --add-data "vigem_bin;vigem_bin" --paths . tools/windows_probe.py
```

`--paths .` 不能省：脚本在 `tools/` 下，而它导入的模块在仓库根目录。

虚拟手柄还需要 ViGEmBus 驱动，程序不内置。请安装官方的 [ViGEmBus 1.22.0](https://github.com/ViGEm/ViGEmBus/releases/tag/v1.22.0)；它是最后一版，项目已于 2023 年归档（[#49](https://github.com/LowGuiGui/gbfr_auto/issues/49)）。

## 项目结构

```
gbfr_auto/
├── main.py               # 主程序：界面、热键、主循环、页面判定表与派发
├── pages.py              # 页面判定规则树（纯函数）
├── option.py             # 动作层：前进、开打、再来、确认，以及状态调和
├── backend.py            # 输入后端：键鼠 / 虚拟手柄 / 空
├── supervisor.py         # 调和规则（纯函数）：窗口丢失、游戏重启、实体手柄接管等
├── window_capture.py     # 窗口截图：PrintWindow，失败时改用前台截图
├── window_input.py       # 键鼠通道：兼容（pynput）/ 注入（hook DLL + 命名管道）
├── opencv.py             # 模板匹配与空白帧检测
├── geometry.py           # 窗口矩形与客户区换算
├── framediff.py          # 帧差分（自检工具用）
├── config.py             # 配置文件 gbfr_auto.toml
├── applog.py             # 日志
├── vigem.py              # 虚拟 Xbox 手柄（ViGEmClient）
├── xinput.py             # 读取 XInput 手柄状态
├── procinfo.py           # 进程与已加载模块
├── hook/                 # 注入用 DLL 的源码（gbfr_hook.c）与注入器
├── tools/windows_probe.py  # 环境自检工具 gbfr-probe.exe
├── tests/                # 在 Linux 上运行的测试（Windows 边界由 tests/conftest.py 打桩）
└── template/             # 模板图片
```

## 页面识别说明

每 3 秒截一次图（`loop.poll_interval_ms`），按下表从上到下判定：

| 页面状态 | 判定 | 触发动作（键鼠模式） |
|---|---|---|
| 战斗中 (BATTLE) | `flag_battle` | 按住 W，并在客户区中心按住鼠标中键 |
| 结算-再战 (REWARD_AGAIN) | `flag_battleresult` + `flag_again` | 按 `a`（`keys.confirm`）确认再战 |
| 结算-退出 (REWARD_EXIT) | `flag_battleresult` + `flag_exit` | 按 `3`（`keys.again`）切换到再战 |
| 结算 (SCORE) | 只有 `flag_battleresult` | 按 `a` |
| 暂停 (PAUSE) | `flag_continue` | 按 `a` 继续 |
| 其他 (UNKNOWN) | 都不匹配 | 按 `a` 推进流程，最多连续 5 次（`loop.max_blind_taps`），之后停手并告警 |

- 上游的说明写的是"按 Enter"，实际按的一直是 `a`。
- 本工具每局都在结算页手动选择再战，不使用游戏自带的连续再战（10 次上限），所以不受那个上限影响。
- 手柄模式下：前进是左摇杆向前，开打是右摇杆按下，再来是 Y，确认是 A。**这些映射都是未经验证的猜测**。

## 暂停时的进度

按原计划的顺序：

1. **F2**：确认 Steam 输入的"桌面布局"是不是 [#53](https://github.com/LowGuiGui/gbfr_auto/issues/53) 的原因。这是可逆测试：先拔掉实体手柄，再把桌面布局里左摇杆的绑定改成"无"。**不要**点桌面布局里的"禁用 Steam 输入"，有用户报告点了之后无法恢复。
2. **G1**：核对手柄按键映射（[#66](https://github.com/LowGuiGui/gbfr_auto/pull/66)）。
3. **G2 / G3**：自动恢复（拖动窗口、切换模式、重启游戏等），以及拿起实体手柄时自动让开（#66）。
4. 让主程序在手柄模式下注入 DLL 并开启焦点伪装，这是后台刷本（[#45](https://github.com/LowGuiGui/gbfr_auto/issues/45)）缺的最后一块。
5. 每显示器 DPI 感知（[#47](https://github.com/LowGuiGui/gbfr_auto/issues/47)，要和对应的自检输出一起做），然后把截图改成客户区（[#46](https://github.com/LowGuiGui/gbfr_auto/issues/46)）。
6. 多分辨率匹配（[#12](https://github.com/LowGuiGui/gbfr_auto/issues/12)）。先打开 `log_scores`、`save_anomaly_frames` 和 `dry_run`，收集真实的匹配分数，再据此调整。
7. 停靠在游戏窗口旁的状态面板。
8. 从存档读物品数量（[#48](https://github.com/LowGuiGui/gbfr_auto/issues/48)）。只读：先复制再解析，绝不写存档。

还没回答的问题：游戏在后台是否降帧。两次实测结果矛盾（153% 和 15%），且都没有控制场景。

## 实测环境

以下都来自所有者的机器（2026-08-24 至 08-26）：

- Windows 11 26200；GBFR 2.0.4；简体中文界面；窗口化
- 主显示器 3840×2160，150% 缩放
- 游戏经 Reloaded-II 启动，以管理员权限运行
- 窗口边框：左 11、上 45、右 11、下 11（1440p 和 1080p 都一样）
- PrintWindow 在窗口化和无边框下都返回真实画面
- 存档 `%LOCALAPPDATA%\GBFR\Saved\SaveGames\SaveData1.dat` 约 22 MB，游戏过程中也会写盘
- ViGEmBus 1.22.0 已安装且可用
- 游戏失焦时会自己暂停（1.1 版加入的防挂机机制，三条独立证据）。Windows 本身并不在失焦时屏蔽 XInput，但这个结论只来自一次实测

## 恢复开发

1. 在仓库的 Settings → Danger Zone 点 "Unarchive this repository"，或运行 `gh repo unarchive LowGuiGui/gbfr_auto`。
2. 旧的 CI 记录和构建产物都已过期（公开仓库最多保留 90 天）。先在 dev 上重新构建一次：`gh workflow run build-windows.yml --ref dev`。下载产物时用 `gh run download <RUN_ID> -D <目录>`；gh 2.46 不加 `-D` 会误报路径穿越。
3. 先读置顶的状态 issue 和[暂停时的进度](#暂停时的进度)。
4. 每次注入前先重启游戏。如果旧 DLL 还在进程里，自检报告会显示 `window proc subclassed=no`。
5. 开发约定：
   - 每个改动一个分支，通过 PR 合并到 dev。dev 受保护，需要 `ruff`、`pytest (ubuntu)` 和 `build (windows-latest)` 三项检查通过。
   - 测试在 Linux 上运行（`pytest`），Windows 边界在 `tests/conftest.py` 里打桩。
   - CI 会在 Windows 上用真实的 win32 模块再跑一遍同一套测试。

## 关于 AI 编写

- **工具**：Anthropic 的 Claude Code（Claude Opus 5 / 5.5 模型），在仓库所有者的指示下工作。
- **范围**：
  - 本 fork 从 2026-08-23 起对上游所做的改动。
  - 除 2026-08-23 的 3 个初始配置提交（ruff 与 pre-commit 配置、依赖版本、恢复 LICENSE）外，本 fork 的每个非合并提交都带有 `Co-Authored-By: Claude` 标注。
  - 上游作者的原始代码不在此列。
- **审查**：**没有人工逐行审查。** 所有者负责方向和真机测试；真机上测过什么，见[功能状态](#功能状态)里的 ✅。
- **验证**：
  - Linux 上有 558 个自动化测试；CI 会在 Windows 上用真实的 win32 模块再跑一遍。
  - 测试覆盖不到真实的 Win32 调用、注入与命名管道、Tk 界面，以及任何需要游戏本身的行为。
- **风险**：代码会向游戏进程注入 DLL，并改变游戏对窗口焦点的判断。GPL-2.0 不提供任何担保（第 11、12 条）。
- **版权**：AI 生成的内容能否受版权保护，目前在法律上尚无定论；在受保护的范围内，本 fork 的改动按 GPL-2.0 授权，见 [COPYRIGHT](COPYRIGHT)。AI 的输出也可能与其训练数据相似。
- **请勿**把这些代码提交给禁止 AI 生成内容的项目（例如 Gentoo、NetBSD、QEMU）。
- 本说明同样由这个 AI 起草，所有者在发布前审阅过。

## 注意事项

- 本工具仅用于学习交流，请勿用于商业用途
- 使用前请确保游戏分辨率与模板图片匹配，否则可能识别失败
- 建议先在低难度副本测试，确认识别与操作逻辑无误后再长时间挂机

## 许可证

GPL-2.0，全文见 [LICENSE](LICENSE)。上游作品归上游作者所有；本 fork 改动的版权说明见 [COPYRIGHT](COPYRIGHT)。
