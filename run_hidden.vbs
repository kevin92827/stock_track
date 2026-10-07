' Launches run_scheduled.bat without showing a console window.
' Used by the Windows scheduled task so nobody closes the window by accident mid-run.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd.exe /c """ & dir & "\run_scheduled.bat""", 0, True
