@echo off
setlocal
cd /d "%~dp0"

REM UTF-8 console / Python IO
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>&1
  if %ERRORLEVEL%==0 (
    set "PY=python"
  ) else (
    echo.
    echo ERROR: Python was not found on PATH.
    echo Install Python 3 from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
  )
)

echo Upgrading pip...
%PY% -m pip install --upgrade pip -q
if errorlevel 1 (
  echo Failed to upgrade pip.
  pause
  exit /b 1
)

echo Installing requirements...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  pause
  exit /b 1
)

echo Starting GUI server...
%PY% gui_server.py
if errorlevel 1 (
  echo GUI server exited with an error.
  pause
  exit /b 1
)

endlocal
