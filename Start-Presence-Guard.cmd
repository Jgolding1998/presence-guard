@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -I -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 goto venv
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -I -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 goto launcher
)
where python >nul 2>nul
if not errorlevel 1 (
  python -I -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 goto python
)
echo Python 3.11 or newer is required. Get it from https://www.python.org/downloads/
echo In the official installer, enable Add python.exe to PATH, then open this file again.
pause
exit /b 1
:venv
".venv\Scripts\python.exe" -m presence_guard --demo --open
goto result
:launcher
py -3 -m presence_guard --demo --open
goto result
:python
python -m presence_guard --demo --open
:result
if errorlevel 1 pause
