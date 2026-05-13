param(
  [string]$ProjectRoot = "D:\工作\霈方國際\工作流\raw_data_RPA"
)

$ErrorActionPreference = "Stop"

$dirs = @(
  "src\pos_report_bot\app",
  "src\pos_report_bot\config",
  "src\pos_report_bot\core",
  "src\pos_report_bot\drive",
  "src\pos_report_bot\gui\pages",
  "src\pos_report_bot\installer",
  "src\pos_report_bot\notifier",
  "src\pos_report_bot\pos",
  "src\pos_report_bot\reports\handlers",
  "src\pos_report_bot\scheduler",
  "src\pos_report_bot\storage",
  "tests\unit",
  "tests\integration",
  "tests\fixtures",
  "assets\icons",
  "build",
  "dist",
  "_external",
  ".agents\skills",
  ".agents\plugins",
  "runtime_dev\downloads",
  "runtime_dev\output",
  "runtime_dev\logs",
  "runtime_dev\screenshots",
  "runtime_dev\state"
)

New-Item -ItemType Directory -Force -Path $ProjectRoot | Out-Null

foreach ($dir in $dirs) {
  New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot $dir) | Out-Null
}

Write-Host "Project folders created under: $ProjectRoot"
