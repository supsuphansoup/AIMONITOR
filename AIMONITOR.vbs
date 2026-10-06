' AIMONITOR 100% Silent Launcher for Windows
' Double-clicking this script launches the native desktop application with ZERO console window.
Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = ScriptDir
WshShell.Run "pythonw """ & ScriptDir & "\run_desktop.pyw""", 0, False
