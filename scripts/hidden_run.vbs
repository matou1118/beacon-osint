' Run the overnight collection with no visible console window.
'
' schtasks defaults to "run only when user is logged on", which opens a real CMD
' window. Closing that window kills the whole process tree mid-round. Window
' style 0 (hidden) plus a non-blocking run detaches the job from any console.
Option Explicit
Dim shell, fso, logDir, logFile, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
logDir = "d:\tvbox\BeaconOSINT\data"
If Not fso.FolderExists(logDir) Then fso.CreateFolder logDir
logFile = logDir & "\overnight.log"
cmd = "cmd /c d:\tvbox\BeaconOSINT\scripts\run_overnight.bat >> """ & logFile & """ 2>&1"
' 0 = hidden window, False = do not wait for it to finish
shell.Run cmd, 0, False
