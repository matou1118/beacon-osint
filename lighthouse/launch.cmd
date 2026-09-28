@echo off
REM ===========================================================================
REM  Beacon lighthouse - detached background starter (ASCII ONLY)
REM
REM  Registered as a Windows Scheduled Task (ONLOGON) so it survives the
REM  launching session and restarts after a reboot.
REM
REM  Uses pythonw.exe, the windowless Python. The watchdog logs to a file, so
REM  it does not need stdout, and a console-less parent means its children
REM  cannot inherit one either. Using the console python here left a cmd
REM  window on the desktop, and every child of it could pop its own.
REM
REM  Do NOT put non-ASCII in this file. cmd.exe reads batch files in the
REM  console code page (GBK on this machine), so UTF-8 Chinese here gets
REM  parsed as commands and the script dies before creating its log.
REM ===========================================================================
cd /d "%~dp0.."

if exist "C:\Python310\pythonw.exe" (
    "C:\Python310\pythonw.exe" lighthouse\watchdog.py %*
) else (
    python lighthouse\watchdog.py %*
)
exit /b %ERRORLEVEL%
