@echo off
REM Overnight collection runner. Detached via schtasks + hidden_run.vbs so it
REM survives the launching shell and shows no console window.
REM PYTHONIOENCODING is set because Windows otherwise writes this log in the
REM console codepage (cp936), which makes the Chinese progress lines unreadable.
setlocal
cd /d "D:\tvbox\BeaconOSINT"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo === overnight run start %DATE% %TIME% ===
python -u scripts\batch_collect.py --limit 60 --workers 6 --timeout 300 --force --rounds 40 --pause 120 --stop-when-dry 3
echo === overnight run end %DATE% %TIME% ===
python -u scripts\collection_report.py -o data\overnight_report.md
echo === report written %DATE% %TIME% ===
