# shelldeck: zsh reads startup files from $ZDOTDIR; restore the user's and chain to theirs.
__sd_zdotdir="${ZDOTDIR}"
ZDOTDIR="${SHELLDECK_USER_ZDOTDIR:-$HOME}"
[ -f "$ZDOTDIR/.zshenv" ] && . "$ZDOTDIR/.zshenv"
__sd_user_zdotdir="$ZDOTDIR"
ZDOTDIR="$__sd_zdotdir"
