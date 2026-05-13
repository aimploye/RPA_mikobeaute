$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Spec = Join-Path $ProjectRoot "POSReportBot.spec"

if (-not (Test-Path $Python)) {
  throw "Python venv not found: $Python. Run py -3 -m venv .venv and install dependencies first."
}

& $Python -m PyInstaller --noconfirm --clean $Spec
