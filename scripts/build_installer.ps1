$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$InnoScript = Join-Path $ProjectRoot "installer\POSReportBot.iss"
$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$SignScript = Join-Path $ProjectRoot "scripts\sign_artifact.ps1"

if (-not (Test-Path $InnoScript)) {
  throw "Inno Setup script not found: $InnoScript"
}

if (-not (Test-Path $InnoCompiler)) {
  throw "Inno Setup Compiler not found: $InnoCompiler. Install Inno Setup 6 before building installer."
}

$InnoArgs = @()
if (-not [string]::IsNullOrWhiteSpace($env:POSREPORTBOT_INNO_SIGNTOOL)) {
  $InnoArgs += "/Sposreportbotsigntool=$env:POSREPORTBOT_INNO_SIGNTOOL"
}
$InnoArgs += $InnoScript

& $InnoCompiler @InnoArgs

$InstallerOutput = Join-Path $ProjectRoot "dist\installer\POSReportBotSetup-2.1.0.exe"
if (-not (Test-Path $InstallerOutput)) {
  throw "Expected installer output not found: $InstallerOutput"
}

& $SignScript -Path $InstallerOutput
