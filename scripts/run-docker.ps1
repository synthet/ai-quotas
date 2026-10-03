$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"

Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try { $Host.UI.RawUI.WindowTitle = "AI Quotas" } catch {}

function Test-DockerReady {
    & docker info --format "{{.ServerVersion}}" 1>$null 2>$null
    return $LASTEXITCODE -eq 0
}

function Stop-BridgePid {
    $pidFile = Join-Path (Get-Location) ".docker\bridge.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) {
        return
    }
    $oldPid = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldPid) {
        & taskkill /PID $oldPid /T /F 1>$null 2>$null
    }
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
}

Write-Host "Starting AI Quotas in Docker at http://127.0.0.1:8787"
Write-Host "Leave this window open. Closing it stops the container."

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker is not installed. Install Docker Desktop and try again."
    exit 1
}

if (-not (Test-DockerReady)) {
    $dockerDesktop = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $dockerDesktop) {
        Write-Host "Docker Desktop is not running, and its program file was not found."
        exit 1
    }
    Write-Host "Starting Docker Desktop..."
    Start-Process -FilePath $dockerDesktop | Out-Null
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerReady) { break }
        Start-Sleep -Seconds 3
    }
    if (-not (Test-DockerReady)) {
        Write-Host "Docker Desktop did not become ready."
        exit 1
    }
}

$listeners = netstat -ano | Select-String "127.0.0.1:8787\s+.*LISTENING"
foreach ($listener in $listeners) {
    $procId = ($listener.ToString().Trim() -split "\s+")[-1]
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
    if ($owner -and $owner.CommandLine -like "*uvicorn app.main:app*") {
        Write-Host "Stopping the previous local dashboard on port 8787."
        & taskkill /PID $procId /T /F 1>$null 2>$null
    }
}

if (-not (Test-Path -LiteralPath ".env")) {
    if (-not (Test-Path -LiteralPath ".env.example")) {
        Write-Host ".env is missing."
        exit 1
    }
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example"
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "Python is required on this PC so the container can reach the local CLIs."
    exit 1
}

New-Item -ItemType Directory -Force -Path ".docker" | Out-Null
Stop-BridgePid
Remove-Item -LiteralPath ".docker\bridge.out.log", ".docker\bridge.err.log" -ErrorAction SilentlyContinue

$readyFile = Join-Path $env:TEMP "ai-quotas-docker.ready"
Remove-Item -LiteralPath $readyFile -ErrorAction SilentlyContinue

$bridge = Start-Process -FilePath $pythonCmd.Source `
    -ArgumentList "scripts\host_cli_bridge.py" `
    -WorkingDirectory (Get-Location) `
    -WindowStyle Hidden `
    -RedirectStandardOutput ".docker\bridge.out.log" `
    -RedirectStandardError ".docker\bridge.err.log" `
    -PassThru
Set-Content -LiteralPath ".docker\bridge.pid" -Value $bridge.Id -Encoding ascii

$bridgeReady = $false
for ($i = 0; $i -lt 90; $i++) {
    if ($bridge.HasExited) { break }
    try {
        $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:18788/health" -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            $bridgeReady = $true
            break
        }
    } catch {
    }
    Start-Sleep -Seconds 1
}

if (-not $bridgeReady) {
    Write-Host "The local CLI bridge did not start. See .docker\bridge.err.log"
    Stop-BridgePid
    exit 1
}

$opener = Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\open-when-ready.ps1", "-ReadyFile", $readyFile `
    -WindowStyle Hidden `
    -PassThru

$composeCode = 0
try {
    & docker compose up --build
    $composeCode = $LASTEXITCODE
} finally {
    if ($opener -and -not $opener.HasExited) {
        & taskkill /PID $opener.Id /T /F 1>$null 2>$null
    }
    & docker compose down
    Stop-BridgePid
}

if ((-not (Test-Path -LiteralPath $readyFile)) -and $composeCode -ne 0) {
    Write-Host "AI Quotas did not become ready (Docker Compose exit $composeCode)."
    exit 1
}

exit 0
