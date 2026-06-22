$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SecretDir = Join-Path $env:LOCALAPPDATA "CognitiveOS\cloud-sync"
$LogDir = Join-Path $env:LOCALAPPDATA "CognitiveOS\logs"
$LogPath = Join-Path $LogDir "local-cloud-sync.log"
$DatabaseSecret = Join-Path $SecretDir "neon-database.xml"
$TokenSecret = Join-Path $SecretDir "cloud-sync-token.xml"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$mutex = [Threading.Mutex]::new($false, "Local\CognitiveOSCloudSync")
if (-not $mutex.WaitOne(0)) {
    exit 3
}

try {
    if (-not (Test-Path -LiteralPath $DatabaseSecret) -or
        -not (Test-Path -LiteralPath $TokenSecret)) {
        throw "Local cloud sync secrets are not installed."
    }

    $databaseCredential = Import-Clixml -LiteralPath $DatabaseSecret
    $tokenCredential = Import-Clixml -LiteralPath $TokenSecret
    $env:NEON_DATABASE_URL = $databaseCredential.GetNetworkCredential().Password
    $env:CLOUD_SYNC_TOKEN = $tokenCredential.GetNetworkCredential().Password

    $pythonCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if (-not $pythonCandidates) {
        throw "Python executable was not found."
    }

    Push-Location $ProjectRoot
    try {
        $stdoutPath = Join-Path $LogDir "local-cloud-sync.stdout.tmp"
        $stderrPath = Join-Path $LogDir "local-cloud-sync.stderr.tmp"
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
        try {
            $process = Start-Process `
                -FilePath $pythonCandidates[0] `
                -ArgumentList "scripts\local_cloud_sync.py" `
                -WorkingDirectory $ProjectRoot `
                -WindowStyle Hidden `
                -Wait `
                -PassThru `
                -RedirectStandardOutput $stdoutPath `
                -RedirectStandardError $stderrPath
            $syncExitCode = $process.ExitCode
        }
        finally {
            foreach ($outputPath in @($stdoutPath, $stderrPath)) {
                if (Test-Path -LiteralPath $outputPath) {
                    Get-Content -LiteralPath $outputPath |
                        Out-File -LiteralPath $LogPath -Append -Encoding utf8
                    Remove-Item -LiteralPath $outputPath -Force
                }
            }
        }
        exit $syncExitCode
    }
    finally {
        Pop-Location
    }
}
catch {
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    "$timestamp local_cloud_sync_runner_failed error_type=$($_.Exception.GetType().Name)" |
        Out-File -LiteralPath $LogPath -Append -Encoding utf8
    exit 2
}
finally {
    Remove-Item Env:NEON_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:CLOUD_SYNC_TOKEN -ErrorAction SilentlyContinue
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
