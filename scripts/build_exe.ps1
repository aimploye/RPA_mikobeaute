param(
  [switch]$RequireSigning,
  [switch]$AllowUnsignedDevBuild
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Spec = Join-Path $ProjectRoot "POSReportBot.spec"
$SignScript = Join-Path $ProjectRoot "scripts\sign_artifact.ps1"
$RuntimeVerificationScript = Join-Path $ProjectRoot "scripts\assert_clean_frozen_runtime.ps1"

if ($RequireSigning -and $AllowUnsignedDevBuild) {
  throw "RequireSigning and AllowUnsignedDevBuild cannot be used together."
}

if ($AllowUnsignedDevBuild) {
  $env:POSREPORTBOT_REQUIRE_SIGNING = "0"
  $env:POSREPORTBOT_SIGN_CERT_SHA1 = ""
  $env:POSREPORTBOT_SIGN_CERT_PFX = ""
  Write-Warning "Unsigned build requested: signing inputs were disabled for this build."
} elseif ($RequireSigning) {
  $env:POSREPORTBOT_REQUIRE_SIGNING = "1"
} else {
  $env:POSREPORTBOT_REQUIRE_SIGNING = "0"
  if ([string]::IsNullOrWhiteSpace($env:POSREPORTBOT_SIGN_CERT_SHA1) -and [string]::IsNullOrWhiteSpace($env:POSREPORTBOT_SIGN_CERT_PFX)) {
    Write-Warning "Unsigned build requested: no POSReportBot signing certificate is configured. The artifact can still be built and run."
  }
}

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

$OriginalPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
$BasePythonDir = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
if ([string]::IsNullOrWhiteSpace($BasePythonDir)) {
  throw "Unable to resolve the base Python directory for the clean PyInstaller environment."
}
$VenvScriptsDir = Split-Path -Parent $Python
$SafeBuildPathEntries = @(
  $VenvScriptsDir,
  $BasePythonDir,
  (Join-Path $env:SystemRoot "System32"),
  $env:SystemRoot,
  (Join-Path $env:SystemRoot "System32\Wbem"),
  (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0")
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
$SafeBuildPath = $SafeBuildPathEntries -join [IO.Path]::PathSeparator

try {
  # The Codex desktop process adds document-tool runtimes (Poppler/libheif) to
  # PATH. PyInstaller follows native DLL dependencies and would otherwise copy
  # their Windows 11 UCRT/API-set/OpenSSL/ICU files into this unrelated product.
  $env:PATH = $SafeBuildPath
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

  & $RuntimeVerificationScript `
    -DistributionDir $ExeOutput `
    -BuildDir (Join-Path $ProjectRoot "build\POSReportBot")
  if ($LASTEXITCODE -ne 0) {
    throw "Frozen runtime verification failed with exit code $LASTEXITCODE"
  }

  # Keep the clean build PATH active while importing Qt. Otherwise a missing
  # packaged dependency could be accidentally satisfied by a build-host tool.
  $GuiRuntimeSelfTest = Start-Process `
    -FilePath (Join-Path $ExeOutput "POSReportBot.exe") `
    -ArgumentList "--self-test-gui-runtime" `
    -Wait `
    -PassThru
  if ($GuiRuntimeSelfTest.ExitCode -ne 0) {
    throw "Frozen GUI runtime self-test failed with exit code $($GuiRuntimeSelfTest.ExitCode)"
  }
  Write-Output "Frozen GUI runtime self-test passed."
} finally {
  $env:PATH = $OriginalPath
}

$BuiltExecutables | ForEach-Object { & $SignScript -Path $_.FullName }
