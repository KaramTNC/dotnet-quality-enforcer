$ErrorActionPreference = 'SilentlyContinue'
$raw = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
try { $payload = $raw | ConvertFrom-Json } catch { exit 0 }
$toolName = [string]$payload.tool_name
$inputObject = $payload.tool_input
$command = [string]$payload.command
if (-not $command -and $inputObject) {
    $command = [string]$inputObject.command
    if (-not $command) { $command = [string]$inputObject.input }
}
$text = "$toolName $command".ToLowerInvariant()
$risk = $null
if ($text -match '(^|[\\/])\.env($|[.])|private\.key|id_rsa|secret|api[_-]?key|access[_-]?token') {
    $risk = 'secret or credential handling'
} elseif ($text -match 'git\s+(reset\s+--hard|clean\s+-f|checkout\s+--)|rm\s+-rf|remove-item.*-recurse|format-volume') {
    $risk = 'destructive filesystem or Git operation'
} elseif ($text -match 'git\s+push|docker\s+push|kubectl\s+(apply|delete)|\bdeploy\b|\bssh\b|\bscp\b') {
    $risk = 'external publication or deployment operation'
}
if ($risk) {
    [pscustomobject]@{ hookSpecificOutput = [pscustomobject]@{
        hookEventName = 'PreToolUse'
        permissionDecision = 'deny'
        permissionDecisionReason = "Blocked high-risk $risk. Use a separately reviewed, explicitly authorized workflow."
    }} | ConvertTo-Json -Compress
}

