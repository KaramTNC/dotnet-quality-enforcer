$ErrorActionPreference = 'SilentlyContinue'
$raw = [Console]::In.ReadToEnd()
try { $payload = if ($raw) { $raw | ConvertFrom-Json } else { $null } } catch { $payload = $null }
$root = if ($payload.cwd) { [IO.Path]::GetFullPath([string]$payload.cwd) } else { (Get-Location).Path }
$handoffDir = Join-Path $root '.codex-output/context-handoffs'
$latest = Get-ChildItem -LiteralPath $handoffDir -Filter '*.md' -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
$status = @(git -C $root status --short 2>&1)
$summary = if ($status) { $status -join '; ' } else { 'clean' }
$latestPath = if ($latest) { $latest.FullName } else { 'none yet' }
[pscustomobject]@{ hookSpecificOutput = [pscustomobject]@{
    hookEventName = 'SessionStart'
    additionalContext = "Resume context: cwd=$root; worktree=$summary; latest handoff=$latestPath. Keep scope, blockers, and next validation explicit."
}} | ConvertTo-Json -Compress

