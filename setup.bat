@echo off
setlocal
cd /d "%~dp0"
set "BOOTSTRAP_PY="
if exist ".venv\Scripts\python.exe" set "BOOTSTRAP_PY=.venv\Scripts\python.exe"
if not defined BOOTSTRAP_PY (
  where py >nul 2>nul && set "BOOTSTRAP_PY=py -3"
)
if not defined BOOTSTRAP_PY if exist "..\aistory\.venv\Scripts\python.exe" set "BOOTSTRAP_PY=..\aistory\.venv\Scripts\python.exe"
if not defined BOOTSTRAP_PY (
  if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "BOOTSTRAP_PY=%LocalAppData%\Programs\Python\Python311\python.exe"
)
if not defined BOOTSTRAP_PY (
  where winget >nul 2>nul
  if errorlevel 1 (
    echo Python 3.11 is required. Install it from https://www.python.org/downloads/ and rerun setup.bat.
    pause
    exit /b 1
  )
  echo Installing Python 3.11 for the current Windows user...
  winget install --id Python.Python.3.11 -e --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "BOOTSTRAP_PY=%LocalAppData%\Programs\Python\Python311\python.exe"
  if not defined BOOTSTRAP_PY (
    echo Python installation did not complete. Rerun setup.bat after Python finishes installing.
    pause
    exit /b 1
  )
)
if not exist ".venv\Scripts\python.exe" %BOOTSTRAP_PY% -m venv .venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install customtkinter requests keyring
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" setup_wizard.py
if errorlevel 1 pause
