# shelldeck installer for Windows.
#   irm https://raw.githubusercontent.com/codejunction/shelldeck/main/install.ps1 | iex
# Installs uv if missing, then shelldeck as a uv tool (uv fetches Python 3.12+ itself if needed).
# SHELLDECK_SOURCE overrides what gets installed (a wheel path, or git+https://github.com/codejunction/shelldeck).

$ErrorActionPreference = 'Stop'
$source = if ($env:SHELLDECK_SOURCE) { $env:SHELLDECK_SOURCE } else { 'shelldeck' }
$bin = Join-Path $HOME '.local\bin'

function Say($text, $color = 'Gray') { Write-Host "  $text" -ForegroundColor $color }

Write-Host ''
Say 'shelldeck installer' Magenta

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say 'installing uv (https://docs.astral.sh/uv) ...'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$bin;$env:Path"
}

Say "installing $source ..."
uv tool install --force --python '>=3.12' $source
if ($LASTEXITCODE) { throw "uv tool install failed ($LASTEXITCODE)" }
if (-not $env:UV_NO_MODIFY_PATH) { uv tool update-shell *> $null }
if (-not ($env:Path -split ';' -contains $bin)) { $env:Path = "$bin;$env:Path" }

Write-Host ''
Say 'shelldeck is installed.' Green
Say 'Run  sd  (or  shelldeck) to start it and open your browser.'
Say 'Open a new terminal if the command is not found.'
Write-Host ''
