<#
.SYNOPSIS
    Create a "Cognitive OS" shortcut on the current user's desktop.
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LauncherScript = $ProjectRoot + "\scripts\launch_web_ui.pyw"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = $DesktopPath + "\Cognitive OS.lnk"

Write-Host "=== Cognitive OS Desktop Shortcut ===" -ForegroundColor Cyan

# Prefer real Python over Windows Store stub
$pythonw = $null
$realPaths = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe")
)
foreach ($p in $realPaths) {
    if (Test-Path -LiteralPath $p -PathType Leaf) { $pythonw = $p; break }
}
if (-not $pythonw) {
    $pythonw = Get-Command pythonw.exe -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike "*\Microsoft\WindowsApps\*" } |
        Select-Object -First 1 -ExpandProperty Source
}
if (-not $pythonw) {
    Write-Host "ERROR: pythonw.exe not found. Install Python first." -ForegroundColor Red
    exit 1
}
Write-Host "Using: $pythonw" -ForegroundColor Gray

if (-not (Test-Path $LauncherScript -PathType Leaf)) {
    Write-Host "ERROR: $LauncherScript not found" -ForegroundColor Red
    exit 1
}

$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($ShortcutPath)
$lnk.TargetPath = $pythonw
$lnk.Arguments = [string]::Concat('"', $LauncherScript, '"')
$lnk.WorkingDirectory = $ProjectRoot
$lnk.Description = "Cognitive OS"
$lnk.Save()

Write-Host "Desktop shortcut created: Cognitive OS" -ForegroundColor Green
Write-Host "Path: $ShortcutPath" -ForegroundColor Gray
