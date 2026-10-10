# shelldeck installer for Windows.
#   irm https://raw.githubusercontent.com/codejunction/shelldeck/main/install.ps1 | iex
# Installs the standalone build (no Python needed) from the GitHub release into %LOCALAPPDATA%\shelldeck:
# versions\<ver>\ + current.txt + the sd shims, with that folder on your PATH. Run it again to update.
#   SHELLDECK_VERSION=0.0.12rc2   a specific release (tag v0.0.12rc2) instead of the latest
#   SHELLDECK_ARCHIVE=<file>      install a local shelldeck-<ver>-windows-x64.zip (its .sha256 next to it is checked)
#   SHELLDECK_ROOT=<dir>          install somewhere else
#   SHELLDECK_NO_MODIFY_PATH=1    leave PATH alone (UV_NO_MODIFY_PATH works too)
#   SHELLDECK_PIP=1 (or -Pip)     the Python package instead: uv (installed if missing) + `uv tool install shelldeck`,
#                                 updated with `uv tool upgrade shelldeck`; SHELLDECK_SOURCE (a wheel path, or
#                                 git+https://...) implies it. With irm | iex, use:  & ([scriptblock]::Create((irm <url>))) -Pip
param([switch]$Pip)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest's progress bar makes downloads many times slower
$repo = 'codejunction/shelldeck'

function Say($text, $color = 'Gray') { Write-Host "  $text" -ForegroundColor $color }

Write-Host ''
Say 'shelldeck installer' Magenta

function Install-Pip {
    $source = if ($env:SHELLDECK_SOURCE) { $env:SHELLDECK_SOURCE } else { 'shelldeck' }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Say 'installing uv (https://docs.astral.sh/uv) ...'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$(Join-Path $HOME '.local\bin');$env:Path"
    }
    $bin = (uv tool dir --bin)  # honours UV_TOOL_BIN_DIR
    Say "installing $source ..."
    uv tool install --force --upgrade --python '>=3.12' $source
    if ($LASTEXITCODE) { throw "uv tool install failed ($LASTEXITCODE)" }
    if (-not $env:UV_NO_MODIFY_PATH) { uv tool update-shell *> $null }
    if (-not ($env:Path -split ';' -contains $bin)) { $env:Path = "$bin;$env:Path" }
}

function Install-Standalone {
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw "no standalone build for $($env:PROCESSOR_ARCHITECTURE) yet; use: `$env:SHELLDECK_PIP=1" }
    $root = if ($env:SHELLDECK_ROOT) { $env:SHELLDECK_ROOT } else { Join-Path $env:LOCALAPPDATA 'shelldeck' }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "shelldeck-install-$PID"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    try {
        if ($env:SHELLDECK_ARCHIVE) {
            $zip = (Resolve-Path $env:SHELLDECK_ARCHIVE).Path
            $name = Split-Path $zip -Leaf
            $sumFile = "$zip.sha256"
            if (-not (Test-Path $sumFile)) { $sumFile = $null }
        } else {
            $tag = if ($env:SHELLDECK_VERSION) { 'v' + $env:SHELLDECK_VERSION.TrimStart('v') }
                   else { (Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'shelldeck-installer' }).tag_name }
            $name = "shelldeck-$($tag.TrimStart('v'))-windows-x64.zip"
            $url = "https://github.com/$repo/releases/download/$tag/$name"
            Say "downloading $name ..."
            $zip = Join-Path $tmp $name
            $sumFile = "$zip.sha256"
            Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
            Invoke-WebRequest "$url.sha256" -OutFile $sumFile -UseBasicParsing
        }
        if ($name -notmatch '^shelldeck-(.+)-windows-x64\.zip$') { throw "unexpected archive name: $name" }
        $ver = $Matches[1]
        if ($sumFile) {
            $want = ((Get-Content $sumFile -Raw).Trim() -split '\s+')[0].ToLower()
            $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
            if ($want -ne $got) { throw "checksum mismatch for $name (expected $want, got $got)" }
            Say 'checksum ok'
        }
        $dest = Join-Path $root "versions\$ver"
        if (Test-Path (Join-Path $dest 'sd.exe')) {
            Say "$ver is already unpacked"
        } else {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $out = Join-Path $tmp 'out'
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, $out)
            New-Item -ItemType Directory -Force (Join-Path $root 'versions') | Out-Null
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }  # a half-unpacked earlier try
            Move-Item (Join-Path $out "shelldeck-$ver") $dest
        }
        [IO.File]::WriteAllText((Join-Path $root 'current.txt'), $ver)  # no newline: the sh shim reads it with cat
        # the shims, as shelldeck/frozen.py writes them (keep in sync): run versions\<current.txt>\sd
        [IO.File]::WriteAllText((Join-Path $root 'sd.cmd'), "@echo off`r`nsetlocal`r`nset /p v=<`"%~dp0current.txt`"`r`n`"%~dp0versions\%v%\sd.exe`" %*`r`n")
        [IO.File]::WriteAllText((Join-Path $root 'sd'), "#!/bin/sh`nd=`$(dirname `"`$0`")`nexec `"`$d/versions/`$(cat `"`$d/current.txt`")/sd`" `"`$@`"`n")
        & (Join-Path $dest 'sd.exe') --help *> $null
        if ($LASTEXITCODE) { throw "the unpacked sd.exe did not run ($LASTEXITCODE)" }
        if (-not ($env:SHELLDECK_NO_MODIFY_PATH -or $env:UV_NO_MODIFY_PATH)) {
            $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
            if (-not (($userPath -split ';') -contains $root)) {
                [Environment]::SetEnvironmentVariable('Path', ($(if ($userPath) { "$root;$userPath" } else { $root })), 'User')
                Say "added $root to your PATH"
            }
        }
        if (-not (($env:Path -split ';') -contains $root)) { $env:Path = "$root;$env:Path" }
        Say "installed shelldeck $ver in $root"
        if ((Get-Command uv -ErrorAction SilentlyContinue) -and ((uv tool list 2>$null) -match '^shelldeck ')) {
            Say 'an older uv install of shelldeck is still there; remove it with: sd stop; uv tool uninstall shelldeck' Yellow
        }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

if ($Pip -or $env:SHELLDECK_PIP -or $env:SHELLDECK_SOURCE) { Install-Pip } else { Install-Standalone }

Write-Host ''
Say 'shelldeck is installed.' Green
Say 'Run  sd  (or  shelldeck) to start it and open your browser. Already running? sd restart'
Say 'Open a new terminal if the command is not found.'
Write-Host ''
