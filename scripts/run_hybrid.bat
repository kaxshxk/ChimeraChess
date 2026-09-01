@echo off
:: ChimeraChess — Windows launcher for chess GUIs (CuteChess, Arena, etc.)
:: Register this .bat file as the engine executable in your GUI.
:: All arguments passed by the GUI (e.g. --movetime) are forwarded as-is.

setlocal

:: Resolve the directory this script lives in, then find the engine relative to it
set "SCRIPT_DIR=%~dp0"
set "ENGINE=%SCRIPT_DIR%..\src\hybrid_engine.py"

:: Check Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found on PATH. Install Python 3.10+ and add it to PATH.
    exit /b 1
)

python "%ENGINE%" %*
