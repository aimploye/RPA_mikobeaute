$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Spec = Join-Path $ProjectRoot "POSReportBot.spec"
$SignScript = Join-Path $ProjectRoot "scripts\sign_artifact.ps1"

if (-not (Test-Path $Python)) {
  throw "Python venv not found: $Python. Run py -3 -m venv .venv and install dependencies first."
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
  throw "pip upgrade failed with exit code $LASTEXITCODE"
}
& $Python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
  throw "pip install failed with exit code $LASTEXITCODE"
}
& $Python -m PyInstaller --noconfirm --clean $Spec
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$ExeOutput = Join-Path $ProjectRoot "dist\POSReportBot"
if (-not (Test-Path $ExeOutput)) {
  throw "Expected PyInstaller output directory not found: $ExeOutput"
}

$BuiltExecutables = @(Get-ChildItem $ExeOutput -Filter "*.exe" -Recurse)
if ($BuiltExecutables.Count -eq 0) {
  throw "No executable files found under PyInstaller output directory: $ExeOutput"
}

$BuiltExecutables | ForEach-Object { & $SignScript -Path $_.FullName }
