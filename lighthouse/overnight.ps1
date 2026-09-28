# lighthouse overnight collection driver.
#
# WHY THIS IS ASCII-ONLY, AND WHY IT IS NOT A .bat
# -------------------------------------------------
# A .bat cannot be used here: cmd.exe reads batch files in the console code
# page (GBK on this machine), so UTF-8 Chinese comments are parsed as
# commands and the script dies before it can even create its log file.
#
# A .ps1 written without a UTF-8 BOM has the same class of problem: Windows
# PowerShell 5.1 assumes ANSI/GBK for BOM-less files, and the multi-byte
# sequences corrupt the parser (it reported "value is not a valid numeric"
# on a perfectly ordinary `[int]$Rounds = 12`).
#
# So: this file is deliberately pure ASCII. Chinese documentation lives in
# lighthouse/README.md, which is never parsed by anything.
#
# USAGE
#   powershell -ExecutionPolicy Bypass -File lighthouse\overnight.ps1
#   powershell -ExecutionPolicy Bypass -File lighthouse\overnight.ps1 -Rounds 12 -Minutes 50
#
# PARAMS
#   -Rounds        max rounds to run
#   -Minutes       hard time limit per round
#   -DeadlineHour  stop when the clock hour reaches this (0-23)
#   -Stale         stop a source after N consecutive already-collected URLs.
#                  MUST be raised for overnight runs: lists are sorted newest
#                  first, so the first few dozen are already in the DB. The
#                  default in web_collector_config.yaml is 2, which makes an
#                  overnight run skip known URLs and collect nothing at all.
#   -Limit         max records per source per round
#   -Workers       source-level concurrency
#   -Fresh         ignore the known-URL set and re-collect everything
param(
    [int]$Rounds = 12,
    [int]$Minutes = 50,
    [int]$DeadlineHour = 7,
    [int]$Stale = 40,
    [int]$Limit = 25,
    [int]$Workers = 6,
    [switch]$Fresh
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root "data\runtime\lighthouse"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir "overnight.log"

function Write-Log([string]$text) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
    Write-Output $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

Write-Log ("=" * 74)

# The deadline is the NEXT occurrence of DeadlineHour:00, not "hour >= DeadlineHour".
# Comparing the hour alone is wrong: at 19:39 with a 07:00 deadline, "19 >= 7" is
# true, so the run stopped instantly with zero rounds. Compute the absolute
# deadline once and compare timestamps.
$startedAt = Get-Date
$deadline = $startedAt.Date.AddHours($DeadlineHour)
if ($deadline -le $startedAt) { $deadline = $deadline.AddDays(1) }

Write-Log ("START rounds={0} minutes/round={1} stale={2} limit={3} workers={4} fresh={5}" -f `
    $Rounds, $Minutes, $Stale, $Limit, $Workers, [bool]$Fresh)
Write-Log ("deadline = {0}  ({1} h from now)" -f $deadline.ToString("yyyy-MM-dd HH:mm"), `
    [math]::Round(($deadline - $startedAt).TotalHours, 1))

for ($i = 1; $i -le $Rounds; $i++) {
    if ((Get-Date) -ge $deadline) {
        Write-Log ("deadline {0} reached, stopping after {1} round(s)" -f $deadline.ToString("HH:mm"), ($i - 1))
        break
    }

    $pyArgs = @("-u", "lighthouse\run.py", "--all",
                "--limit", $Limit, "--max-minutes", $Minutes,
                "--max-stale", $Stale, "--workers", $Workers, "--ingest")
    if ($Fresh) { $pyArgs += "--fresh" }

    Write-Log ("--- round {0}/{1} start ---" -f $i, $Rounds)
    $started = Get-Date
    # 2>&1 folds python's stderr into the pipeline so crash traces reach the log
    & python @pyArgs 2>&1 | ForEach-Object { Write-Log ("    " + $_) }
    $code = $LASTEXITCODE
    $took = [int]((Get-Date) - $started).TotalMinutes
    Write-Log ("--- round {0}/{1} done  {2} min  exit={3} ---" -f $i, $Rounds, $took, $code)
}

Write-Log ("=" * 74)
Write-Log "OVERNIGHT DONE"
