' 媒体源调度器，无可见窗口。
'
' 与政府源调度器同理：schtasks 直接调 .bat 会开真实 CMD 窗口，关掉窗口会
' 连带杀掉采集进程链。业务逻辑不动，只包装启动方式。
Option Explicit

Dim shell, fso, logFile, cmd

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If Not fso.FolderExists("d:\tvbox\BeaconOSINT\data") Then fso.CreateFolder "d:\tvbox\BeaconOSINT\data"
logFile = "d:\tvbox\BeaconOSINT\data\media_scheduler.log"

cmd = "cmd /c d:\tvbox\BeaconOSINT\scripts\run_media_scheduler.bat >> " & Chr(34) & logFile & Chr(34) & " 2>&1"

shell.Run cmd, 0, False
