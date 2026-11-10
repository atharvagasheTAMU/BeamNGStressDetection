<#
.SYNOPSIS
    Launches the eye-tracker LSL bridge and middleware console in separate terminals.

.DESCRIPTION
    Starts two processes:
      1. eye-tracker-live/lsl_bridge.py  (camera + LSL publish)
      2. scripts/eye_tracker_console.py    (receive + print metrics/events)

    In the bridge window: SPACE = calibrate baseline, Q = quit.

.EXAMPLE
    .\run_eye_stream.ps1
    .\run_eye_stream.ps1 -ExperimentId "lab01" -Camera 0
    .\run_eye_stream.ps1 -BridgeWaitSeconds 5 -LslWait 10

.NOTES
    Uses .venv\Scripts\python.exe when present (run .\setup_env.ps1 first).
    Eye-tracker deps: pip install -r eye-tracker-live\requirements.txt
#>

param(
    [string]$ExperimentId = "",
    [int]$Camera = 0,
    [switch]$NoGui,
    [int]$BridgeWaitSeconds = 8,
    [double]$LslWait = 20.0
)

$RepoRoot = $PSScriptRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Warning ".venv not found — run .\setup_env.ps1. Falling back to 'python' on PATH."
    $PythonExe = "python"
}

$BridgeArgs = @("lsl_bridge.py", "--camera", $Camera)
if ($ExperimentId) {
    $BridgeArgs += @("--experiment-id", $ExperimentId)
}
if ($NoGui) {
    $BridgeArgs += "--no-gui"
}

$BridgeArgLine = ($BridgeArgs | ForEach-Object {
    if ($_ -match '\s') { "'$_'" } else { $_ }
}) -join " "

Write-Host "Starting eye-tracker LSL bridge..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$RepoRoot\eye-tracker-live'; & '$PythonExe' $BridgeArgLine"
)

Write-Host "Waiting $BridgeWaitSeconds s before console..." -ForegroundColor DarkGray
Start-Sleep -Seconds $BridgeWaitSeconds

Write-Host "Starting eye_tracker_console..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$RepoRoot\scripts'; & '$PythonExe' eye_tracker_console.py --lsl-wait $LslWait"
)

Write-Host "Launched." -ForegroundColor Green
Write-Host "  1) Bridge window: click camera preview, SPACE to calibrate, Q to quit." -ForegroundColor Yellow
Write-Host "  2) Console window: [event] always; [metrics] only after calibration." -ForegroundColor Yellow
