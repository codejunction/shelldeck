# shelldeck shell integration for bash (used as --rcfile).
# Loads the user's normal startup files, then reports prompt/exit/cwd via OSC sequences.
if [ -n "$SHELLDECK_LOGIN" ]; then
  unset SHELLDECK_LOGIN
  [ -f /etc/profile ] && . /etc/profile
  for __sd_f in ~/.bash_profile ~/.bash_login ~/.profile; do
    if [ -f "$__sd_f" ]; then . "$__sd_f"; break; fi
  done
  unset __sd_f
else
  [ -f ~/.bashrc ] && . ~/.bashrc
fi

__sd_prompt() {
  local ec=$? d=$PWD
  # Git Bash: report /c/x as C:\x so the server can reopen the folder
  if [ -n "$MSYSTEM" ] && [[ $d =~ ^/([a-zA-Z])(/.*)?$ ]]; then
    d="${BASH_REMATCH[1]^^}:${BASH_REMATCH[2]:-/}"
    d=${d//\//\\}
  fi
  printf '\033]133;D;%s\007\033]633;P;Cwd=%s\007' "$ec" "$d"
  return $ec
}
if [[ "$PROMPT_COMMAND" != *__sd_prompt* ]]; then
  PROMPT_COMMAND="__sd_prompt${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
fi
PS1="\[\033]133;A\007\]$PS1\[\033]133;B\007\]"
