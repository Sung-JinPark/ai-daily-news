# Stop hook: auto-commit + push any pending changes under the app directories.
#
# This used to be an inline `powershell -NoProfile -Command "...$s=...; if ($s) {...}"`
# one-liner in .claude/settings.json. Claude Code on this machine invokes
# hook commands through a POSIX shell (git-bash), and bash expands any
# $-prefixed name inside a double-quoted string BEFORE PowerShell ever
# sees it — since $s/$c aren't bash variables, they silently became empty,
# turning `$s = & git status ...` into `= & git status ...` and
# `if ($s) { ... }` into `if () { ... }`. Every Stop-hook run therefore hit
# a PowerShell parser error (visible in chat as garbled, codepage-mangled
# Korean error text), and the hook never actually committed anything.
# Moving the logic into a real .ps1 file sidesteps the shell entirely —
# only the file path is quoted in settings.json, and it contains no `$`.

Set-Location "C:\workspace\ai-daily-news"

$status = & git status --porcelain 2>&1
if (-not $status) {
    exit 0
}

& git add site/ pipeline/ README.md CLAUDE.md AGENTS.md .mcp.json .claude/ .github/ 2>&1 | Out-Null

$staged = & git diff --cached --name-only 2>&1

# Leak gate (fail closed). This repo is PUBLIC and this hook pushes without
# review, so a private path that slips into the index would be irreversible.
# The risk grew when .gitignore was narrowed from `.claude/skills/` to just the
# unpublished research skill: new private material is no longer denied by
# default. Unstage the offenders and skip the commit entirely so the anomaly
# is visible instead of being auto-pushed.
$leak = @($staged | Where-Object {
    $_ -match '^data/(research_private|papers_private)/' -or $_ -match 'bodies\.jsonl$'
})
if ($leak.Count -gt 0) {
    & git restore --staged -- $leak 2>&1 | Out-Null
    Write-Output "LEAK GATE: auto-commit aborted. Private paths were staged: $($leak -join ', ')"
    exit 0
}

if ($staged) {
    & git commit -m "auto: apply changes" 2>&1 | Out-Null
    & git push 2>&1 | Out-Null
}

exit 0
