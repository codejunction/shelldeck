# shelldeck shell integration for fish: report prompt/exit/cwd via OSC sequences.
function __sd_prompt --on-event fish_prompt
    set -l ec $status
    printf '\e]133;D;%s\a\e]633;P;Cwd=%s\a\e]133;A\a' $ec $PWD
end
