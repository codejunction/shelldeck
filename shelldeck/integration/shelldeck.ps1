# shelldeck shell integration for pwsh / Windows PowerShell.
# Wraps the user's prompt (after their profile ran) to report, via OSC sequences:
#   633;E;<cmd>  last command line      133;D;<code>  command finished
#   633;P;Cwd=   current directory       133;A / 133;B  prompt start / end
if ($global:__sdLoaded) { return }
$global:__sdLoaded = $true
$global:__sdOrigPrompt = $function:prompt
$global:__sdLastId = -1
$global:__sdPrevCode = $global:LASTEXITCODE

function global:prompt {
    $ok = $?
    $code = $global:LASTEXITCODE
    $e = [char]27
    $b = [char]7
    $out = ""
    $h = Get-History -Count 1
    if ($global:__sdLastId -eq -1) {
        # first prompt: history holds this integration script (-EncodedCommand), not a user command
        $global:__sdLastId = if ($h) { $h.Id } else { 0 }
    } elseif ($h -and $h.Id -ne $global:__sdLastId) {
        $global:__sdLastId = $h.Id
        # cmdlet errors leave $LASTEXITCODE stale, so only trust it when the command changed it
        $ec = if ($ok) { 0 } elseif ($code -and $code -ne $global:__sdPrevCode) { $code } else { 1 }
        $cmd = $h.CommandLine -replace '[\x00-\x1f]', ' '
        $out += "$e]633;E;$cmd$b$e]133;D;$ec$b"
    }
    $loc = Get-Location
    if ($loc.Provider.Name -eq 'FileSystem') { $out += "$e]633;P;Cwd=$($loc.ProviderPath)$b" }
    $out += "$e]133;A$b"
    $global:__sdPrevCode = $code
    $p = & $global:__sdOrigPrompt
    $global:LASTEXITCODE = $code
    "$out$p$e]133;B$b"
}
