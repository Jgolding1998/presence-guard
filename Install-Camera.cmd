@echo off
setlocal
cd /d "%~dp0"
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
pause
exit /b 1
:launcher
py -3 -I scripts\install_camera.py
goto result
:python
python -I scripts\install_camera.py
:result
pause
