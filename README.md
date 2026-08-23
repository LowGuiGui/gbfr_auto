# GBFR Auto

碧蓝幻想：Relink（Granblue Fantasy: Relink）自动战斗脚本，基于图像识别与键鼠模拟实现全自动循环战斗。

## 功能特性

- **图像识别页面检测**：通过 OpenCV 模板匹配自动识别当前游戏页面（战斗中/结算/奖励等）
- **全自动战斗循环**：识别到战斗场景自动按住 W 前进 + 鼠标中键，持续战斗
- **结算自动处理**：战斗结束后自动切换为"再战"
- **全局热键**：支持后台运行，即使窗口失焦也能响应按键
- **悬浮提示**：屏幕左上角显示运行状态
- **实时日志**：窗口内显示操作日志与战斗计数

## 前置条件

- 确保游戏语言为简中，按键与锁定视角设置为默认
- 确保当前显示为全屏
- 确保游戏分辨率与模板图片匹配，否则可能识别失败

## 快捷键

| 按键 | 功能 |
|---|---|
| **F1** | 启动自动循环 |
| **F2** | 停止自动循环 |

## 环境要求

- Python 3.10+
- Windows 系统

## 安装依赖

```bash
pip install pyautogui pynput pillow opencv-python numpy
```

## 环境自检工具

发行包里还有一个 `gbfr-probe.exe`。它只读，不改游戏、不改存档、不改配置，用来一次性查清本机环境：

- 游戏窗口是无边框、有边框还是独占全屏，边框偏移多少
- 进程的 DPI 感知等级、显示器布局
- 后台截图能不能拿到真实画面（会把截到的图存下来供确认）
- 存档文件在哪、有多大、什么时候写的

开着游戏双击运行即可，结果写在 `probe-out/probe-report.txt`。识别不正常时，先跑它。

## 模板图片

程序首次运行会把内置模板释放到可执行文件旁边的 `template/` 目录。游戏分辨率与模板不匹配时，可以直接替换这个目录里的图片。

升级到新版本时：

- 你没有改过的模板会自动更新；
- 你改过的模板**不会被覆盖**，新版内置模板会另存为同名 `.new` 文件放在旁边，需要时自行替换；
- `template/.bundled.json` 是程序用来分辨"这个文件有没有被改过"的记录，请勿手动编辑或删除。

## 编译 hook DLL（后台注入模式）

后台注入模式需要 `hook/gbfr_hook.dll`，由 `hook/gbfr_hook.c` 编译生成。该文件**不纳入版本控制**——二进制无法 diff、无法审阅——所以从源码运行前需要自行编译一次：

```bash
cd hook
build.bat
```

`build.bat` 会自动选择 Visual Studio Build Tools (`cl`) 或 MinGW (`gcc`)，两者任选其一即可。

未编译时程序照常运行，只有启用注入模式会提示"找不到 gbfr_hook.dll"并指向本节。CI 构建产物与 Release 压缩包中已包含编译好的 DLL，直接下载使用则无需这一步。

## 使用方法

1. 启动游戏并进入可重复战斗的界面
2. 运行脚本：

```bash
python main.py
```
## 打包为可执行文件

1. 安装 PyInstaller：

```bash
pip install pyinstaller
```

2. 打包为可执行文件

```bash
pyinstaller --onefile --windowed --name "GBFR Auto" --uac-admin --icon "icon.ico" --add-data "template;template" --add-data "icon.ico;." main.py
```

3. 按 **F1** 启动自动循环，屏幕左上角出现绿色提示表示已启动
4. 按 **F2** 停止循环，日志中会显示完成的战斗次数

## 项目结构

```
gbfr_auto/
├── main.py            # 主程序入口，包含 App 类与热键逻辑
├── option.py          # Option 类，封装键鼠操作（按住W、中键连点、按键等）
├── opencv.py          # 模板匹配工具，封装 OpenCV 图像识别函数
├── template/          # 模板图片目录，用于页面识别
│   ├── flag_battle.png
│   ├── flag_battleresult.png
│   ├── flag_again.png
│   ├── flag_exit.png
│   └── flag_continue.png
```

## 页面识别说明

| 页面状态 | 触发动作 |
|---|---|
| 战斗中 (BATTLE) | 按住 W 键 + 鼠标中键 |
| 奖励-再战 (REWARD_AGAIN) | 按 Enter 确认再战 |
| 奖励-退出 (REWARD_EXIT) | 按 3 切换到再战 |
| 暂停 (PAUSE) | 按 Enter 继续 |
| 其他 (UNKNOWN) | 按 Enter 推进流程 |

## 注意事项

- 本工具仅用于学习交流，请勿用于商业用途
- 使用前请确保游戏分辨率与模板图片匹配，否则可能识别失败
- 建议先在低难度副本测试，确认识别与操作逻辑无误后再长时间挂机
