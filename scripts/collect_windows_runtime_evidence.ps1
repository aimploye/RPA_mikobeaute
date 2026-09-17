param(
  [string]$ExecutablePath = "C:\Program Files (x86)\POSReportBot\POSReportBot.exe",
  [ValidateRange(1, 168)]
  [int]$LookbackHours = 24,
  [string]$OutputDirectory = "C:\ProgramData\POSReportBot\logs"
)

$ErrorActionPreference = "Stop"
$StartedAt = (Get-Date).AddHours(-$LookbackHours)
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DatedOutputDirectory = Join-Path $OutputDirectory (Get-Date -Format "yyyyMMdd")

function Convert-EventRecord {
  param([System.Diagnostics.Eventing.Reader.EventRecord]$EventRecord)

  [ordered]@{
    time_created = $EventRecord.TimeCreated.ToUniversalTime().ToString("o")
    id = $EventRecord.Id
    provider = $EventRecord.ProviderName
    level = $EventRecord.LevelDisplayName
    message = $EventRecord.Message
  }
}

function Get-FilteredEvents {
  param(
    [string]$LogName,
    [int[]]$Ids,
    [string]$MessagePattern
  )

  try {
    $Events = Get-WinEvent -FilterHashtable @{
      LogName = $LogName
      StartTime = $StartedAt
      Id = $Ids
    } -ErrorAction Stop
    $MatchedEvents = @($Events | Where-Object { $_.Message -match $MessagePattern })
    return [ordered]@{
      ok = $true
      log_name = $LogName
      events = @($MatchedEvents | ForEach-Object { Convert-EventRecord -EventRecord $_ })
      error = $null
    }
  } catch {
    if ($_.FullyQualifiedErrorId -like "NoMatchingEventsFound*") {
      return [ordered]@{
        ok = $true
        log_name = $LogName
        events = @()
        error = $null
      }
    }
    return [ordered]@{
      ok = $false
      log_name = $LogName
      events = @()
      error = $_.Exception.Message
    }
  }
}

$ExecutableEvidence = [ordered]@{
  path = $ExecutablePath
  exists = Test-Path -LiteralPath $ExecutablePath
  sha256 = $null
  signature_status = $null
  signer_subject = $null
  file_version = $null
  product_version = $null
}

if ($ExecutableEvidence.exists) {
  $ExecutableEvidence.sha256 = (Get-FileHash -LiteralPath $ExecutablePath -Algorithm SHA256).Hash
  $Signature = Get-AuthenticodeSignature -LiteralPath $ExecutablePath
  $ExecutableEvidence.signature_status = [string]$Signature.Status
  if ($null -ne $Signature.SignerCertificate) {
    $ExecutableEvidence.signer_subject = $Signature.SignerCertificate.Subject
  }
  $Version = (Get-Item -LiteralPath $ExecutablePath).VersionInfo
  $ExecutableEvidence.file_version = $Version.FileVersion
  $ExecutableEvidence.product_version = $Version.ProductVersion
}

$MessagePattern = "(?i)POSReportBot|POSReportBot\.exe"
$Payload = [ordered]@{
  schema_version = 1
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  lookback_hours = $LookbackHours
  machine_name = $env:COMPUTERNAME
  os = (Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber)
  executable = $ExecutableEvidence
  running_processes = @(
    Get-Process -Name "POSReportBot" -ErrorAction SilentlyContinue |
      Select-Object Id, ProcessName, StartTime, Path
  )
  defender = Get-FilteredEvents `
    -LogName "Microsoft-Windows-Windows Defender/Operational" `
    -Ids @(1116, 1117, 1121, 1122, 5007) `
    -MessagePattern $MessagePattern
  code_integrity = Get-FilteredEvents `
    -LogName "Microsoft-Windows-CodeIntegrity/Operational" `
    -Ids @(3004, 3033, 3076, 3077, 3089, 3099) `
    -MessagePattern $MessagePattern
  application_errors = Get-FilteredEvents `
    -LogName "Application" `
    -Ids @(1000, 1001, 1002) `
    -MessagePattern $MessagePattern
}

New-Item -ItemType Directory -Path $DatedOutputDirectory -Force | Out-Null
$OutputPath = Join-Path $DatedOutputDirectory "windows_runtime_evidence_$Timestamp.json"
$Payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output $OutputPath
