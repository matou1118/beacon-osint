@echo off
setlocal
cd /d "%~dp0.."
python scheduler.py
exit /b %ERRORLEVEL%
