param(
    [switch]$DryRun, [switch]$Quiet, [switch]$WebUI, [switch]$CLI,
    [switch]$SkipNapCat, [switch]$NoCooldown, [switch]$WithCooldown,
    [string]$Model, [string]$ReasoningEffort,
    [ValidateSet('', 'on', 'off')][string]$FastMode = '',
    [ValidateSet('auto', 'docker')][string]$Backend = 'auto',
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$ExtraArgs
)
$entryArgs = @()
if ($DryRun) { $entryArgs += '--dry-run' }
if ($Quiet) { $entryArgs += '--quiet' }
if ($CLI) { $entryArgs += '--cli' }
if ($SkipNapCat) { $entryArgs += '--skip-napcat' }
if ($PSBoundParameters.ContainsKey('NoCooldown')) { $entryArgs += @('--cooldown', $(if ($NoCooldown) { 'off' } else { 'on' })) }
if ($WithCooldown) { $entryArgs += @('--cooldown', 'on') }
if ($Model) { $entryArgs += @('--model', $Model) }
if ($ReasoningEffort) { $entryArgs += @('--effort', $ReasoningEffort) }
if ($FastMode) { $entryArgs += @('--fast', $FastMode) }
if ($Backend -ne 'auto') { $entryArgs += @('--backend', $Backend) }
$entryArgs += $ExtraArgs
& (Join-Path $PSScriptRoot 'scripts/run.ps1') -Entry 'bot' @entryArgs
