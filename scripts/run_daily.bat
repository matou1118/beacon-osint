@echo off
setlocal
cd /d "%~dp0.."
python scheduler.py --once
exit /b %ERRORLEVEL%
