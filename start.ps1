# 启动(或重启)控制台。端口上已有本项目的控制台就先停掉再起;端口被别的程序占着则报错退出,不乱杀。
# 用法:.\start.ps1  [-Db <库>] [-DataRoot <素材根>] [-Port 8700]
param(
    [string]$Db = "outputs/drama/tijia/drama.db",
    [string]$DataRoot = "outputs/drama/tijia",
    [int]$Port = 8700
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listen) {
    $procId = $listen.OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId"
    if ($proc.CommandLine -notmatch "tools\.serve\.console") {
        Write-Host "端口 $Port 被别的程序占着(PID $procId):$($proc.CommandLine)" -ForegroundColor Red
        Write-Host "不是控制台,不替你停。换端口:.\start.ps1 -Port 8701" -ForegroundColor Red
        exit 1
    }
    Write-Host "停掉正在跑的控制台(PID $procId)…"
    Stop-Process -Id $procId -Force
    for ($i = 0; $i -lt 20 -and (Get-Process -Id $procId -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
        Write-Host "PID $procId 10 秒内没退出,放弃启动" -ForegroundColor Red
        exit 1
    }
}

Write-Host "启动控制台:http://127.0.0.1:$Port/  (库 $Db,素材根 $DataRoot)"
python -X utf8 -m tools.serve.console --db $Db --data-root $DataRoot --port $Port
exit $LASTEXITCODE
