# gantt-hook.ps1
# PostToolUse hook body. Runs after Claude Code's Edit/Write.
# Re-renders the PNG only when the edited file is a wbs-gantt_v<N>.html, and renders
# that exact file. Any other edit exits immediately without launching a browser.
#
# ASCII comments only - see the note in render-gantt-png.ps1.
#
# Input : hook JSON on stdin (reads .tool_input.file_path)
# Output: always exit 0. A failure here must never block the edit.
#
# Wire it up in .claude/settings.json:
#   "hooks": { "PostToolUse": [ { "matcher": "Write|Edit",
#     "hooks": [ { "type": "command",
#       "command": "powershell -NoProfile -ExecutionPolicy Bypass -File wbs-gantt/gantt-hook.ps1" } ] } ] }
#
# The hook has been observed NOT to fire in some cases. After editing the HTML,
# check the PNG's timestamp; if it is stale, run render-gantt-png.ps1 by hand.

$ErrorActionPreference = "SilentlyContinue"

$raw = [Console]::In.ReadToEnd()
if (-not $raw) { exit 0 }

try { $payload = $raw | ConvertFrom-Json } catch { exit 0 }

$fp = $payload.tool_input.file_path
if (-not $fp) { exit 0 }

# Accept both / and \ separators. Capture the file name so any version renders.
if ($fp -notmatch '(wbs-gantt_v\d+)\.html$') { exit 0 }
$htmlName = $Matches[1] + ".html"

$dir = [System.IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)
$render = Join-Path $dir 'render-gantt-png.ps1'
if (Test-Path -LiteralPath $render) {
  $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $render -Html $htmlName 2>&1
}

exit 0
