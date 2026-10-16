$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
New-Item -ItemType File -Path (Join-Path $root 'group-bot\CONSOLE_STOP') -Force | Out-Null
& (Join-Path $root 'Stop-QQ-Group-Bot.ps1')
$deadline = (Get-Date).AddSeconds(45)
do {
    $bots = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and ($_.CommandLine -like ('*' + $root + '\group-bot\bot.py*') -or $_.CommandLine -like ('*' + $root + '\group-bot\debug_console.py*')) })
    if (!$bots.Count) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if ($bots.Count) { throw 'Bots have not stopped; NapCat was left running for safe shutdown.' }
$processes = @(Get-CimInstance Win32_Process)
$napcat = @($processes | Where-Object { $_.Name -eq 'NapCatWinBootMain.exe' -and $_.CommandLine -like '*D:\QQNT\NapCat\NapCatWinBootHook.dll*' -and $_.CommandLine -like '*-q 169901502*' })
$owned = New-Object 'System.Collections.Generic.HashSet[int]'
foreach ($process in $napcat) { [void]$owned.Add([int]$process.ProcessId) }
do {
    $added = $false
    foreach ($process in $processes) {
        if ($owned.Contains([int]$process.ParentProcessId) -and !$owned.Contains([int]$process.ProcessId)) {
            [void]$owned.Add([int]$process.ProcessId); $added = $true
        }
    }
} while ($added)
foreach ($process in $processes) {
    $mcpOrTest = $process.Name -in @('python.exe','pythonw.exe') -and $process.CommandLine -like ('*' + $root + '\qq-mcp\*') -and ($process.CommandLine -like '*-m napcat_mcp*' -or $process.CommandLine -like '*faulthandler*unittest*')
    if ($owned.Contains([int]$process.ProcessId) -or $mcpOrTest) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output ('Stopped QQ project process: ' + $process.Name + ' ' + $process.ProcessId)
    }
}
Write-Output 'QQ bot, its NapCat/QQ session, and QQ MCP have been stopped. Codex desktop remains running.'
