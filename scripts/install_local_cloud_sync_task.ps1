param(
    [string]$TaskName = "Cognitive OS Local Cloud Sync"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_local_cloud_sync.ps1"
$SecretDir = Join-Path $env:LOCALAPPDATA "CognitiveOS\cloud-sync"

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Local cloud sync runner is missing."
}

New-Item -ItemType Directory -Force -Path $SecretDir | Out-Null

$databaseSecret = Read-Host "Neon PostgreSQL URL" -AsSecureString
$tokenSecret = Read-Host "CLOUD_SYNC_TOKEN" -AsSecureString

[PSCredential]::new("NEON_DATABASE_URL", $databaseSecret) |
    Export-Clixml -LiteralPath (Join-Path $SecretDir "neon-database.xml")
[PSCredential]::new("CLOUD_SYNC_TOKEN", $tokenSecret) |
    Export-Clixml -LiteralPath (Join-Path $SecretDir "cloud-sync-token.xml")

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`"" `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "07:00"
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Sync JWXT and Chaoxing locally to Neon at 07:00." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Run now: Start-ScheduledTask -TaskName `"$TaskName`""
