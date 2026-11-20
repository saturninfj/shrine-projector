@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."
cd /d "%PROJECT_DIR%"

echo ==================================================
echo           SHRINEPROJECTOR WINDOWS BOOT            
echo ==================================================

:: Check ffmpeg
where ffmpeg >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [-] Error: ffmpeg is required but not found in PATH.
    echo [*] Install via winget: winget install Gyan.FFmpeg
    pause
    exit /b 1
)

:: Check Python
set "PYTHON_BIN=python"
if exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_DIR%\.venv\Scripts\python.exe"
)

:: Check config
set "CONFIG_FILE=%PROJECT_DIR%\config\shrine-config.json"
if not exist "%CONFIG_FILE%" (
    echo [-] Error: config\shrine-config.json not found.
    echo [*] Initializing configuration...
    "%PYTHON_BIN%" scripts\init-config
    if %ERRORLEVEL% neq 0 (
        echo [-] Failed to generate configuration.
        pause
        exit /b 1
    )
)

echo [+] Project Root: %PROJECT_DIR%
echo [+] Launching ShrineProjector daemon on Windows...
echo --------------------------------------------------

"%PYTHON_BIN%" -m server.app
