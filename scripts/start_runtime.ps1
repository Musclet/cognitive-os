$ErrorActionPreference = "Stop"

$projectRoot = "C:\Users\admin\Documents\New project 8"
$pythonExe = "python"
$port = 8081
$dataDir = Join-Path $projectRoot "data"
$bootstrapLog = Join-Path $dataDir "runtime_bootstrap.log"
$stdoutLog = Join-Path $dataDir "runtime_stdout.log"
$stderrLog = Join-Path $dataDir "runtime_stderr.log"

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

function Write-BootstrapLog($message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $bootstrapLog -Value "[$timestamp] $message"
}

$listener = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($listener) {
    Write-BootstrapLog "runtime already listening on port $port, pid=$($listener.OwningProcess); skip start"
    exit 0
}

Write-BootstrapLog "starting Cognitive OS runtime"
Start-Process `
    -FilePath $pythonExe `
    -ArgumentList "scripts/run.py" `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog
