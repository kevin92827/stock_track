' Launches run_lev_live.bat without showing a console window (used by the 13:31 scheduled task).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd.exe /c """ & dir & "\run_lev_live.bat""", 0, True
