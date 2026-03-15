@echo off
setlocal

set "ROOT=%~dp0"
set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=0.0.2"

set "APP_NAME=IdleHomeBotGUI"
set "BUILD_DIR=%ROOT%build"
set "DIST_ROOT=%ROOT%dist"
set "DIST_DIR=%DIST_ROOT%\%APP_NAME%"
set "RELEASE_ROOT=%ROOT%release"
set "RELEASE_DIR=%RELEASE_ROOT%\IdleHomeBot-v%VERSION%"
set "ZIP_PATH=%RELEASE_ROOT%\IdleHomeBot-v%VERSION%.zip"

echo.
echo [1/7] Checking Python...
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH.
  exit /b 1
)

echo.
echo [2/7] Installing or updating PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo Failed to install PyInstaller.
  exit /b 1
)

echo.
echo [3/7] Cleaning old build output...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"
if exist "%ZIP_PATH%" del /f /q "%ZIP_PATH%"

echo.
echo [4/7] Building %APP_NAME%...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --name "%APP_NAME%" ^
  --collect-all cv2 ^
  --collect-all numpy ^
  --collect-all PIL ^
  "%ROOT%idle_home_gui.py"
if errorlevel 1 (
  echo PyInstaller build failed.
  exit /b 1
)

if not exist "%DIST_DIR%" (
  echo Build output not found: %DIST_DIR%
  exit /b 1
)

echo.
echo [5/7] Copying runtime files into dist output...
copy /y "%ROOT%idle_home_config.json" "%DIST_DIR%\idle_home_config.json" >nul
if errorlevel 1 (
  echo Failed to copy idle_home_config.json into dist.
  exit /b 1
)
if exist "%ROOT%idle_home_config.extra.json" (
  copy /y "%ROOT%idle_home_config.extra.json" "%DIST_DIR%\idle_home_config.extra.json" >nul
)
copy /y "%ROOT%README.md" "%DIST_DIR%\README.md" >nul

robocopy "%ROOT%templates" "%DIST_DIR%\templates" /E >nul
if errorlevel 8 (
  echo Failed to copy templates into dist.
  exit /b 1
)

mkdir "%DIST_DIR%\failure_captures" >nul 2>nul

echo.
echo [6/7] Staging release files...
mkdir "%RELEASE_ROOT%" >nul 2>nul
mkdir "%RELEASE_DIR%" >nul 2>nul

robocopy "%DIST_DIR%" "%RELEASE_DIR%" /E >nul
if errorlevel 8 (
  echo Failed to copy executable files.
  exit /b 1
)
> "%RELEASE_DIR%\version.txt" (
  echo %VERSION%
)

echo.
echo [7/7] Creating zip package...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%RELEASE_DIR%\*' -DestinationPath '%ZIP_PATH%' -Force"
if errorlevel 1 (
  echo Failed to create zip archive.
  exit /b 1
)

echo.
echo Build complete.
echo Release folder: %RELEASE_DIR%
echo Release zip   : %ZIP_PATH%
exit /b 0
