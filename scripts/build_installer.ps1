param(
  [switch]$RequireSigning,
  [switch]$AllowUnsignedDevBuild
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$InnoScript = Join-Path $ProjectRoot "installer\POSReportBot.iss"
$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$SignScript = Join-Path $ProjectRoot "scripts\sign_artifact.ps1"

if ($RequireSigning -and $AllowUnsignedDevBuild) {
  throw "RequireSigning and AllowUnsignedDevBuild cannot be used together."
}

if ($AllowUnsignedDevBuild) {
  $env:POSREPORTBOT_REQUIRE_SIGNING = "0"
  $env:POSREPORTBOT_SIGN_CERT_SHA1 = ""
  $env:POSREPORTBOT_SIGN_CERT_PFX = ""
  $env:POSREPORTBOT_INNO_SIGNTOOL = ""
  Write-Warning "Unsigned build requested: signing inputs were disabled for this build."
} elseif ($RequireSigning) {
  $env:POSREPORTBOT_REQUIRE_SIGNING = "1"
  if ([string]::IsNullOrWhiteSpace($env:POSREPORTBOT_INNO_SIGNTOOL)) {
    throw "Release installer signing is required. Set POSREPORTBOT_INNO_SIGNTOOL so Inno Setup can sign the uninstaller."
  }
} else {
  $env:POSREPORTBOT_REQUIRE_SIGNING = "0"
  if ([string]::IsNullOrWhiteSpace($env:POSREPORTBOT_SIGN_CERT_SHA1) -and [string]::IsNullOrWhiteSpace($env:POSREPORTBOT_SIGN_CERT_PFX)) {
    Write-Warning "Unsigned build requested: no POSReportBot signing certificate is configured. The installer can still be built and run."
  }
}

$AppExecutable = Join-Path $ProjectRoot "dist\POSReportBot\POSReportBot.exe"
if (-not (Test-Path $AppExecutable)) {
  throw "Built application executable not found: $AppExecutable. Run scripts\build_exe.ps1 first."
}
if ($RequireSigning) {
  $AppSignature = Get-AuthenticodeSignature -LiteralPath $AppExecutable
  if ($AppSignature.Status -ne "Valid") {
    throw "Refusing to package an unsigned or invalid POSReportBot.exe: $($AppSignature.Status)"
  }
}

if (-not (Test-Path $InnoScript)) {
  throw "Inno Setup script not found: $InnoScript"
}

$VersionMatch = Select-String -LiteralPath $InnoScript -Pattern '^\s*#define\s+MyAppVersion\s+"(?<version>\d+\.\d+\.\d+)"\s*$' | Select-Object -First 1
if ($null -eq $VersionMatch) {
  throw "Unable to read MyAppVersion from Inno Setup script: $InnoScript"
}
$AppVersion = $VersionMatch.Matches[0].Groups["version"].Value
$ExecutableProductVersion = (Get-Item -LiteralPath $AppExecutable).VersionInfo.ProductVersion
try {
  $ParsedExecutableVersion = [version]$ExecutableProductVersion
  $ParsedInstallerVersion = [version]$AppVersion
} catch {
  throw "Unable to compare executable and installer versions: executable='$ExecutableProductVersion', installer='$AppVersion'."
}
if (
  $ParsedExecutableVersion.Major -ne $ParsedInstallerVersion.Major -or
  $ParsedExecutableVersion.Minor -ne $ParsedInstallerVersion.Minor -or
  $ParsedExecutableVersion.Build -ne $ParsedInstallerVersion.Build
) {
  throw "Executable ProductVersion '$ExecutableProductVersion' does not match installer version '$AppVersion'. Rebuild the executable first."
}

if (-not (Test-Path $InnoCompiler)) {
  throw "Inno Setup Compiler not found: $InnoCompiler. Install Inno Setup 6 before building installer."
}

$InnoArgs = @()
if (-not [string]::IsNullOrWhiteSpace($env:POSREPORTBOT_INNO_SIGNTOOL)) {
  $InnoArgs += "/Sposreportbotsigntool=$env:POSREPORTBOT_INNO_SIGNTOOL"
}
$InnoArgs += $InnoScript

$InstallerOutput = Join-Path $ProjectRoot "dist\installer\POSReportBotSetup-$AppVersion.exe"
if (Test-Path -LiteralPath $InstallerOutput) {
  Remove-Item -LiteralPath $InstallerOutput -Force
}
& $InnoCompiler @InnoArgs
if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup compiler failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path $InstallerOutput)) {
  throw "Expected installer output not found: $InstallerOutput"
}

& $SignScript -Path $InstallerOutput
