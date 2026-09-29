@echo off
REM ===========================================================================
REM  Beacon lighthouse - detached background starter (ASCII ONLY)
REM
REM  Registered as a Scheduled Task (ONLOGON). This script only exists to give
REM  the task something simple to invoke; the real work is in launch.py, which
REM  creates the watchdog with a HIDDEN CONSOLE.
REM
REM  Why a hidden console and not "no console": with no console anywhere in
REM  the chain, playwright allocates a brand-new console for its node driver
REM  (conhost.exe appears and dies within a second, about once a minute on the
REM  desktop). A hidden console exists, is inherited by every child, and shows
REM  nothing - so nothing anywhere in the chain allocates its own.
REM
REM  Do NOT put non-ASCII in this file. cmd.exe reads batch files in the
REM  console code page (GBK here), so UTF-8 Chinese gets parsed as commands.
REM ===========================================================================
cd /d "%~dp0.."
"C:\Python310\pythonw.exe" lighthouse\launch.py %*
exit /b %ERRORLEVEL%
