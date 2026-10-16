$ErrorActionPreference = 'Stop'
$botDir = 'D:\MCP\qqmcp\group-bot'
$settings = Get-Content -LiteralPath (Join-Path $botDir 'settings.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$groups = @($settings.chat_group_whitelist)
if (!$groups.Count) { $groups = @($settings.group_id) }
foreach ($group in $groups) {
    $groupId = [int64]$group
    $runtimeName = if ($groupId -eq [int64]$settings.group_id) { 'runtime' } else { 'runtime-' + $groupId }
    $runtime = Join-Path $botDir $runtimeName
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $runtime 'STOP') -Force | Out-Null
    Write-Output ('Stop requested for group ' + $groupId)
}
