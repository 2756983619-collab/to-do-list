@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 用 pythonw 启动，不留黑框；找不到 pythonw 就退回 python 并显示错误
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "%~dp0app.py"
    exit /b 0
)
where pyw >nul 2>nul
if not errorlevel 1 (
    start "" pyw -3 "%~dp0app.py"
    exit /b 0
)
echo.
echo 没有找到 Python，请改用 dist\ToDoList\ToDoList.exe
echo.
pause
