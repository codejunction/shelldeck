# shelldeck shell integration for zsh: load the user's .zshrc, then report prompt/exit/cwd.
ZDOTDIR="$__sd_user_zdotdir"
[ -f "$ZDOTDIR/.zshrc" ] && . "$ZDOTDIR/.zshrc"

__sd_precmd() {
  local ec=$?
  printf '\033]133;D;%s\007\033]633;P;Cwd=%s\007\033]133;A\007' "$ec" "$PWD"
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd __sd_precmd
