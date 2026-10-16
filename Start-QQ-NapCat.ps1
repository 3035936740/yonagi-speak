param([switch]$Quiet)
$ErrorActionPreference = 'Stop'
$napcatDir = 'D:\QQNT\NapCat'
$qqExe = 'D:\QQNT\QQ.exe'
if (!(Test-Path -LiteralPath "$napcatDir\NapCatWinBootMain.exe")) { throw 'NapCat installation is missing.' }
if (!(Test-Path -LiteralPath $qqExe)) { throw 'QQ.exe was not found.' }

$config = Get-Content -Raw -LiteralPath "$napcatDir\config\webui.json" | ConvertFrom-Json
$env:NAPCAT_PATCH_PACKAGE = "$napcatDir\qqnt.json"
$env:NAPCAT_LOAD_PATH = "$napcatDir\loadNapCat.js"
$env:NAPCAT_INJECT_PATH = "$napcatDir\NapCatWinBootHook.dll"
$env:NAPCAT_MAIN_PATH = "$napcatDir\napcat.mjs"
$env:NAPCAT_WORKDIR = $napcatDir
$running = $false
try {
    $result = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:6099' -TimeoutSec 2
    $running = $result.StatusCode -eq 200
} catch {}

if (!$running) {
    $logsDir = Join-Path $PSScriptRoot 'runtime'
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
    $bootArgs = @($qqExe, "$napcatDir\NapCatWinBootHook.dll")
    $lastAccount = Get-ChildItem -LiteralPath "$napcatDir\config" -Filter 'onebot11_*.json' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($lastAccount -and $lastAccount.BaseName -match '^onebot11_(\d+)$') {
        $bootArgs += @('-q', $Matches[1])
    }
    $process = Start-Process -FilePath "$napcatDir\NapCatWinBootMain.exe" -ArgumentList $bootArgs -WorkingDirectory $napcatDir -WindowStyle Hidden -RedirectStandardOutput "$logsDir\napcat-launch.out.log" -RedirectStandardError "$logsDir\napcat-launch.err.log" -PassThru
    $process.Id | Set-Content -LiteralPath "$logsDir\napcat-launch.pid"
}
if ($Quiet) { Write-Output 'NapCat dependency started; WebUI: http://127.0.0.1:6099'; return }
Write-Output 'NapCat started. Open the local WebUI and scan the QQ login code.'
Write-Output 'WebUI: http://127.0.0.1:6099'
Write-Output ('WebUI login token: ' + $config.token)
Write-Output ('Open panel: http://127.0.0.1:6099/webui?token=' + [Uri]::EscapeDataString($config.token))

