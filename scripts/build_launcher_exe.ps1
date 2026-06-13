<#
.SYNOPSIS
    Optional: Package launch_web_ui.pyw into a standalone .exe using PyInstaller.
.DESCRIPTION
    This is optional. The default launcher (launch_web_ui.pyw) works without
    PyInstaller — just double-click or use the desktop shortcut.

    Requirements: pip install pyinstaller
    Output: dist/CognitiveOSLauncher.exe
#>

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LauncherScript = Join-Path $ProjectRoot "scripts" "launch_web_ui.pyw"
$DistDir = Join-Path $ProjectRoot "dist"

Write-Host "=== Cognitive OS Launcher EXE Builder ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "注意：这不是必需的。launch_web_ui.pyw 可以直接使用。" -ForegroundColor Yellow
Write-Host ""

# Check PyInstaller
$py = "python"
$check = & $py -c "import PyInstaller; print(PyInstaller.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller 未安装。" -ForegroundColor Red
    Write-Host ""
    Write-Host "请先安装：" -ForegroundColor White
    Write-Host "  pip install pyinstaller" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "或直接使用启动器脚本：" -ForegroundColor White
    Write-Host "  python scripts\launch_web_ui.pyw" -ForegroundColor Yellow
    exit 1
}

Write-Host "PyInstaller 版本: $check" -ForegroundColor Gray
Write-Host "正在打包..." -ForegroundColor Cyan

# Run PyInstaller
& $py -m PyInstaller `
    --name "CognitiveOSLauncher" `
    --onefile `
    --windowed `
    --distpath (Join-Path $ProjectRoot "dist") `
    --workpath (Join-Path $ProjectRoot "build") `
    --specpath $ProjectRoot `
    --add-data "$ProjectRoot/scripts;scripts" `
    --hidden-import PyInstaller `
    $LauncherScript 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "打包成功！" -ForegroundColor Green
    Write-Host "输出: $DistDir\CognitiveOSLauncher.exe" -ForegroundColor White
    Write-Host ""
    Write-Host "您可以直接运行该 exe，或创建指向它的桌面快捷方式。" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "打包失败，请检查上方错误信息。" -ForegroundColor Red
    exit 1
}
