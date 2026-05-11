@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo([ERROR] Missing .venv\Scripts\python.exe
    echo(Please install OpenAkita in this project folder first.
    pause
    exit /b 1
)

if not exist "scripts\openakita_gui_launcher.py" (
    echo([ERROR] Missing scripts\openakita_gui_launcher.py
    pause
    exit /b 1
)

if "%OPENAKITA_LAUNCHER_SELFTEST%"=="1" (
    echo(SELFTEST_OK
    exit /b 0
)

".venv\Scripts\python.exe" "scripts\openakita_gui_launcher.py"
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
    echo(
    echo(OpenAkita GUI launcher exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
