$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
$runtimeDir = Join-Path $root ".runtime"
$statePath = Join-Path $runtimeDir "telemetry_state.json"

function Stop-ApiProcessByPid([int]$pid) {
  if ($pid -le 0) { return }
  try {
    $p = Get-Process -Id $pid -ErrorAction Stop
    Stop-Process -Id $p.Id -Force -ErrorAction Stop
    Write-Host "Stopped API process PID $pid"
  } catch {}
}

try {
  $listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
  foreach ($ln in $listeners) {
    try {
      Stop-Process -Id $ln.OwningProcess -Force -ErrorAction Stop
      Write-Host "Stopped listener PID $($ln.OwningProcess) on port 8000"
    } catch {}
  }
} catch {}

if (Test-Path $statePath) {
  try {
    $state = Get-Content $statePath | ConvertFrom-Json
    if ($state.api_pid) {
      Stop-ApiProcessByPid -pid ([int]$state.api_pid)
    }
  } catch {}
}

# Fallback: stop any uvicorn telemetry process from this project.
$uvicornProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
  (($_.CommandLine -like "*uvicorn backend.app:app*") -or ($_.CommandLine -like "*uvicorn app:app*")) -and $_.CommandLine -like "*Telemetry-main*"
}
foreach ($p in $uvicornProcs) {
  try {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    Write-Host "Stopped API process PID $($p.ProcessId)"
  } catch {}
}

try {
  $null = & tailscale funnel --https=443 off 2>$null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Funnel disabled."
  } else {
    Write-Host "Funnel already off."
  }
} catch {
  Write-Warning "Funnel disable command failed (Tailscale may be offline)."
}

Remove-Item $statePath -ErrorAction SilentlyContinue

Write-Host "Telemetry stopped."
