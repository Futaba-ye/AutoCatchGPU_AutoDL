@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo 请先安装 Python 3.11 或更高版本，并勾选加入 PATH。
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo 安装完成。网页登录需要本机安装 Chrome 或 Edge。
echo 双击 start.cmd 启动 CatchGPU。
pause
exit /b 0
:failed
echo.
echo 安装失败，请检查上方错误信息及网络连接。
pause
exit /b 1

