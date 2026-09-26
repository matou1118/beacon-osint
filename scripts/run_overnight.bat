@echo off
REM Overnight collection runner. Detached via schtasks so it survives the
REM launching shell. Writes an unbuffered log and one JSON line per round.
setlocal
cd /d "D:\tvbox\BeaconOSINT"
echo === overnight run start %DATE% %TIME% ===
python -u scripts\batch_collect.py --limit 60 --workers 6 --timeout 300 --force --rounds 40 --pause 60
echo === overnight run end %DATE% %TIME% ===
python -u scripts\collection_report.py -o data\overnight_report.md
echo === report written %DATE% %TIME% ===
