@echo off
REM Overnight self-iteration runner: collect -> clean -> refill -> amounts ->
REM summary -> classify -> dedup -> audit, in one command, then a health report.
REM
REM Changed from calling batch_collect.py directly: collecting alone leaves the
REM DB unclassified, unsummarised and without amounts, so the morning review had
REM nothing to look at. The whole point of running overnight is that the data
REM comes back processed. The drain flags make collection keep going until every
REM source is exhausted instead of taking N items per source and stopping.
REM
REM ASCII only in this file on purpose: cmd.exe reads .bat using the system ANSI
REM codepage (GBK here). A UTF-8 .bat with Chinese comments gets decoded as
REM mojibake, and one run produced AT's usage help because a garbled line was
REM parsed as a command. All Chinese output comes from Python, which runs with
REM PYTHONIOENCODING=utf-8 below.
REM
REM Mutual exclusion is handled by collectors/lock.py plus the pipeline's own
REM stale-lock cleanup, so a manual run during the night will not corrupt the DB.
setlocal
cd /d "D:\tvbox\BeaconOSINT"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo === overnight start %DATE% %TIME% ===
python -u -B scripts\pipeline.py --drain --limit 60 --workers 6 --timeout 300
echo --- pipeline exit code %ERRORLEVEL% ---

echo === report %DATE% %TIME% ===
python -u -B scripts\collection_report.py -o data\overnight_report.md
echo --- report exit code %ERRORLEVEL% ---

REM Health check last, and non-fatal: it is a report, not a gate. A failed
REM health check must not make the task look like the collection failed.
python -u -B scripts\overnight_health.py
echo --- health exit code %ERRORLEVEL% ---

echo === overnight done %DATE% %TIME% ===
