' 政府工作报告 + 统计公报 的周期采集，无可见窗口。
'
' schtasks 直接调 .bat 会开真实 CMD 窗口（用户明确提过"怎么有两个 cmd 窗口"），
' 关掉窗口会连带杀掉整条采集进程链。window style 0 无窗口，且不阻塞本进程。
'
' 互斥由 collectors/lock.py 负责：报告与公报两个脚本依次取锁，锁释放后才跑下一个。
Option Explicit

Dim shell, fso, base, logDir, logFile, cmd

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = "d:\tvbox\BeaconOSINT"
logDir = base & "\data"
If Not fso.FolderExists(logDir) Then fso.CreateFolder logDir
logFile = logDir & "\work_reports.log"

cmd = "cmd /c " & base & "\scripts\run_work_reports.bat >> " & Chr(34) & logFile & Chr(34) & " 2>&1"

' 0 = 隐藏窗口，False = 不等待结束
shell.Run cmd, 0, False
