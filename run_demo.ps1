<#
.SYNOPSIS
    Launches the EmotiBit LSL bridge, replay, and stress detector in separate terminals.

.DESCRIPTION
    Starts three processes, each in its own PowerShell window:
      1. lsl_bridge.py        (emotibit-live/)
      2. replay.py <csv>      (emotibit-live/)  -- after $BridgeWaitSeconds
      3. stress console/demo  (scripts/)         -- after $ReplayWaitSeconds

.EXAMPLE
    .\run_demo.ps1
    .\run_demo.ps1 -CsvFile "data\my_recording.csv" -BaselineMode slope
    .\run_demo.ps1 -RunMode demo

.NOTES
    Uses .venv\Scripts\python.exe when present (run .\setup_env.ps1 first).
#>

param(
    [string]$CsvFile = "data\2026-06-23_17-08-36-829312.csv",
    [int]$ReplaySpeed = 5,

    # Options: rolling | calibrated | slope | eda | combined
    #   rolling    - HR vs rolling mean (+/- BPM threshold)
    #   calibrated - HR vs fixed baseline recorded at session start
    #   slope      - HR rate-of-change (BPM/s) -> defensive on fast rise
    #   eda        - SCR peak count per window  -> defensive on enough peaks
    #   combined   - HR slope fused with SCR peak count (see -CombinePolicy)
    [string]$BaselineMode = "combined",

    # Only used when -BaselineMode combined. Options: or | and
    #   or  - stressed if HR slope OR SCR peak count crosses its threshold
    #   and - stressed only if both HR slope AND SCR peak count cross threshold
    [string]$CombinePolicy = "and",

    # Options: console | demo
    #   console - stress_detect_console.py (no BeamNG, just prints stress state)
    #   demo    - mode_switch_stress_demo.py (full BeamNG driving demo)
    [string]$RunMode = "console",

    [int]$BridgeWaitSeconds = 10,
    [int]$ReplayWaitSeconds = 2
)

$RepoRoot = $PSScriptRoot
$CsvPath = Join-Path $RepoRoot $CsvFile

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Warning ".venv not found — run .\setup_env.ps1. Falling back to 'python' on PATH."
    $PythonExe = "python"
}

if ($RunMode -eq "demo") {
    $DetectorScript = "mode_switch_stress_demo.py"
} else {
    $DetectorScript = "stress_detect_console.py"
}

Write-Host "Starting LSL bridge..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$RepoRoot\emotibit-live'; & '$PythonExe' lsl_bridge.py"
)

Write-Host "Waiting $BridgeWaitSeconds s before replay..." -ForegroundColor DarkGray
Start-Sleep -Seconds $BridgeWaitSeconds

Write-Host "Starting replay..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$RepoRoot\emotibit-live'; & '$PythonExe' replay.py '$CsvPath' --speed $ReplaySpeed"
)

Write-Host "Waiting $ReplayWaitSeconds s before stress detector..." -ForegroundColor DarkGray
Start-Sleep -Seconds $ReplayWaitSeconds

Write-Host "Starting $DetectorScript..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$RepoRoot\scripts'; & '$PythonExe' $DetectorScript --baseline-mode $BaselineMode --combine-policy $CombinePolicy"
)

Write-Host "All processes launched." -ForegroundColor Green
