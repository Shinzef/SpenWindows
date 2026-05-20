
param(
    [string]$ServerIp = $env:SPEN_SERVER_IP,
    [int]$ServerPort = 5005,
    [string]$EventDevice = $env:SPEN_EVENT_DEVICE,
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'

$ndkBin = 'C:\Android\ndk\26.1.10909125\toolchains\llvm\prebuilt\windows-x86_64\bin'
$env:PATH += ';' + $ndkBin

Set-Location $PSScriptRoot

function Get-TabletPenEventDevice {
    $raw = adb shell getevent -i
    $current = $null
    $hasPressure = $false
    $hasPenBtn = $false
    $candidates = @()

    foreach ($line in $raw -split "`r?`n") {
        if ($line -match 'add device \d+: (/dev/input/event\d+)') {
            if ($current -and $hasPressure -and $hasPenBtn) {
                $candidates += $current
            }
            $current = $Matches[1]
            $hasPressure = $false
            $hasPenBtn = $false
            continue
        }

        if ($line -match '0018') { $hasPressure = $true }
        if ($line -match '0140' -or $line -match 'spen|stylus|pen|wacom|wcom') { $hasPenBtn = $true }
    }

    if ($current -and $hasPressure -and $hasPenBtn) {
        $candidates += $current
    }

    if ($candidates.Count -eq 0) {
        throw 'Could not auto-detect the pen event device. Set -EventDevice or SPEN_EVENT_DEVICE.'
    }

    return $candidates[0]
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    throw 'adb.exe was not found in PATH.'
}

if ([string]::IsNullOrWhiteSpace($ServerIp)) {
    $ServerIp = Read-Host 'Enter the PC IP address that the tablet should send to'
}

if ([string]::IsNullOrWhiteSpace($EventDevice)) {
    $EventDevice = Get-TabletPenEventDevice
}

$serial = adb devices | Select-String 'device$' | Select-Object -First 1
if (-not $serial) {
    throw 'No connected adb device was found.'
}

function Reset-RemoteDaemonState {
    adb shell "pkill -f spen_daemon >/dev/null 2>&1 || killall spen_daemon >/dev/null 2>&1 || true" | Out-Host
    adb shell "rm -f /data/local/tmp/spen_daemon.log" | Out-Host
}

Write-Host "Using event device: $EventDevice"
Write-Host "Using server: $ServerIp`:$ServerPort"

$localBinary = Join-Path $PSScriptRoot 'spen_daemon'
Remove-Item $localBinary -Force -ErrorAction SilentlyContinue
Reset-RemoteDaemonState

$build = 'aarch64-linux-android34-clang'
& $build spen_daemon.c -o spen_daemon
if ($LASTEXITCODE -ne 0 -or -not (Test-Path .\spen_daemon)) {
    throw 'Build failed.'
}

adb push .\spen_daemon /data/local/tmp/spen_daemon | Out-Host
adb shell chmod 755 /data/local/tmp/spen_daemon | Out-Host

$remoteCmd = "/data/local/tmp/spen_daemon $EventDevice $ServerIp $ServerPort"
if ($KeepRunning) {
    Write-Host "Starting daemon in foreground: $remoteCmd"
    adb shell $remoteCmd
} else {
    Write-Host "Starting daemon in background: $remoteCmd"
    adb shell "nohup $remoteCmd >/data/local/tmp/spen_daemon.log 2>&1 &"
    Write-Host 'Daemon started. Log: /data/local/tmp/spen_daemon.log'
}
