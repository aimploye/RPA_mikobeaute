[CmdletBinding()]
param(
    [string]$ExePath = 'C:\Program Files (x86)\POSReportBot\POSReportBot.exe',
    [string]$ConfigPath = 'C:\ProgramData\POSReportBot\config\app.yaml',
    [string]$StateDir = 'C:\ProgramData\POSReportBot\state',
    [string]$CompletedSummaryPath = '',
    [ValidateRange(1, 100)][int]$MaxConsecutiveFailures = 1,
    [ValidateSet('All', 'NonR05', 'R05')]
    [string]$TaskScope = 'All',
    [switch]$PreviewOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "POSReportBot executable not found: $ExePath"
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "POSReportBot config not found: $ConfigPath"
}

$versionText = (Get-Item -LiteralPath $ExePath).VersionInfo.ProductVersion
$versionMatch = [regex]::Match([string]$versionText, '\d+\.\d+\.\d+')
if (-not $versionMatch.Success) {
    throw "Unable to determine POSReportBot version from file metadata: $versionText"
}
$installedVersion = [version]$versionMatch.Value
$minimumVersion = [version]'2.1.20'
if ($installedVersion -lt $minimumVersion) {
    throw "Installed version is $installedVersion. Install 2.1.20 or newer; older builds omit R05 course refunds or can misclassify the secondary-filter workflow before opening Other Conditions."
}

$appRuntimeRoot = Join-Path $env:ProgramData 'POSReportBot'
$logDir = Join-Path $appRuntimeRoot 'logs\backfill_20260730_20260811'
$failureEvidenceDir = Join-Path $logDir 'failure_evidence'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$summary = [System.Collections.Generic.List[object]]::new()
$consecutiveFailures = 0
$circuitOpen = $false
$completedTaskKeys = @{}

if (-not [string]::IsNullOrWhiteSpace($CompletedSummaryPath)) {
    if (-not (Test-Path -LiteralPath $CompletedSummaryPath -PathType Leaf)) {
        throw "Completed summary not found: $CompletedSummaryPath"
    }
    foreach ($row in Import-Csv -LiteralPath $CompletedSummaryPath) {
        $propertyNames = @($row.PSObject.Properties.Name)
        if ('RunDate' -notin $propertyNames -or 'Task' -notin $propertyNames -or 'Status' -notin $propertyNames) {
            throw "Completed summary must contain RunDate, Task, and Status columns: $CompletedSummaryPath"
        }
        if ([string]$row.Status -notin @('SUCCESS', 'NO_DATA_CONTINUE', 'ALREADY_SUCCESS')) {
            continue
        }
        $runDate = ([string]$row.RunDate).Trim()
        $taskId = ([string]$row.Task).Trim().ToUpperInvariant()
        if ([string]::IsNullOrWhiteSpace($runDate) -or [string]::IsNullOrWhiteSpace($taskId)) {
            continue
        }
        $completedTaskKeys["$runDate|$taskId"] = $true
    }
}

function Test-BackfillTaskAlreadyCompleted {
    param(
        [Parameter(Mandatory = $true)][string]$TaskId,
        [Parameter(Mandatory = $true)][string]$RunDate
    )

    return $completedTaskKeys.ContainsKey("$RunDate|$($TaskId.ToUpperInvariant())")
}

function New-BackfillFailureEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$TaskId,
        [Parameter(Mandatory = $true)][string]$RunDate,
        [Parameter(Mandatory = $true)][datetime]$StartedAt,
        [Parameter(Mandatory = $true)][datetime]$FinishedAt
    )

    New-Item -ItemType Directory -Path $failureEvidenceDir -Force | Out-Null

    $collectorPath = Join-Path $PSScriptRoot 'collect_windows_runtime_evidence.ps1'
    $collectorError = $null
    if (Test-Path -LiteralPath $collectorPath -PathType Leaf) {
        try {
            & $collectorPath -ExecutablePath $ExePath -LookbackHours 6 -OutputDirectory (Join-Path $appRuntimeRoot 'logs') | Out-Null
        } catch {
            $collectorError = $_.Exception.Message
        }
    } else {
        $collectorError = "Runtime evidence collector not found: $collectorPath"
    }

    $windowStart = $StartedAt.AddMinutes(-2)
    $windowEnd = (Get-Date).AddMinutes(2)
    $roots = @(
        [pscustomobject]@{ Name = 'logs'; Path = (Join-Path $appRuntimeRoot 'logs') },
        [pscustomobject]@{ Name = 'screenshots'; Path = (Join-Path $appRuntimeRoot 'screenshots') },
        [pscustomobject]@{ Name = 'state'; Path = (Join-Path $appRuntimeRoot 'state') }
    )
    $selectedFiles = @(
        foreach ($root in $roots) {
            if (-not (Test-Path -LiteralPath $root.Path -PathType Container)) {
                continue
            }
            foreach ($file in Get-ChildItem -LiteralPath $root.Path -Recurse -File -Force -ErrorAction SilentlyContinue) {
                if ($file.FullName.StartsWith($failureEvidenceDir, [System.StringComparison]::OrdinalIgnoreCase)) {
                    continue
                }
                if ($file.LastWriteTime -lt $windowStart -or $file.LastWriteTime -gt $windowEnd) {
                    continue
                }
                [pscustomobject]@{ RootName = $root.Name; RootPath = $root.Path; File = $file }
            }
        }
    )

    $safeRunDate = $RunDate.Replace('-', '')
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmssfff'
    $bundleName = "{0}_{1}_{2}.zip" -f $safeRunDate, $TaskId, $timestamp
    $bundlePath = Join-Path $failureEvidenceDir $bundleName
    $stagingDir = Join-Path $failureEvidenceDir ('.staging_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
    $copied = [System.Collections.Generic.List[object]]::new()
    $copyErrors = [System.Collections.Generic.List[string]]::new()

    try {
        foreach ($entry in $selectedFiles) {
            try {
                $relativePath = $entry.File.FullName.Substring($entry.RootPath.Length).TrimStart('\')
                $destination = Join-Path (Join-Path $stagingDir $entry.RootName) $relativePath
                $destinationParent = Split-Path -Parent $destination
                New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
                Copy-Item -LiteralPath $entry.File.FullName -Destination $destination -Force
                $copied.Add([pscustomobject]@{
                    category = $entry.RootName
                    original_path = $entry.File.FullName
                    archive_path = ("{0}\{1}" -f $entry.RootName, $relativePath)
                    bytes = $entry.File.Length
                    last_write_time = $entry.File.LastWriteTime.ToString('o')
                })
            } catch {
                $copyErrors.Add("$($entry.File.FullName): $($_.Exception.Message)")
            }
        }

        $manifest = [ordered]@{
            schema_version = 1
            created_at = (Get-Date).ToUniversalTime().ToString('o')
            run_date = $RunDate
            task_id = $TaskId
            child_started_at = $StartedAt.ToString('o')
            child_finished_at = $FinishedAt.ToString('o')
            executable_path = $ExePath
            executable_product_version = (Get-Item -LiteralPath $ExePath).VersionInfo.ProductVersion
            collection_window_start = $windowStart.ToString('o')
            collection_window_end = $windowEnd.ToString('o')
            collector_error = $collectorError
            copied_file_count = $copied.Count
            copied_files = @($copied)
            copy_errors = @($copyErrors)
            excluded = @('config', 'downloads', 'output', 'credentials')
        }
        $manifestPath = Join-Path $stagingDir 'manifest.json'
        $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        Compress-Archive -Path (Join-Path $stagingDir '*') -DestinationPath $bundlePath -CompressionLevel Optimal -Force
    } finally {
        $evidenceRootFull = [System.IO.Path]::GetFullPath($failureEvidenceDir).TrimEnd('\') + '\'
        $stagingFull = [System.IO.Path]::GetFullPath($stagingDir)
        if ($stagingFull.StartsWith($evidenceRootFull, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $stagingFull)) {
            Remove-Item -LiteralPath $stagingFull -Recurse -Force
        }
    }

    return $bundlePath
}

function Invoke-BackfillTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskId,
        [Parameter(Mandatory = $true)][string]$RunDate,
        [switch]$ForceRun
    )

    $label = "$RunDate $TaskId"
    if (-not $ForceRun -and (Test-BackfillTaskAlreadyCompleted -TaskId $TaskId -RunDate $RunDate)) {
        Write-Host "[SKIP-SUCCESS] $label"
        $summary.Add([pscustomobject]@{
            RunDate = $RunDate
            Task = $TaskId
            ExitCode = 0
            Status = 'ALREADY_SUCCESS'
            EvidenceBundle = $CompletedSummaryPath
        })
        return $true
    }
    if ($PreviewOnly) {
        Write-Host "[PREVIEW] $label"
        $summary.Add([pscustomobject]@{ RunDate = $RunDate; Task = $TaskId; ExitCode = $null; Status = 'PREVIEW'; EvidenceBundle = $null })
        return $true
    }

    Write-Host "[RUN] $label"
    $logPath = Join-Path $logDir ("{0}_{1}.log" -f $RunDate.Replace('-', ''), $TaskId)
    $stdoutPath = Join-Path $logDir ("{0}_{1}_stdout.txt" -f $RunDate.Replace('-', ''), $TaskId)
    $stderrPath = Join-Path $logDir ("{0}_{1}_stderr.txt" -f $RunDate.Replace('-', ''), $TaskId)
    $arguments = @('--config', $ConfigPath, '--run-task', $TaskId, '--today', $RunDate)
    $startedAt = Get-Date
    $processError = $null
    try {
        $process = Start-Process `
            -FilePath $ExePath `
            -ArgumentList $arguments `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -Wait `
            -PassThru
        $exitCode = $process.ExitCode
    } catch {
        $exitCode = -1
        $processError = $_.Exception.Message
    }
    $finishedAt = Get-Date
    $r13NoDataConfirmed = $false
    if ($TaskId -eq 'R13' -and $exitCode -ne 0) {
        $stateDateDir = $RunDate.Replace('-', '')
        $markerPath = Join-Path $StateDir "$stateDateDir\r13_no_report_data.json"
        if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
            try {
                $marker = Get-Content -Raw -Encoding UTF8 -LiteralPath $markerPath | ConvertFrom-Json
                $runDateValue = [datetime]::ParseExact($RunDate, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
                $expectedEndDate = $runDateValue.AddDays(-1).ToString('yyyy/MM/dd')
                $expectedStartDate = $runDateValue.AddDays(-1).ToString('yyyy/MM') + '/01'
                $r13NoDataConfirmed = (
                    $marker.error_code -eq 'NO_REPORT_DATA' -and
                    $marker.run_date -eq $RunDate -and
                    $marker.start_date -eq $expectedStartDate -and
                    $marker.end_date -eq $expectedEndDate
                )
            } catch {
                $r13NoDataConfirmed = $false
            }
        }
    }
    $status = if ($exitCode -eq 0) { 'SUCCESS' } elseif ($r13NoDataConfirmed) { 'NO_DATA_CONTINUE' } else { 'FAILED' }
    $logLines = @(
        "RunDate=$RunDate"
        "Task=$TaskId"
        "StartedAt=$($startedAt.ToString('o'))"
        "FinishedAt=$($finishedAt.ToString('o'))"
        "ExitCode=$exitCode"
        "Status=$status"
        "ProcessError=$processError"
        "StandardOutput=$stdoutPath"
        "StandardError=$stderrPath"
        "ApplicationLogs=$env:ProgramData\POSReportBot\logs"
    )
    $logLines | Set-Content -LiteralPath $logPath -Encoding UTF8

    $failureEvidenceBundle = $null
    $failureEvidenceError = $null
    if ($exitCode -ne 0) {
        try {
            $failureEvidenceBundle = New-BackfillFailureEvidence `
                -TaskId $TaskId `
                -RunDate $RunDate `
                -StartedAt $startedAt `
                -FinishedAt $finishedAt
            Write-Host "[EVIDENCE] $failureEvidenceBundle"
        } catch {
            $failureEvidenceError = $_.Exception.Message
            Write-Warning "Unable to package failure evidence for $label`: $failureEvidenceError"
        }
    }
    @(
        $logLines
        "FailureEvidenceBundle=$failureEvidenceBundle"
        "FailureEvidenceError=$failureEvidenceError"
    ) | Set-Content -LiteralPath $logPath -Encoding UTF8
    Write-Host "[$status] $label (exit code $exitCode)"
    if ($status -eq 'FAILED') {
        if ((Test-Path -LiteralPath $stdoutPath -PathType Leaf) -and (Get-Item -LiteralPath $stdoutPath).Length -gt 0) {
            Write-Host "[STDOUT] $stdoutPath"
            Get-Content -Raw -Encoding UTF8 -LiteralPath $stdoutPath | Write-Host
        }
        if ((Test-Path -LiteralPath $stderrPath -PathType Leaf) -and (Get-Item -LiteralPath $stderrPath).Length -gt 0) {
            Write-Host "[STDERR] $stderrPath"
            Get-Content -Raw -Encoding UTF8 -LiteralPath $stderrPath | Write-Host
        }
    }
    $summary.Add([pscustomobject]@{
        RunDate = $RunDate
        Task = $TaskId
        ExitCode = $exitCode
        Status = $status
        EvidenceBundle = $failureEvidenceBundle
    })

    if ($exitCode -eq 0 -or $r13NoDataConfirmed) {
        $script:consecutiveFailures = 0
    } else {
        $script:consecutiveFailures += 1
        if ($script:consecutiveFailures -ge $MaxConsecutiveFailures) {
            $script:circuitOpen = $true
            Write-Warning "Backfill circuit breaker opened after $($script:consecutiveFailures) consecutive failures. Remaining tasks will not be started."
        }
    }
    return $exitCode -eq 0 -or $r13NoDataConfirmed
}

function Test-BackfillTaskInScope {
    param([Parameter(Mandatory = $true)][string]$TaskId)

    if ($TaskScope -eq 'R05') {
        return $TaskId -eq 'R05'
    }
    if ($TaskScope -eq 'NonR05') {
        return $TaskId -ne 'R05'
    }
    return $true
}

# This manifest is the remaining work after the full 2026-07-01 through
# 2026-08-11 Drive audit and the 2026-08-13 real-POS R04 verification.
# The 2026-07-01 R04 gap is intentionally absent because it completed and
# uploaded successfully in the isolated verification run. R05 remains
# selectable as an isolated scope. Version 2.1.20 restores the user-verified
# course refund + Other Conditions + secondary-filter sequence.
# R06 uses RunDate in filenames. Other listed reports use their planner-specific date semantics.
# Every missing R14 must run R13, W01, and R14 in that order for the same historical RunDate.
$manifest = @(
    [pscustomobject]@{ RunDate = '2026-07-01'; Tasks = @('R06'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-02'; Tasks = @('R04'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-03'; Tasks = @('R04'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-04'; Tasks = @('R04'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-05'; Tasks = @('R04'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-06'; Tasks = @('R04'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-07'; Tasks = @('R04'); RunR14 = $false },
    [pscustomobject]@{
        RunDate = '2026-07-18'
        Tasks = @('R01', 'R02', 'R03', 'R04', 'R05', 'R06', 'R07', 'R08', 'R09', 'R10', 'R11', 'R12')
        RunR14 = $true
    },
    [pscustomobject]@{
        RunDate = '2026-07-19'
        Tasks = @('R01', 'R02', 'R03', 'R04', 'R05', 'R06', 'R07', 'R08', 'R09', 'R10', 'R11', 'R12')
        RunR14 = $true
    },
    [pscustomobject]@{ RunDate = '2026-07-23'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-25'; Tasks = @('R02'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-28'; Tasks = @('R12'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-07-29'; Tasks = @('R01', 'R11', 'R12'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-02'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-03'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-05'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-07'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-08'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-09'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-10'; Tasks = @('R05'); RunR14 = $false },
    [pscustomobject]@{ RunDate = '2026-08-11'; Tasks = @('R01', 'R02', 'R03', 'R05'); RunR14 = $false }
)

$batchMutexName = 'Local\POSReportBot.AutomationBatch'
$automationMutexName = 'Local\POSReportBot.AutomationRunner'
$parentLockEnvName = 'POSREPORTBOT_PARENT_RUN_LOCK'
$batchMutex = $null
$batchLockAcquired = $false
$batchLockId = [guid]::NewGuid().ToString('N')
$previousParentLock = [Environment]::GetEnvironmentVariable($parentLockEnvName, 'Process')
$ownerPath = Join-Path $StateDir 'automation_run_owner.json'

if (-not $PreviewOnly) {
    $batchMutex = [System.Threading.Mutex]::new($false, $batchMutexName)
    try {
        $batchLockAcquired = $batchMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $batchLockAcquired = $true
    }
    if (-not $batchLockAcquired) {
        $ownerText = ''
        if (Test-Path -LiteralPath $ownerPath -PathType Leaf) {
            $ownerText = (Get-Content -Raw -Encoding UTF8 -LiteralPath $ownerPath).Trim()
        }
        $batchMutex.Dispose()
        throw "Another POSReportBot automation run is active. Wait for it to finish before backfill. Owner: $ownerText"
    }

    # The batch reservation prevents new independent runs from entering while
    # child EXEs are launched.  Also verify that no run already owns the actual
    # POS automation mutex before the batch starts.
    $automationProbeMutex = [System.Threading.Mutex]::new($false, $automationMutexName)
    $automationProbeAcquired = $false
    try {
        $automationProbeAcquired = $automationProbeMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $automationProbeAcquired = $true
    }
    if (-not $automationProbeAcquired) {
        $ownerText = ''
        if (Test-Path -LiteralPath $ownerPath -PathType Leaf) {
            $ownerText = (Get-Content -Raw -Encoding UTF8 -LiteralPath $ownerPath).Trim()
        }
        $automationProbeMutex.Dispose()
        $batchMutex.ReleaseMutex()
        $batchLockAcquired = $false
        $batchMutex.Dispose()
        throw "Another POSReportBot automation run is active. Wait for it to finish before backfill. Owner: $ownerText"
    }
    $automationProbeMutex.ReleaseMutex()
    $automationProbeMutex.Dispose()
}

$scriptExitCode = 0
try {
    if (-not $PreviewOnly) {
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
        [Environment]::SetEnvironmentVariable($parentLockEnvName, $batchLockId, 'Process')
        [pscustomobject]@{
            lock_id = $batchLockId
            pid = $PID
            run_source = 'historical_backfill'
            app_version = $installedVersion.ToString()
            started_at = (Get-Date).ToUniversalTime().ToString('o')
        } | ConvertTo-Json | Set-Content -LiteralPath $ownerPath -Encoding UTF8
    }

    :ManifestLoop foreach ($entry in $manifest) {
        foreach ($taskId in $entry.Tasks) {
            if (-not (Test-BackfillTaskInScope -TaskId $taskId)) {
                continue
            }
            [void](Invoke-BackfillTask -TaskId $taskId -RunDate $entry.RunDate)
            if ($circuitOpen) {
                break ManifestLoop
            }
        }

        if (-not $entry.RunR14) {
            continue
        }
        # R13 -> W01 -> R14 is one atomic dependency chain. NonR05 and All
        # execute the complete chain; R05 scope skips it completely.
        if ($TaskScope -eq 'R05') {
            continue
        }
        if (Test-BackfillTaskAlreadyCompleted -TaskId 'R14' -RunDate $entry.RunDate) {
            Write-Host "[SKIP-SUCCESS] $($entry.RunDate) R13 -> W01 -> R14 (R14 already successful)"
            $summary.Add([pscustomobject]@{
                RunDate = $entry.RunDate
                Task = 'R14'
                ExitCode = 0
                Status = 'ALREADY_SUCCESS'
                EvidenceBundle = $CompletedSummaryPath
            })
            continue
        }
        $r13Succeeded = Invoke-BackfillTask -TaskId 'R13' -RunDate $entry.RunDate -ForceRun
        if ($circuitOpen) {
            break ManifestLoop
        }
        if (-not $r13Succeeded) {
            Write-Warning "$($entry.RunDate) R13 failed without a matching no-data marker. Skipping W01 and R14 to prevent stale raw data use."
            $summary.Add([pscustomobject]@{ RunDate = $entry.RunDate; Task = 'W01'; ExitCode = $null; Status = 'BLOCKED_BY_R13'; EvidenceBundle = $null })
            $summary.Add([pscustomobject]@{ RunDate = $entry.RunDate; Task = 'R14'; ExitCode = $null; Status = 'BLOCKED_BY_R13'; EvidenceBundle = $null })
            continue
        }

        $w01Succeeded = Invoke-BackfillTask -TaskId 'W01' -RunDate $entry.RunDate -ForceRun
        if ($circuitOpen) {
            break ManifestLoop
        }
        if (-not $w01Succeeded) {
            Write-Warning "$($entry.RunDate) W01 failed. Skipping R14 to prevent unsynchronized inventory use."
            $summary.Add([pscustomobject]@{ RunDate = $entry.RunDate; Task = 'R14'; ExitCode = $null; Status = 'BLOCKED_BY_W01'; EvidenceBundle = $null })
            continue
        }
        [void](Invoke-BackfillTask -TaskId 'R14' -RunDate $entry.RunDate -ForceRun)
        if ($circuitOpen) {
            break ManifestLoop
        }
    }

    $summary | Format-Table -AutoSize
    if (-not $PreviewOnly) {
        $summaryFileName = if ($TaskScope -eq 'All') {
            'backfill_summary.csv'
        } else {
            'backfill_summary_{0}.csv' -f $TaskScope
        }
        $summaryPath = Join-Path $logDir $summaryFileName
        $summary | Export-Csv -LiteralPath $summaryPath -NoTypeInformation -Encoding UTF8
        Write-Host "Backfill summary: $summaryPath"
    }

    $failed = @($summary | Where-Object { $_.Status -notin @('SUCCESS', 'PREVIEW', 'NO_DATA_CONTINUE', 'ALREADY_SUCCESS') })
    if ($failed.Count -gt 0) {
        $scriptExitCode = 1
    }
} finally {
    if ($batchLockAcquired) {
        if ($null -eq $previousParentLock) {
            [Environment]::SetEnvironmentVariable($parentLockEnvName, $null, 'Process')
        } else {
            [Environment]::SetEnvironmentVariable($parentLockEnvName, $previousParentLock, 'Process')
        }
        if (Test-Path -LiteralPath $ownerPath -PathType Leaf) {
            try {
                $owner = Get-Content -Raw -Encoding UTF8 -LiteralPath $ownerPath | ConvertFrom-Json
                if ($owner.lock_id -eq $batchLockId) {
                    Remove-Item -LiteralPath $ownerPath -Force
                }
            } catch {
                Write-Warning "Unable to clean run-lock owner metadata: $($_.Exception.Message)"
            }
        }
        $batchMutex.ReleaseMutex()
        $batchMutex.Dispose()
    }
}
exit $scriptExitCode
