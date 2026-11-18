# airlock sandbox shell shim.
#
# Sourced INSTEAD OF the user's ~/.zshrc while inside an airlock sandbox.
# A different prompt is the signal: do not source the user's rc by default.
# All state comes from AIRLOCK_* environment variables, never from cwd.

autoload -Uz add-zsh-hook

# Keep mise-managed tool versions on PATH without reading the user's rc.
# mise activate is idempotent for our purposes; the exported guard also
# prevents a nested zsh from stacking its PATH entries again.
if (( $+commands[mise] )) && [[ -z ${_AIRLOCK_MISE_ACTIVE+x} ]]; then
  eval "$(mise activate zsh 2>/dev/null)"
  typeset -gx _AIRLOCK_MISE_ACTIVE=1
fi

# precmd runs on every prompt. Capture the pristine PROMPT once so repeated
# calls rebuild from the same value instead of accumulating segments.
if [[ -z ${_AIRLOCK_BASE_PROMPT+x} ]]; then
  typeset -g _AIRLOCK_BASE_PROMPT="$PROMPT"
fi

function _airlock_is_num() {
  [[ -n "$1" && -z "${1//[0-9]/}" ]]
}

function _airlock_precmd() {
  local sha marker base n=''

  sha=$(command git rev-parse --short HEAD 2>/dev/null)
  [[ -n "$sha" ]] || sha='???????'

  if [[ -n $(command git status --porcelain 2>/dev/null) ]]; then
    marker='*'
  else
    marker=''
  fi

  if [[ -n "$AIRLOCK_BASE_SHA" ]]; then
    # Literal rule: commits in the recorded base not reachable from HEAD.
    n=$(command git rev-list --count "HEAD..$AIRLOCK_BASE_SHA" 2>/dev/null)

    # The recorded base is normally an ancestor of HEAD, so the check above
    # stays at 0. Catch the case that actually matters: the main copy has
    # moved past the base recorded at clone time.
    if { ! _airlock_is_num "$n" || (( n == 0 )); } && [[ -n "$AIRLOCK_MAIN" ]]; then
      n=$(command git -C "$AIRLOCK_MAIN" rev-list --count "$AIRLOCK_BASE_SHA..HEAD" 2>/dev/null)
    fi
    _airlock_is_num "$n" || n=0
  fi

  if (( n > 0 )); then
    base=" base+$n"
  else
    base=''
  fi

  PROMPT="%B%K{196}%F{231} sb:${AIRLOCK_SANDBOX} v${AIRLOCK_VERSION} ${sha}${marker}${base} %k%f%b${_AIRLOCK_BASE_PROMPT}"

  # OSC 2: window title. No OSC 11 (background) and no blinking.
  printf '\033]2;airlock:%s@%s\007' "$AIRLOCK_SANDBOX" "$sha"
}

add-zsh-hook -d precmd _airlock_precmd 2>/dev/null
add-zsh-hook precmd _airlock_precmd
