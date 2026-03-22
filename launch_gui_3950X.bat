@echo off
setlocal
cd /d "%~dp0"
call "%~dp0launch_gui.bat" idle_home_config_3950X.json
exit /b %ERRORLEVEL%
