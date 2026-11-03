@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行 setup.cmd 安装依赖。
    pause
    exit /b 1
)
echo 正在启动 CatchGPU，请保留此窗口。按 Ctrl+C 可停止服务。
".venv\Scripts\python.exe" run.py
if errorlevel 1 pause

