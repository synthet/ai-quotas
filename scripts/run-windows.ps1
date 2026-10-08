$ErrorActionPreference = "Continue"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"

Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try { $Host.UI.RawUI.WindowTitle = "AI Quotas" } catch {}

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

function Stop-DockerDashboard {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        return
    }
    & docker info --format "{{.ServerVersion}}" 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $running = & docker ps -q --filter "name=^ai-quotas$"
    if (-not $running) {
        return
    }
    Write-Host "Stopping the Docker dashboard so this window can serve the page."
    & docker stop -t 10 ai-quotas 1>$null 2>$null
}

function Stop-PreviousServer {
    $listeners = netstat -ano | Select-String "127.0.0.1:8787\s+.*LISTENING"
    foreach ($listener in $listeners) {
        $procId = ($listener.ToString().Trim() -split "\s+")[-1]
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
        if ($owner -and $owner.CommandLine -like "*uvicorn app.main:app*") {
            Write-Host "Stopping the previous dashboard on port 8787."
            & taskkill /PID $procId /T /F 1>$null 2>$null
        }
    }
}

Write-Host "Starting AI Quotas at http://127.0.0.1:8787"
Write-Host "Leave this window open. Closing it stops the dashboard."

Stop-BridgePid
Stop-DockerDashboard
Stop-PreviousServer

$stillListening = netstat -ano | Select-String "127.0.0.1:8787\s+.*LISTENING"
if ($stillListening) {
    Write-Host "Port 8787 is already in use."
    exit 1
}

if (-not (Test-Path -LiteralPath ".env")) {
    if (-not (Test-Path -LiteralPath ".env.example")) {
        Write-Host ".env is missing."
        exit 1
    }
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example"
}

$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3 -m venv .venv
    } else {
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCmd) {
            Write-Host "Python is required on this PC."
            exit 1
        }
        & python -m venv .venv
    }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Host "Could not create .venv."
        exit 1
    }
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not install Python dependencies."
        exit 1
    }
}

& $python -c "import uvicorn, fastapi" 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not install Python dependencies."
        exit 1
    }
}

$readyFile = Join-Path $env:TEMP "ai-quotas-windows.ready"
Remove-Item -LiteralPath $readyFile -ErrorAction SilentlyContinue

$opener = Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\open-when-ready.ps1", "-ReadyFile", $readyFile `
    -WindowStyle Hidden `
    -PassThru

$serverCode = 0
try {
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
    $serverCode = $LASTEXITCODE
} finally {
    if ($opener -and -not $opener.HasExited) {
        & taskkill /PID $opener.Id /T /F 1>$null 2>$null
    }
}

if ((-not (Test-Path -LiteralPath $readyFile)) -and $serverCode -ne 0) {
    Write-Host "AI Quotas did not become ready (exit $serverCode)."
    exit 1
}

exit 0
