# Install the overnight self-iteration scheduled task. Idempotent.
#
# Why raw XML instead of New-ScheduledTaskTrigger: combining -Daily with
# -RepetitionInterval hits an ambiguous parameter set, and StopAtDurationEnd
# (which must be false, otherwise the daily trigger itself dies when the
# repetition window closes) is not reachable through the cmdlet at all. The
# XML is longer but it is the only way to say exactly what is meant.
#
# Design notes worth keeping:
#  - 23:30 daily, repeating every 2h for 12h -> 23:30,01:30,03:30,05:30,07:30,09:30.
#    Slices rather than one long run: one hanging source then costs a single
#    slice instead of the whole night, and progress is visible all night.
#  - MultipleInstances = IgnoreNew. collectors/lock.py would stop a second
#    writer anyway, but this makes the scheduler itself not spawn it, so task
#    state stays truthful instead of showing "running" for two overlapping jobs.
#  - ExecutionTimeLimit PT3H. A single slice should never take that long; if it
#    does, something is wedged and the next slice deserves a turn.
#  - StartWhenAvailable, so a machine asleep at 23:30 still runs after wake-up
#    instead of skipping the whole night.
#  - A separate ONLOGON catch-up task: repetition only fires while the machine
#    is on, so a laptop that slept through the night would otherwise collect
#    nothing until the next 23:30.
#  - SYSTEM principal, so collection continues with nobody logged in — the whole
#    point of running overnight.
param(
    [int]$AtHour = 23,
    [int]$AtMinute = 30,
    [int]$EveryHours = 2,
    [int]$WindowHours = 12
)

$ErrorActionPreference = 'Stop'
$vbs = 'd:\tvbox\BeaconOSINT\scripts\hidden_overnight.vbs'
if (-not (Test-Path $vbs)) { throw "launcher missing: $vbs" }

$start = (Get-Date).Date.AddHours($AtHour).AddMinutes($AtMinute)
$iso = $start.ToString('yyyy-MM-ddTHH:mm:ss')

function New-LogonCatchupXml {
    # A LogonTrigger cannot use LogonType=ServiceAccount (the XML validator
    # rejects it outright: "value out of range"). A logon trigger by definition
    # fires when someone logs on, so InteractiveToken + the current user is the
    # only coherent combination. This task is a catch-up, not the main job, so
    # "user is logged on" is exactly right for it.
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
  </Settings>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>$me</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>"$vbs"</Arguments>
    </Exec>
  </Actions>
</Task>
"@
}

# Main task. Principal is the current user with InteractiveToken, matching the
# three existing BeaconOSINT tasks that demonstrably run on this machine —
# <LogonType>ServiceAccount</LogonType> is rejected by the XML validator here
# ("value out of range") even with UserId S-1-5-18. Unattended-overnight is
# still covered: the session stays logged on, and if the machine reboots and
# nobody logs in, the ONLOGON catch-up task fires as soon as they do.
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$main = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$iso</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Repetition><Interval>PT${EveryHours}H</Interval><Duration>PT${WindowHours}H</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>$me</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>"$vbs"</Arguments>
    </Exec>
  </Actions>
</Task>
"@

$catchup = New-LogonCatchupXml

foreach ($pair in @(@('BeaconOSINT-Overnight', $main),
                    @('BeaconOSINT-Overnight-Startup', $catchup))) {
    $name = $pair[0]
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Write-Output "removing existing $name"
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $name -Xml $pair[1] | Out-Null
}

Write-Output '=== installed ==='
foreach ($name in @('BeaconOSINT-Overnight', 'BeaconOSINT-Overnight-Startup')) {
    $t = Get-ScheduledTask -TaskName $name
    $i = Get-ScheduledTaskInfo -TaskName $name
    Write-Output ("  {0,-34} {1,-9} next={2}" -f $name, $t.State, $i.NextRunTime)
}
Write-Output ''
Write-Output 'log:    data\overnight_YYYYMMDD.log'
Write-Output 'health: python scripts\overnight_health.py'
Write-Output "run now: schtasks /Run /TN BeaconOSINT-Overnight"
