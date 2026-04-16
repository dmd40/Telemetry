$port = $env:CA_PORT
if ([string]::IsNullOrWhiteSpace($port)) { $port = 'COM3' }

$baud = $env:CA_BAUD
if ([string]::IsNullOrWhiteSpace($baud)) { $baud = 9600 }

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$logDir = Join-Path $projectRoot 'logs'
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logPath = Join-Path $logDir ("ca_reader_{0}.tsv" -f (Get-Date -Format 'yyyy-MM-dd'))
$newLog = -not (Test-Path $logPath)
$droppedCount = 0

$serial = New-Object -TypeName System.IO.Ports.SerialPort -ArgumentList $port, ([int]$baud), 'None', 8, 'One'
$serial.ReadTimeout = 1000
$serial.NewLine = "`n"
$serial.DtrEnable = $false
$serial.RtsEnable = $false

Write-Host "Reading Cycle Analyst from $port @ $baud"
Write-Host "Press Ctrl+C to stop."
Write-Host "Logging to $logPath"

if ($newLog) {
    "timestamp`traw`tAh`tV`tA`tS`tD`tDeg`tRPM`tHW`tNm`tThI`tThO`tAuxA`tAuxD`tFlgs" | Add-Content -Path $logPath -Encoding ascii
}

try {
    try {
        $serial.Open()
    }
    catch [System.UnauthorizedAccessException] {
        Write-Host "Could not open $port. Another app is using the port or it is still locked."
        Write-Host "Close Serial Monitor / Arduino IDE / any other reader and run this again."
        return
    }

    while ($true) {
        try {
            $line = $serial.ReadLine()
            if (-not $line) { continue }

            $trimmed = $line.Trim()
            if (-not $trimmed) { continue }

            $parts = $trimmed -split "`t"
            if ($parts.Count -ne 14 -or ($parts | Where-Object { $_ -notmatch '^-?\d+(\.\d+)?$' }).Count -gt 0) {
                $droppedCount++
                if (($droppedCount % 50) -eq 0) {
                    $t = Get-Date -Format 'HH:mm:ss'
                    Write-Host ("{0} dropped malformed rows: {1}" -f $t, $droppedCount)
                }
                continue
            }

            $ah  = [double]$parts[0]
            $v   = [double]$parts[1]
            $a   = [double]$parts[2]
            $rpm = [double]$parts[6]
            $nm  = [double]$parts[8]
            $thi = [double]$parts[9]
            $tho = [double]$parts[10]
            $t = Get-Date -Format 'HH:mm:ss'
            Write-Host ("{0} V={1:N2} A={2:N2} Ah={3:N4} RPM={4:N0} Nm={5:N1} ThI={6:N2} ThO={7:N2}" -f $t, $v, $a, $ah, $rpm, $nm, $thi, $tho)
            $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
            ("{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}`t{7}`t{8}`t{9}`t{10}`t{11}`t{12}`t{13}`t{14}`t{15}" -f `
                $stamp, $trimmed, $ah, $v, $a, $parts[3], $parts[4], $parts[5], $rpm, $parts[7], $nm, $thi, $tho, $parts[11], $parts[12], $parts[13]) | Add-Content -Path $logPath -Encoding ascii
        }
        catch [System.TimeoutException] {
        }
    }
}
finally {
    if ($serial.IsOpen) { $serial.Close() }
}
