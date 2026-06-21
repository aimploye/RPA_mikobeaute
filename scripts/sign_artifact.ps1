param(
  [Parameter(Mandatory = $true)]
  [string]$Path
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Path)) {
  throw "Cannot sign missing artifact: $Path"
}

$Thumbprint = $env:POSREPORTBOT_SIGN_CERT_SHA1
$PfxPath = $env:POSREPORTBOT_SIGN_CERT_PFX
$RequireSigning = $env:POSREPORTBOT_REQUIRE_SIGNING -in @("1", "true", "TRUE", "yes", "YES")

if ([string]::IsNullOrWhiteSpace($Thumbprint) -and [string]::IsNullOrWhiteSpace($PfxPath)) {
  if ($RequireSigning) {
    throw "Release signing is required. Set POSREPORTBOT_SIGN_CERT_SHA1 or POSREPORTBOT_SIGN_CERT_PFX before building POS artifacts."
  }
  Write-Host "Skipping code signing; no POSReportBot signing certificate environment variables configured: $Path"
  exit 0
}

$SignTool = $env:POSREPORTBOT_SIGNTOOL
if ([string]::IsNullOrWhiteSpace($SignTool)) {
  $WindowsKitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
  if (Test-Path $WindowsKitsRoot) {
    $SignTool = Get-ChildItem $WindowsKitsRoot -Recurse -Filter "signtool.exe" |
      Where-Object { $_.FullName -like "*\x64\signtool.exe" } |
      Sort-Object FullName -Descending |
      Select-Object -First 1 -ExpandProperty FullName
  }
}

if ([string]::IsNullOrWhiteSpace($SignTool) -or -not (Test-Path $SignTool)) {
  throw "Code signing was requested, but signtool.exe was not found. Set POSREPORTBOT_SIGNTOOL to the Windows SDK signtool.exe path."
}

$TimestampUrl = $env:POSREPORTBOT_SIGN_TIMESTAMP_URL
if ([string]::IsNullOrWhiteSpace($TimestampUrl)) {
  $TimestampUrl = "http://timestamp.digicert.com"
}

$SignArgs = @("sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $TimestampUrl)

if (-not [string]::IsNullOrWhiteSpace($Thumbprint)) {
  $SignArgs += @("/sha1", $Thumbprint)
} else {
  if (-not (Test-Path $PfxPath)) {
    throw "Code signing PFX file not found: $PfxPath"
  }
  $SignArgs += @("/f", $PfxPath)
  if (-not [string]::IsNullOrWhiteSpace($env:POSREPORTBOT_SIGN_PFX_PASSWORD)) {
    $SignArgs += @("/p", $env:POSREPORTBOT_SIGN_PFX_PASSWORD)
  }
}

$SignArgs += $Path

& $SignTool @SignArgs
if ($LASTEXITCODE -ne 0) {
  throw "signtool failed for artifact: $Path"
}
