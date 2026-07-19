@echo off
REM 在 hook 目录下运行此脚本编译 DLL
REM 需要: Visual Studio Build Tools 或 MinGW (gcc)

setlocal

where cl >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [MSVC] 编译中...
    cl /LD /O2 /Fe:gbfr_hook.dll gbfr_hook.c user32.lib
    goto :done
)

where gcc >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [MinGW] 编译中...
    gcc -shared -O2 -o gbfr_hook.dll gbfr_hook.c -luser32
    goto :done
)

echo 未找到编译器。请安装 Visual Studio Build Tools 或 MinGW (gcc)。
exit /b 1

:done
echo.
echo 编译完成: gbfr_hook.dll
endlocal
