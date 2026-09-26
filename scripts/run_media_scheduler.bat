@echo off
setlocal
cd /d "%~dp0.."
python scheduler.py --config config/media_sources.yaml --limit 3
exit /b %ERRORLEVEL%
