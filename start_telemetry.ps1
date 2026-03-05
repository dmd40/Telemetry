param(
  [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $root ".runtime"
$statePath = Join-Path $runtimeDir "telemetry_state.json"
$venvDir = Join-Path $root ".venv312"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

function Stop-TelemetryApiIfRunning {
  $listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
  foreach ($ln in $listeners) {
    try { Stop-Process -Id $ln.OwningProcess -Force -ErrorAction Stop } catch {}
  }

  $uvicornProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -like "*uvicorn app:app*" -and $_.CommandLine -like "*Telemetry-main*"
  }
  foreach ($p in $uvicornProcs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
  }
}

Stop-TelemetryApiIfRunning

if (-not (Test-Path $venvPython)) {
  Write-Host "Creating Python 3.12 virtual environment..."
  py -3.12 -m venv $venvDir
}

if ($InstallDeps) {
  Write-Host "Installing dependencies from requirements.txt..."
  & $venvPython -m pip install -r (Join-Path $root "requirements.txt")
} else {
  try {
    & $venvPython -c "import fastapi, uvicorn, websockets, serial" | Out-Null
  } catch {
    Write-Host "Dependencies missing. Installing requirements.txt..."
    & $venvPython -m pip install -r (Join-Path $root "requirements.txt")
  }
}

$apiLog = Join-Path $runtimeDir "uvicorn.out.log"
$apiErr = Join-Path $runtimeDir "uvicorn.err.log"
Remove-Item $apiLog, $apiErr -ErrorAction SilentlyContinue

$apiProc = Start-Process `
  -FilePath $venvPython `
  -ArgumentList @("-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000") `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $apiLog `
  -RedirectStandardError $apiErr `
  -PassThru

Start-Sleep -Seconds 2

$healthOk = $false
try {
  $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/api/health" -TimeoutSec 5
  if ($health.StatusCode -eq 200) { $healthOk = $true }
} catch {}

if (-not $healthOk) {
  throw "Telemetry API did not start cleanly. Check $apiErr"
}

# Start or refresh Tailscale Funnel in background.
try {
  $null = & tailscale funnel --bg --yes 8000 2>$null
} catch {
  Write-Warning "Could not start Funnel automatically. Make sure Tailscale is connected and Funnel is enabled."
}

$publicDash = ""
$publicHealth = ""
for ($i = 0; $i -lt 8; $i++) {
  try {
    $funnelJson = tailscale funnel status --json | ConvertFrom-Json
    if ($funnelJson.Web) {
      $webKey = ($funnelJson.Web.PSObject.Properties | Select-Object -First 1).Name
      if ($webKey) {
        $publicHost = $webKey.Split(":")[0]
        $publicDash = "https://$publicHost/static/index.html"
        $publicHealth = "https://$publicHost/api/health"
        break
      }
    }
  } catch {}
  Start-Sleep -Milliseconds 500
}

if (-not $publicDash) {
  try {
    $statusJson = tailscale status --json | ConvertFrom-Json
    $dnsName = $statusJson.Self.DNSName
    if ($dnsName) {
      $publicHost = $dnsName.TrimEnd(".")
      $publicDash = "https://$publicHost/static/index.html"
      $publicHealth = "https://$publicHost/api/health"
    }
  } catch {}
}

$state = [ordered]@{
  started_at      = (Get-Date).ToString("o")
  api_pid         = $apiProc.Id
  local_dashboard = "http://127.0.0.1:8000/static/index.html"
  local_health    = "http://127.0.0.1:8000/api/health"
  public_dashboard = $publicDash
  public_health    = $publicHealth
}

$state | ConvertTo-Json | Set-Content -Encoding UTF8 $statePath

Write-Host ""
Write-Host "Telemetry started."
Write-Host "Local dashboard : http://127.0.0.1:8000/static/index.html"
Write-Host "Local health    : http://127.0.0.1:8000/api/health"
if ($publicDash) {
  Write-Host "Public dashboard: $publicDash"
  Write-Host "Public health   : $publicHealth"
} else {
  Write-Host "Public dashboard: (not available)"
  Write-Host "Tip: run 'tailscale status' and then 'tailscale funnel --bg --yes 8000'"
}
