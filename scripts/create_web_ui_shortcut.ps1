<#
.SYNOPSIS
    Create a "Cognitive OS" shortcut on the current user's desktop.
.DESCRIPTION
    Points to pythonw.exe scripts\launch_web_ui.pyw.
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LauncherScript = $ProjectRoot + "\scripts\launch_web_ui.pyw"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = $DesktopPath + "\Cognitive OS.lnk"

Write-Host "=== Cognitive OS Desktop Shortcut ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot" -ForegroundColor Gray

$pythonw = $null
$paths = @(
    $env:USERPROFILE + "\AppData\Local\Programs\Python\Python313\pythonw.exe",
    $env:USERPROFILE + "\AppData\Local\Programs\Python\Python312\pythonw.exe",
    $env:LOCALAPPDATA + "\Programs\Python\Python313\pythonw.exe"
)
foreach ($p in $paths) {
    if (Test-Path $p) { $pythonw = $p; break }
}
if (-not $pythonw) {
    try { $pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source } catch {}
}
if (-not $pythonw) {
    Write-Host "pythonw.exe not found!" -ForegroundColor Red
    Write-Host "Please install Python and ensure it is on your PATH." -ForegroundColor Yellow
    exit 1
}
Write-Host "Using pythonw: $pythonw" -ForegroundColor Gray

if (-not (Test-Path $LauncherScript)) {
    Write-Host "ERROR: Launcher script not found: $LauncherScript" -ForegroundColor Red
    exit 1
}

$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($ShortcutPath)
$lnk.TargetPath = $pythonw
$lnk.Arguments = [string]::Concat('"', $LauncherScript, '"')
$lnk.WorkingDirectory = $ProjectRoot
$lnk.Description = "Cognitive OS - Personal event-sourced cognitive runtime"
$lnk.Save()

Write-Host ""
Write-Host "Desktop shortcut created: Cognitive OS" -ForegroundColor Green
Write-Host "Path: $ShortcutPath" -ForegroundColor Gray
Write-Host "Double-click the shortcut on your desktop to start." -ForegroundColor White