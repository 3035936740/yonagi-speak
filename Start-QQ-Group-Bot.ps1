param(
    [switch]$NoCooldown = $true,
    [string]$Model,
    [string]$ReasoningEffort,
    [ValidateSet('', 'on', 'off')][string]$FastMode = '',
    [switch]$WebUI
)
$ErrorActionPreference = 'Stop'
$botDir = Join-Path $PSScriptRoot 'group-bot'
$python = Join-Path $PSScriptRoot 'qq-mcp\.venv\Scripts\python.exe'
$settingsPath = Join-Path $botDir 'settings.json'
if (!(Test-Path -LiteralPath (Join-Path $botDir 'debug_console.py'))) { throw 'Debug console installation missing.' }
if ($Model -or $ReasoningEffort -or $FastMode) {
    $current = Get-Content -LiteralPath $settingsPath -Encoding UTF8 -Raw | ConvertFrom-Json
    if (!$Model) { $Model = $current.model }
    if (!$ReasoningEffort) { $ReasoningEffort = $current.reasoning_effort }
    if (!$FastMode) { $FastMode = if ($current.fast_mode) { 'on' } else { 'off' } }
    & $python -X utf8 (Join-Path $botDir 'configure_launch.py') --model $Model --effort $ReasoningEffort --fast $FastMode
    if ($LASTEXITCODE -ne 0) { throw 'Invalid model configuration; nothing started.' }
}
$settings = Get-Content -LiteralPath $settingsPath -Encoding UTF8 -Raw | ConvertFrom-Json
foreach ($group in @($settings.chat_group_whitelist)) {
    $name = if ([int64]$group -eq [int64]$settings.group_id) { 'runtime' } else { 'runtime-' + $group }
    $runtime = Join-Path $botDir $name
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $mode = Join-Path $runtime 'NO_COOLDOWN'
    if ($NoCooldown) { New-Item -ItemType File -Path $mode -Force | Out-Null }
    elseif (Test-Path -LiteralPath $mode) { Remove-Item -LiteralPath $mode }
}
& (Join-Path $PSScriptRoot 'Start-QQ-NapCat.ps1') -Quiet
$consoleArgs = @('-X', 'utf8', (Join-Path $botDir 'debug_console.py'), '--root', $botDir)
if ($WebUI) { $consoleArgs += '--webui' }
& $python @consoleArgs
if ($LASTEXITCODE -ne 0) { throw 'Debug console exited with an error; inspect group runtime logs.' }
