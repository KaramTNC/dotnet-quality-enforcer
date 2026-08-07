$ErrorActionPreference = 'SilentlyContinue'
$raw = [Console]::In.ReadToEnd()
try { $payload = if ($raw) { $raw | ConvertFrom-Json } else { $null } } catch { $payload = $null }
$root = if ($payload.cwd) { [IO.Path]::GetFullPath([string]$payload.cwd) } else { (Get-Location).Path }
$diffCheck = @(git -C $root diff --check 2>&1 | Where-Object { $_ -notmatch '^warning:' })
$status = @(git -C $root status --short 2>&1)
$secretFiles = @($status | Where-Object { $_ -match '\.env|\.pem|\.key|id_rsa' })
$issues = [System.Collections.Generic.List[string]]::new()
if ($diffCheck.Count -gt 0) { $issues.Add('whitespace errors reported by git diff --check') }
if ($secretFiles.Count -gt 0) { $issues.Add('possible secret-bearing file appears in worktree status') }
if ($issues.Count -gt 0) { [pscustomobject]@{ systemMessage = "Turn validation found: $($issues -join '; ')" } | ConvertTo-Json -Compress }

