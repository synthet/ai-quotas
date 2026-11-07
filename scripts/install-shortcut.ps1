# Points the AI Quotas desktop shortcut at the tray app, so starting it shows no console.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Host "Run run.cmd once first so .venv exists."
    exit 1
}

$path = Join-Path ([Environment]::GetFolderPath("Desktop")) "AI Quotas.lnk"
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m app.tray"
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = (Join-Path $root "assets\ai-quotas.ico") + ",0"
$shortcut.Save()
Write-Host "Updated $path"
