#!/usr/bin/env bash
# Check launcher defaults without starting Electron or modifying user data.
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")/.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT

export HOME="$test_root/home"
export XDG_CONFIG_HOME="$test_root/config"
export XDG_DATA_HOME="$test_root/data"
export XDG_CACHE_HOME="$test_root/cache"
export XDG_RUNTIME_DIR="$test_root/run"
export FLATPAK_ID=com.t3tools.t3code
export WAYLAND_DISPLAY=""
unset XDG_STATE_HOME HISTFILE CLAUDE_CONFIG_DIR

zypak-wrapper() {
    [[ "$1" == /app/bin/t3code/t3code ]]
    [[ "$NPM_CONFIG_PREFIX" == "$XDG_DATA_HOME/npm-global" ]]
    [[ "$NPM_CONFIG_CACHE" == "$XDG_CACHE_HOME/npm" ]]
    [[ "$CLAUDE_CONFIG_DIR" == "$HOME/.claude" ]]
    [[ "$XDG_STATE_HOME" == "$XDG_DATA_HOME/state" ]]
    [[ "$HISTFILE" == "$XDG_DATA_HOME/state/bash/history" ]]
    [[ "$PATH" == "$NPM_CONFIG_PREFIX/bin:/app/bin:"* ]]
    [[ -d "$(dirname -- "$HISTFILE")" ]]
    [[ -f "$XDG_CONFIG_HOME/git/config" ]]
}

# First run creates persistent state locations; second run keeps user config.
source "$root/startt3code"
printf '%s\n' '# user configuration' > "$XDG_CONFIG_HOME/git/config"
source "$root/startt3code"
grep -Fxq '# user configuration' "$XDG_CONFIG_HOME/git/config"
printf '%s\n' 'Launcher defaults and existing Git config checks passed.'
