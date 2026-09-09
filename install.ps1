# install.ps1
# Installs the study-project skills into ~/.claude/skills/ and rewrites the
# {{TEMPLATE_REPO}} placeholder so the skills can find this repository.
#
# ASCII comments only - Windows PowerShell 5.1 reads BOM-less files as the system
# ANSI codepage (CP932 in JP), and multibyte comments can silently break execution.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File <abs path>\install.ps1
#   powershell ... -File install.ps1 -DryRun

param(
  [switch]$DryRun,
  [string]$SkillsRoot = "$env:USERPROFILE\.claude\skills"
)

$ErrorActionPreference = "Stop"

$repo = [System.IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)
$src  = Join-Path $repo "skills"

if (-not (Test-Path -LiteralPath $src)) { throw "skills folder not found: $src" }

# Forward slashes so the path is safe inside Markdown and Python-ish contexts.
$repoForSkill = $repo -replace '\\','/'

Write-Host "template repo : $repoForSkill"
Write-Host "skills target : $SkillsRoot"
Write-Host ""

if (-not (Test-Path -LiteralPath $SkillsRoot)) {
  if ($DryRun) { Write-Host "[dry-run] would create $SkillsRoot" }
  else { New-Item -ItemType Directory -Path $SkillsRoot -Force | Out-Null }
}

$installed = 0
foreach ($dir in (Get-ChildItem -LiteralPath $src -Directory)) {
  $skillFile = Join-Path $dir.FullName "SKILL.md"
  if (-not (Test-Path -LiteralPath $skillFile)) {
    Write-Host ("  skip {0} (no SKILL.md)" -f $dir.Name)
    continue
  }

  $destDir  = Join-Path $SkillsRoot $dir.Name
  $destFile = Join-Path $destDir "SKILL.md"

  $body = [System.IO.File]::ReadAllText($skillFile, [System.Text.Encoding]::UTF8)
  $body = $body.Replace("{{TEMPLATE_REPO}}", $repoForSkill)

  if ($DryRun) {
    Write-Host ("  [dry-run] {0} -> {1}" -f $dir.Name, $destFile)
  } else {
    if (-not (Test-Path -LiteralPath $destDir)) {
      New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    # UTF-8 without BOM: Claude Code reads skills as UTF-8.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($destFile, $body, $utf8NoBom)
    Write-Host ("  installed {0}" -f $dir.Name)
  }
  $installed++
}

Write-Host ""
Write-Host ("{0} skill(s) {1}." -f $installed, $(if ($DryRun) { "would be installed" } else { "installed" }))
Write-Host ""
Write-Host "Next: start a new study project with"
Write-Host "  cp -r `"$repoForSkill/template`" ~/study-<name>"
Write-Host "then open Claude Code there and say what you want to study."
