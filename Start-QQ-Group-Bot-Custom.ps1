param(
    [string]$Model,
    [string]$ReasoningEffort,
    [ValidateSet('', 'on', 'off')][string]$FastMode = ''
)
$ErrorActionPreference = 'Stop'
$current = Get-Content -LiteralPath "$PSScriptRoot\group-bot\settings.json" -Encoding UTF8 -Raw | ConvertFrom-Json
if (!$Model) {
    $Model = Read-Host "Model [$($current.model)]"
    if (!$Model) { $Model = $current.model }
}
if (!$ReasoningEffort) {
    $ReasoningEffort = Read-Host "Reasoning effort: low/medium/high/xhigh/max [$($current.reasoning_effort)]"
    if (!$ReasoningEffort) { $ReasoningEffort = $current.reasoning_effort }
}
if (!$FastMode) {
    $defaultFast = if ($current.fast_mode) { 'on' } else { 'off' }
    $answer = Read-Host "Fast: on/off [$defaultFast]"
    if (!$answer) { $FastMode = $defaultFast }
    elseif ($answer -in @('on', 'off')) { $FastMode = $answer }
    else { throw 'Fast must be on or off; configuration was not changed.' }
}
& "$PSScriptRoot\Start-QQ-Group-Bot.ps1" -Model $Model -ReasoningEffort $ReasoningEffort -FastMode $FastMode
