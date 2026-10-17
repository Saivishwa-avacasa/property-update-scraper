' Launches run_daily.cmd without a console window, so nothing pops up that
' could be closed by accident (closing the window killed earlier runs).
' Usage (what Task Scheduler calls):
'   wscript.exe run_hidden.vbs TN
'   wscript.exe run_hidden.vbs all full
Dim shell, here, args, i
Set shell = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
args = ""
For i = 0 To WScript.Arguments.Count - 1
  args = args & " " & WScript.Arguments(i)
Next
If args = "" Then args = " all"
shell.CurrentDirectory = here
WScript.Quit shell.Run("cmd.exe /c """"" & here & "run_daily.cmd""" & args & """", 0, True)
