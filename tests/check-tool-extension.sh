#!/usr/bin/env bash
# Run inside the installed app sandbox, with the fixture installed or absent.
set -euo pipefail

case "${1:-}" in
    installed|absent) export TEST_EXTENSION_STATE="$1" ;;
    *) printf 'Usage: bash %s installed|absent\n' "$0" >&2; exit 2 ;;
esac

# Capture the launcher's environment at the Electron handoff. A fresh child
# process checks the exported PATH seen by terminals and agent subprocesses.
zypak-wrapper() {
    bash -euc '
        if [[ "$TEST_EXTENSION_STATE" == installed ]]; then
            [[ "$(command -v t3-tool-test)" == /app/tools/Test/bin/t3-tool-test ]]
            [[ "$PATH" == *:/app/bin:/app/tools/Test/bin:* ]]
            t3-tool-test
        else
            ! command -v t3-tool-test
            [[ "$PATH" != *"/app/tools/"* ]]
        fi
    '
}

# The launcher accepts unset optional display/environment variables.
set +u
source /app/bin/startt3code
printf 'Tool extension check passed: %s\n' "$TEST_EXTENSION_STATE"
