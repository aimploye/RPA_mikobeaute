param(
  [string]$ProjectRoot = "D:\工作\霈方國際\工作流\raw_data_RPA"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

if (-Not (Test-Path ".git")) {
  git init
}

git status
