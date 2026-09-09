# render-gantt-png.ps1
# Renders the WBS gantt HTML to a PNG next to it, so it can be viewed from a phone
# (GitHub / Obsidian mobile) without a browser that runs JavaScript.
#
# NOTE: every comment in this file is ASCII on purpose.
#   Windows PowerShell 5.1 reads BOM-less files as the system ANSI codepage (CP932 in JP),
#   so Japanese comments can silently break execution - no error, the script just stops.
#   Keep explanations in the .md files, not here.
#
# Japanese paths: chrome --screenshot fails when the output path contains multibyte
#   characters, so the render goes through an ASCII temp dir and is copied back.
#   Copies use -LiteralPath to avoid NFC/NFD normalization problems.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File <abs path>\render-gantt-png.ps1
#   powershell ... -File <abs>\render-gantt-png.ps1 -Html wbs-gantt_v2.html -Height 2000
#
#   From Claude Code's PowerShell tool, pass an ABSOLUTE path to -File.
#   A relative path fails with "argument does not exist".

param(
  [string]$Html   = "wbs-gantt_v1.html",
  [string]$Out    = "",
  [int]$Width     = 1240,
  [int]$Height    = 1500
)

$ErrorActionPreference = "Stop"

# Default the output name to the input name with a .png extension.
if ([string]::IsNullOrWhiteSpace($Out)) {
  $Out = [System.IO.Path]::GetFileNameWithoutExtension($Html) + ".png"
}

# Resolve paths relative to this script's own folder.
$scriptDir = [System.IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)
$src = Join-Path $scriptDir $Html
$dst = Join-Path $scriptDir $Out

if (-not (Test-Path -LiteralPath $src)) { throw "source not found: $src" }

$chromeCandidates = @(
  "C:\Program Files\Google\Chrome\Application\chrome.exe",
  "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
  "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chrome) { throw "no chromium browser found in standard locations" }

$tmpDir = Join-Path $env:LOCALAPPDATA "Temp\ganttshot"
if (-not (Test-Path -LiteralPath $tmpDir)) {
  New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
}
$tmpHtml = Join-Path $tmpDir "dash.html"
$tmpPng  = Join-Path $tmpDir "dash.png"

Copy-Item -LiteralPath $src -Destination $tmpHtml -Force
if (Test-Path -LiteralPath $tmpPng) { Remove-Item -LiteralPath $tmpPng -Force }

$fileUrl = "file:///" + ($tmpHtml -replace '\\','/')
& $chrome --headless --disable-gpu --no-sandbox --hide-scrollbars `
  --force-device-scale-factor=1 `
  "--screenshot=$tmpPng" "--window-size=$Width,$Height" $fileUrl | Out-Null

if (-not (Test-Path -LiteralPath $tmpPng)) { throw "screenshot failed: $tmpPng was not created" }

Copy-Item -LiteralPath $tmpPng -Destination $dst -Force
$sizeKb = [math]::Round((Get-Item -LiteralPath $dst).Length / 1KB, 1)
Write-Host "rendered: $dst (${sizeKb} KB, ${Width}x${Height})"
