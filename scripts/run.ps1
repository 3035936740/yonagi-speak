param([string]$Entry, [Parameter(ValueFromRemainingArguments=$true)][string[]]$EntryArgs)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$isWin = $env:OS -eq 'Windows_NT'
$python = $null
$candidates = @($env:QQ_BOT_PYTHON, (Join-Path $projectRoot '.venv/Scripts/python.exe'), (Join-Path $projectRoot '.venv/bin/python'), (Join-Path $projectRoot 'qq-mcp/.venv/Scripts/python.exe'), (Join-Path $projectRoot 'qq-mcp/.venv/bin/python'))
foreach ($candidate in $candidates) { if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { $python = $candidate; break } }
if (!$python) { throw 'Run Install-Environment first (or set QQ_BOT_PYTHON to your Python 3.10+ environment).' }
& $python -X utf8 (Join-Path $PSScriptRoot 'manage.py') $Entry @EntryArgs
exit $LASTEXITCODE
