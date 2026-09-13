@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "AutoTok" ".venv\Scripts\pythonw.exe" "app.py"
) else if exist "..\aistory\.venv\Scripts\python.exe" (
  "..\aistory\.venv\Scripts\python.exe" -m pip install -r requirements.txt
  start "AutoTok" "..\aistory\.venv\Scripts\pythonw.exe" "app.py"
) else (
  echo Python environment not found. Install Python 3.10+ and run setup.bat.
  pause
)
