<#
.SYNOPSIS
    One-time setup: create .venv and install dependencies.

.DESCRIPTION
    Run from the project folder:
      .\setup_env.ps1

    Before lsl_bridge, replay, or run_demo.ps1, activate:
      .\.venv\Scripts\Activate.ps1
#>

$VenvFolder = ".venv"

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

Write-Host "Creating venv at $RepoRoot\$VenvFolder ..." -ForegroundColor Cyan
python -m venv $VenvFolder

$PythonExe = Join-Path $RepoRoot "$VenvFolder\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Error "venv python not found at $PythonExe"
    exit 1
}

Write-Host "Installing packages..." -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r "$RepoRoot\requirements.txt"
& $PythonExe -m pip install -r "$RepoRoot\emotibit-live\requirements.txt"
& $PythonExe -m pip install -r "$RepoRoot\eye-tracker-live\requirements.txt"

Write-Host ""
Write-Host "Done. Before each session run:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "Note: pylsl needs liblsl. If import fails, see https://github.com/sccn/liblsl"
Write-Host ""
