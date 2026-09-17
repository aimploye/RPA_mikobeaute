param(
  [Parameter(Mandatory = $true)][string]$DistributionDir,
  [Parameter(Mandatory = $true)][string]$BuildDir
)

$ErrorActionPreference = "Stop"

$ResolvedDistributionDir = (Resolve-Path -LiteralPath $DistributionDir).Path
$ResolvedBuildDir = (Resolve-Path -LiteralPath $BuildDir).Path
$InternalDir = Join-Path $ResolvedDistributionDir "_internal"
if (-not (Test-Path -LiteralPath $InternalDir -PathType Container)) {
  throw "Frozen runtime verification failed: _internal directory is missing: $InternalDir"
}

$RequiredQtFiles = @(
  (Join-Path $InternalDir "PySide6\QtCore.pyd"),
  (Join-Path $InternalDir "PySide6\Qt6Core.dll"),
  (Join-Path $InternalDir "PySide6\pyside6.abi3.dll"),
  (Join-Path $InternalDir "shiboken6\shiboken6.abi3.dll")
)
$MissingQtFiles = @($RequiredQtFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($MissingQtFiles.Count -gt 0) {
  throw "Frozen runtime verification failed: required Qt/Shiboken files are missing: $($MissingQtFiles -join ', ')"
}

# Windows 10/11 provides UCRT and API-set forwarders. App-local copies found in
# this project were traced to the Codex document-tool runtime (libheif), not to
# Python or PySide6. Shipping those build-host DLLs makes an older target load
# Windows 11 entry points and can surface as QtCore WinError 127.
$ForbiddenRootFiles = @()
$ForbiddenRootFiles += @(Get-ChildItem -LiteralPath $InternalDir -File -Filter "ucrtbase.dll" -ErrorAction SilentlyContinue)
$ForbiddenRootFiles += @(Get-ChildItem -LiteralPath $InternalDir -File -Filter "api-ms-win-*.dll" -ErrorAction SilentlyContinue)
$ForbiddenRootFiles += @(Get-ChildItem -LiteralPath $InternalDir -File -Filter "libcrypto-3-x64.dll" -ErrorAction SilentlyContinue)
$ForbiddenRootFiles += @(Get-ChildItem -LiteralPath $InternalDir -File -Filter "libssl-3-x64.dll" -ErrorAction SilentlyContinue)
$ForbiddenRootFiles += @(Get-ChildItem -LiteralPath $InternalDir -File -Filter "icuuc.dll" -ErrorAction SilentlyContinue)
$ForbiddenRootFiles += @(Get-ChildItem -LiteralPath $InternalDir -File -Filter "icudt*.dll" -ErrorAction SilentlyContinue)
$ForbiddenRootFiles = @($ForbiddenRootFiles | Sort-Object FullName -Unique)
if ($ForbiddenRootFiles.Count -gt 0) {
  $Names = $ForbiddenRootFiles | ForEach-Object { $_.Name }
  throw "Frozen runtime verification failed: build-host system/tool DLLs leaked into _internal: $($Names -join ', ')"
}

$TocFiles = @(Get-ChildItem -LiteralPath $ResolvedBuildDir -File -Filter "*.toc" -ErrorAction SilentlyContinue)
if ($TocFiles.Count -eq 0) {
  throw "Frozen runtime verification failed: PyInstaller TOC files are missing: $ResolvedBuildDir"
}
$ForbiddenProvenancePatterns = @(
  "codex-runtimes",
  "dependencies\\native\\poppler",
  "dependencies\\native\\libheif"
)
$ProvenanceHits = @(
  foreach ($TocFile in $TocFiles) {
    foreach ($Pattern in $ForbiddenProvenancePatterns) {
      Select-String -LiteralPath $TocFile.FullName -SimpleMatch -Pattern $Pattern -ErrorAction SilentlyContinue
    }
  }
)
if ($ProvenanceHits.Count -gt 0) {
  $Locations = $ProvenanceHits | ForEach-Object { "$($_.Path):$($_.LineNumber)" } | Sort-Object -Unique
  throw "Frozen runtime verification failed: external build-tool DLL provenance detected: $($Locations -join ', ')"
}

Write-Output "Frozen runtime verification passed: Qt/Shiboken present; no external build-tool or app-local Windows system runtime detected."
