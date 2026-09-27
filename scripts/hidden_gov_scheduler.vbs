' 政府源调度器，无可见窗口。
'
' schtasks 默认"仅当用户登录时运行"会开真实 CMD 窗口；关掉它会连带杀掉
' scheduler.py 及其采集子进程，所以改为 window style 0 启动。
'
' 这是另一 agent 创建的调度器，这里只包装启动方式，不改它的业务逻辑。
' 采集互斥由 collectors/lock.py（main.py 取锁）负责。
Option Explicit

Dim shell, fso, logFile, cmd

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If Not fso.FolderExists("d:\tvbox\BeaconOSINT\data") Then fso.CreateFolder "d:\tvbox\BeaconOSINT\data"
logFile = "d:\tvbox\BeaconOSINT\data\scheduler.log"

cmd = "cmd /c d:\tvbox\BeaconOSINT\scripts\run_scheduler.bat >> " & Chr(34) & logFile & Chr(34) & " 2>&1"

shell.Run cmd, 0, False
