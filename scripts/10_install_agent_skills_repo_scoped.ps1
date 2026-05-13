param(
  [string]$ProjectRoot = "D:\工作\霈方國際\工作流\raw_data_RPA",
  [string]$RepoUrl = "https://github.com/addyosmani/agent-skills.git"
)

$ErrorActionPreference = "Stop"

$ExternalDir = Join-Path $ProjectRoot "_external"
$CloneDir = Join-Path $ExternalDir "agent-skills"
$TargetSkillsDir = Join-Path $ProjectRoot ".agents\skills"

New-Item -ItemType Directory -Force -Path $ExternalDir | Out-Null
New-Item -ItemType Directory -Force -Path $TargetSkillsDir | Out-Null

if (-Not (Test-Path $CloneDir)) {
  git clone $RepoUrl $CloneDir
} else {
  Push-Location $CloneDir
  git pull
  Pop-Location
}

# 只複製本案建議先用的技能，避免一次載入太多。
$skills = @(
  "using-agent-skills",
  "spec-driven-development",
  "planning-and-task-breakdown",
  "incremental-implementation",
  "test-driven-development",
  "source-driven-development",
  "api-and-interface-design",
  "frontend-ui-engineering",
  "security-and-hardening",
  "debugging-and-error-recovery",
  "code-review-and-quality",
  "ci-cd-and-automation",
  "shipping-and-launch"
)

foreach ($skill in $skills) {
  $src = Join-Path $CloneDir "skills\$skill"
  $dst = Join-Path $TargetSkillsDir $skill

  if (Test-Path $src) {
    if (Test-Path $dst) {
      Remove-Item -Recurse -Force $dst
    }
    Copy-Item -Recurse $src $dst
    Write-Host "Installed skill: $skill"
  } else {
    Write-Warning "Skill not found in repo: $skill"
  }
}

Write-Host ""
Write-Host "Repo-scoped skills installed to: $TargetSkillsDir"
Write-Host "Restart Codex CLI, then run /skills to verify."
