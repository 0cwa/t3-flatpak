#!/usr/bin/env bash
# Run on a Flatpak-capable host, under a disposable OS account.
set -euo pipefail

if [[ $# != 1 || ${T3_DISPOSABLE_ACCOUNT:-} != 1 ]]; then
    echo "Usage: T3_DISPOSABLE_ACCOUNT=1 bash $0 /path/to/t3code.flatpak" >&2
    echo 'Use a disposable OS account: a separate installation still shares app data.' >&2
    exit 2
fi
bundle=$(realpath -- "$1")
[[ -f "$bundle" ]]
for tool in flatpak ssh-agent ssh-keygen ssh-add python3; do
    command -v "$tool" >/dev/null
done

test_root=$(mktemp -d)
canaries=()
created_dirs=()
agent_pid=""
cleanup() {
    if [[ -n "$agent_pid" ]]; then kill "$agent_pid" 2>/dev/null || true; fi
    for path in "${canaries[@]}"; do rm -f -- "$path"; done
    for path in "${created_dirs[@]}"; do rmdir -- "$path" 2>/dev/null || true; done
    rm -rf -- "$test_root"
}
trap cleanup EXIT

# Dummy key/tool files, never real credentials. Check all removed host mounts.
for dir in "$HOME/.ssh" "$HOME/.gnupg" "$HOME/.local/bin"; do
    if [[ ! -d "$dir" ]]; then
        mkdir -p -- "$dir"
        created_dirs+=("$dir")
    fi
    canary=$(mktemp "$dir/t3-flatpak-canary.XXXXXXXX")
    printf '%s\n' 'Disposable sandbox boundary test; not a private key.' > "$canary"
    canaries+=("$canary")
done

export FLATPAK_USER_DIR="$test_root/installation"
flatpak remote-add --user --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --noninteractive "$bundle"
app=com.t3tools.t3code

# Inspect the actual installed application's permissions, not just its source.
flatpak info --user --show-permissions "$app" > "$test_root/permissions"
python3 - "$test_root/permissions" <<'PY'
import configparser
import sys
p = configparser.ConfigParser(interpolation=None)
p.read(sys.argv[1])
context = p['Context']
sockets = context.get('sockets', '').split(';')
assert 'ssh-auth' not in sockets, 'SSH agent must be opt-in'
assert '.ssh' in context.get('persistent', '').split(';'), 'App-private SSH state must persist'
filesystems = context.get('filesystems', '').split(';')
for grant in filesystems:
    path = grant.split(':', 1)[0]
    assert path not in ('home', 'host', '~/.ssh', '~/.gnupg', '~/.local/bin'), grant
assert p.get('Session Bus Policy', 'org.freedesktop.Flatpak', fallback='none') == 'none'
print('Installed permission checks passed')
PY

# Start a dedicated agent with only a temporary key. Never reuse the real agent.
mkdir "$test_root/public"
ssh-keygen -q -t ed25519 -N '' -f "$test_root/test-key"
cp "$test_root/test-key.pub" "$test_root/public/test-key.pub"
eval "$(ssh-agent -s -a "$test_root/agent.sock")" >/dev/null
agent_pid=$SSH_AGENT_PID
ssh-add "$test_root/test-key"

# These variables must expand inside the sandbox, not in the host shell.
# shellcheck disable=SC2016
check_boundary='for path do
    if test -r "$path"; then
        echo "Host canary unexpectedly readable: $path" >&2
        exit 1
    fi
done'
flatpak run --user --command=sh "$app" -ec "$check_boundary
    if test -n \"\${SSH_AUTH_SOCK:-}\" && test -S \"\$SSH_AUTH_SOCK\"; then
        echo 'Agent socket unexpectedly available by default' >&2
        exit 1
    fi" sh "${canaries[@]}"

# Only public test material is mounted. ssh-add -T exercises signing through
# the opt-in agent without making an SSH connection or uploading a public key.
flatpak run --user --socket=ssh-auth \
    --env="T3_TEST_PUBLIC_KEY=$test_root/public/test-key.pub" \
    --filesystem="$test_root/public:ro" --command=sh "$app" -ec "$check_boundary
    /app/bin/ssh-add -T \"\$T3_TEST_PUBLIC_KEY\"" sh "${canaries[@]}" "$test_root/test-key"

# A subsequent default run must still have no agent capability.
# shellcheck disable=SC2016
flatpak run --user --command=sh "$app" -ec '
    if test -n "${SSH_AUTH_SOCK:-}" && test -S "$SSH_AUTH_SOCK"; then
        echo "Agent access leaked into a default run" >&2
        exit 1
    fi'
printf '%s\n' 'Default confinement and opt-in SSH-agent signing checks passed.'
