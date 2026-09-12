@echo off
chcp 65001 >nul
REM =====================================================
REM  Reading Explorer 单元学习器 - 本地启动脚本
REM  用法：双击运行 或 在 cmd 里执行 start_server.bat
REM  启动后浏览器会自动打开 http://localhost:8080/index.html
REM  注意：
REM   1) 语音识别（按住说话）需在 localhost/https 下运行
REM   2) 有道在线查词由本服务器内置代理 /api/youdao 转发
REM =====================================================
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if %errorlevel%==0 (
    set PY=python
) else (
    where python3 >nul 2>nul
    if %errorlevel%==0 ( set PY=python3 ) else ( set PY= )
)

if "%PY%"=="" (
    echo [错误] 未找到 python / python3。
    echo 请先安装 Python（https://www.python.org/downloads/），勾选 "Add to PATH"，
    echo 然后重新运行本脚本。
    pause
    exit /b 1
)

echo [OK] 正在启动本地服务器： http://localhost:8080/index.html
start "" http://localhost:8080/index.html
"%PY%" scripts\server.py 8080
if errorlevel 1 (
    echo.
    echo [提示] server.py 启动失败，回退到简易静态服务器（在线查词代理不可用）。
    "%PY%" -m http.server 8080 --bind 127.0.0.1
)
endlocal
