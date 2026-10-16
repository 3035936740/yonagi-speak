param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$uvDir = Join-Path $projectRoot 'tools/uv'
$uv = Join-Path $uvDir 'uv.exe'
if ($env:OS -ne 'Windows_NT') { $uv = Join-Path $uvDir 'uv' }
if ($DryRun) { Write-Output 'Plan: install uv locally; managed Python 3.12; .venv; requirements.txt; optional qq-mcp; initialize missing settings only.'; exit 0 }
if (!(Test-Path -LiteralPath $uv)) {
  New-Item -ItemType Directory -Path $uvDir -Force | Out-Null
  $env:UV_INSTALL_DIR = $uvDir
  $env:UV_NO_MODIFY_PATH = '1'
  if ($env:OS -eq 'Windows_NT') {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $installer = Join-Path $uvDir 'uv-install.ps1'
    Invoke-WebRequest -UseBasicParsing 'https://astral.sh/uv/install.ps1' -OutFile $installer
    & $installer
  } else {
    $installer = Join-Path $uvDir 'uv-install.sh'
    Invoke-WebRequest 'https://astral.sh/uv/install.sh' -OutFile $installer
    & sh $installer
  }
  if (!(Test-Path -LiteralPath $uv)) { throw 'uv installation failed.' }
}
& $uv python install 3.12
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
$venv = Join-Path $projectRoot '.venv'
if (!(Test-Path -LiteralPath $venv)) {
  & $uv venv --python 3.12 --seed $venv
  if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
}
$python = Join-Path $venv 'Scripts/python.exe'
if ($env:OS -ne 'Windows_NT') { $python = Join-Path $venv 'bin/python' }
& $uv pip install --python $python -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Third-party dependency installation failed.' }
if (Test-Path -LiteralPath (Join-Path $projectRoot 'qq-mcp/pyproject.toml')) {
  & $uv pip install --python $python -e (Join-Path $projectRoot 'qq-mcp')
  if ($LASTEXITCODE -ne 0) { throw 'Optional QQ MCP dependency installation failed.' }
}
& $uv pip check --python $python
if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
& $python -X utf8 (Join-Path $projectRoot 'scripts/manage.py') init
if ($LASTEXITCODE -ne 0) { throw 'Configuration initialization failed.' }
Write-Output 'Environment ready. Configure group-bot/settings.json and NapCat connection before starting.'
