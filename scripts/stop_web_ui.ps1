<#


.SYNOPSIS


    Stop Cognitive OS services launched by launcher.


.DESCRIPTION


    Reads .runtime\launcher-pids.json and kills only the processes


    that were started by the launcher. Does NOT kill all python/node


    processes on the system.


#>





$ProjectRoot = Split-Path -Parent $PSScriptRoot


$PidsFile = ($ProjectRoot + "\.runtime\launcher-pids.json")


$LogsDir = ($ProjectRoot + "\logs\launcher")





Write-Host "=== Cognitive OS 停止脚本 ===" -ForegroundColor Cyan





if (-not (Test-Path $PidsFile)) {


    Write-Host "没有发现由启动器启动的进程（.runtime\launcher-pids.json 不存在）。" -ForegroundColor Yellow


    Write-Host "如果仍有服务在运行，请手动关闭终端窗口。" -ForegroundColor Yellow


    exit 0


}





try {


    $pids = Get-Content $PidsFile -Raw -Encoding UTF8 | ConvertFrom-Json


} catch {


    Write-Host "无法读取 PID 文件：$_" -ForegroundColor Red


    exit 1


}





$stopped = @()


$notFound = @()





if ($pids.backend_pid) {


    $pid = [int]$pids.backend_pid


    try {


        $proc = Get-Process -Id $pid -ErrorAction Stop


        Stop-Process -Id $pid -Force -ErrorAction Stop


        Write-Host "已停止后端 (PID: $pid)" -ForegroundColor Green


        $stopped += "backend"


    } catch {


        Write-Host "后端进程 (PID: $pid) 未在运行，可能已关闭。" -ForegroundColor Yellow


        $notFound += "backend"


    }


}





if ($pids.frontend_pid) {


    $pid = [int]$pids.frontend_pid


    try {


        $proc = Get-Process -Id $pid -ErrorAction Stop


        Stop-Process -Id $pid -Force -ErrorAction Stop


        Write-Host "已停止前端 (PID: $pid)" -ForegroundColor Green


        $stopped += "frontend"


    } catch {


        Write-Host "前端进程 (PID: $pid) 未在运行，可能已关闭。" -ForegroundColor Yellow


        $notFound += "frontend"


    }


}





# Clean up PID file


Remove-Item $PidsFile -Force -ErrorAction SilentlyContinue


Write-Host ""


if ($stopped.Count -gt 0) {


    Write-Host "已停止：$($stopped -join ', ')" -ForegroundColor Green


}


if ($notFound.Count -gt 0) {


    Write-Host "未运行：$($notFound -join ', ')" -ForegroundColor Yellow


}


Write-Host "PID 文件已清理。" -ForegroundColor Gray


Write-Host "Cognitive OS 服务已停止。" -ForegroundColor Cyan


