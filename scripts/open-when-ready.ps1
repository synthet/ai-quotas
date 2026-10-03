param(
    [Parameter(Mandatory = $true)]
    [string]$ReadyFile
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$deadline = (Get-Date).AddMinutes(5)

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/api/health" -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Set-Content -LiteralPath $ReadyFile -Value "ok" -Encoding ascii
            Start-Process "http://127.0.0.1:8787"
            exit 0
        }
    } catch {
    }
    Start-Sleep -Seconds 1
}

exit 1
