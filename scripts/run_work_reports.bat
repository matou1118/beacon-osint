@echo off
REM Periodic collection of government work reports + statistical communiques.
REM ASCII only on purpose: cmd.exe reads .bat files using the system ANSI
REM codepage (GBK here). A UTF-8 .bat with Chinese comments gets decoded as
REM mojibake, and one run produced AT's usage help because a garbled line was
REM parsed as a command. All Chinese output comes from Python, which runs with
REM PYTHONIOENCODING=utf-8 below.
REM
REM Mutual exclusion is handled by collectors/lock.py: each script takes the
REM collect lock and releases it before the next one runs.
setlocal
cd /d "D:\tvbox\BeaconOSINT"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo === work reports / communiques collection start %DATE% %TIME% ===
python -u scripts\collect_work_reports.py --timeout 900
echo --- reports exit code %ERRORLEVEL% ---

python -u scripts\collect_stat_communiques.py
echo --- communiques exit code %ERRORLEVEL% ---

echo === done %DATE% %TIME% ===
