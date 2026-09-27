' Hidden launcher for the overnight self-iteration run.
'
' Task Scheduler's "run only when user is logged on" default still flashes a
' console window. These launchers exist so a 23:30 run does not pop a black
' box on someone's screen at midnight — the existing gov/media/work-report
' launchers do the same thing.
'
' Log is appended, never truncated: several slices per night all land in the
' same file so the morning review reads one continuous story.
Option Explicit

Dim shell, fso, logFile, stamp, cmd, d, m, y

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If Not fso.FolderExists("d:\tvbox\BeaconOSINT\data") Then
  fso.CreateFolder "d:\tvbox\BeaconOSINT\data"
End If

' Build YYYYMMDD by hand. Now is locale-dependent ("9/28/2026 12:05:00 AM"), so
' the obvious Replace(Now, "/", "") then Left(...,8) produced "overnight_2026927"
' — no leading zero and the digits transposed. Two runs a day apart could also
' collide onto one name. DatePart is locale-independent.
d = Right("0" & DatePart("d", Now), 2)
m = Right("0" & DatePart("m", Now), 2)
y = DatePart("yyyy", Now)
logFile = "d:\tvbox\BeaconOSINT\data\overnight_" & y & m & d & ".log"

cmd = "cmd /c d:\tvbox\BeaconOSINT\scripts\run_overnight.bat >> " _
    & Chr(34) & logFile & Chr(34) & " 2>&1"

' shell.Run's last argument is True, i.e. WAIT. It was False ("don't wait")
' with a comment claiming that was better because the scheduler would otherwise
' hold the task as running all night. That reasoning was backwards and it cost a
' whole run: with bWait=False the VBS exits immediately, the scheduler marks the
' task finished within a second, so MultipleInstances=IgnoreNew has nothing to
' ignore and the next trigger starts a SECOND pipeline. Observed live: two
' pipeline.py processes, one in collect while the other was already in dedup.
' Waiting keeps the task "Running" for the slice, which is exactly what makes
' IgnoreNew work and keeps the reported task state honest. The 2-hourly
' repetition is unaffected: an overlapping trigger is skipped, not queued.
shell.Run cmd, 0, True
