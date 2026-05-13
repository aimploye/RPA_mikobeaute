param(
  [string]$ProjectRoot = "D:\工作\霈方國際\工作流\raw_data_RPA"
)

$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot
Write-Host "Starting Codex in: $ProjectRoot"
Write-Host "Recommended first prompt:"
Write-Host "請先閱讀 AGENTS.md、docs/PROJECT_SPEC.md、docs/ARCHITECTURE.md、docs/ACCEPTANCE_CRITERIA.md，並使用 `$spec-driven-development 與 `$planning-and-task-breakdown。不要寫 POS 實機操作成功宣告。先產出實作計畫，然後只建立可在沒有 POS 的本機驗證的 MVP-0.1。"
codex
