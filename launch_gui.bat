@echo off
setlocal
cd /d "%~dp0"

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  set "PY_CMD=py -3"
)
if "%PY_CMD%"=="" (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=python"
  )
)

if "%PY_CMD%"=="" (
  echo Python was not found in PATH.
  pause
  exit /b 1
)

%PY_CMD% idle_home_gui.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo GUI exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
