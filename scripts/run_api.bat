@echo off
setlocal
cd /d "%~dp0.."
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
exit /b %ERRORLEVEL%
