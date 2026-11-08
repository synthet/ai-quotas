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
    Write-Host "Stopping the Docker dashboard so the tray app can serve the page."
    & docker stop -t 10 ai-quotas 1>$null 2>$null
}

function Test-PortFree {
    # Bind test, like app.tray: netstat can show the listener as CLOSED on some Windows builds.
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8790)
    try {
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        $listener.Stop()
    }
}

function Stop-PreviousServer {
    # Find the dashboard by process, not by port: this venv's python running the tray or uvicorn.
    # /T also ends the Store Python child that the venv launcher starts.
    $venvScripts = Join-Path (Get-Location) ".venv\Scripts\"
    $owners = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venvScripts, [StringComparison]::OrdinalIgnoreCase) -and
            ($_.CommandLine -like "*-m app.tray*" -or $_.CommandLine -like "*uvicorn app.main:app*")
        }
    foreach ($owner in $owners) {
        Write-Host "Stopping the previous dashboard (PID $($owner.ProcessId))."
        & taskkill /PID $owner.ProcessId /T /F 1>$null 2>$null
    }
    if ($owners) {
        $deadline = (Get-Date).AddSeconds(5)
        while (-not (Test-PortFree) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 200
        }
    }
}

Write-Host "Starting AI Quotas at http://127.0.0.1:8790"

Stop-BridgePid
Stop-DockerDashboard
Stop-PreviousServer

if (-not (Test-PortFree)) {
    Write-Host "Port 8790 is already in use."
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

& $python -c "import uvicorn, fastapi, pystray, PIL" 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not install Python dependencies."
        exit 1
    }
}

Start-Process -FilePath (Join-Path (Get-Location) ".venv\Scripts\pythonw.exe") `
    -ArgumentList "-m", "app.tray" `
    -WorkingDirectory (Get-Location).Path

Write-Host "AI Quotas is running in the system tray."
exit 0
