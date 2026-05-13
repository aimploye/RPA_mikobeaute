$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$InnoScript = Join-Path $ProjectRoot "installer\POSReportBot.iss"
$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if (-not (Test-Path $InnoScript)) {
  throw "Inno Setup script not found: $InnoScript"
}

if (-not (Test-Path $InnoCompiler)) {
  throw "Inno Setup Compiler not found: $InnoCompiler. Install Inno Setup 6 before building installer."
}

& $InnoCompiler $InnoScript
