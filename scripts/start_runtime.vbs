' Startup shim for Cognitive OS Runtime.
' Runs the PowerShell bootstrapper silently after Windows logon.

WScript.Sleep 30000

Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\admin\Documents\New project 8\scripts\start_runtime.ps1""", 0, False
Set shell = Nothing
