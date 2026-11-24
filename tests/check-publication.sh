#!/usr/bin/env bash
# Shared gates for candidate updates, release bundles, and the Pages repository.
set -euo pipefail

tests_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$tests_dir/.."

case ${1:-} in
    source)
        [[ $# == 1 ]]
        check_environment=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/t3-source-checks.XXXXXXXX")
        trap 'rm -rf -- "$check_environment"' EXIT
        python3 -m venv "$check_environment"
        export PATH="$check_environment/bin:$PATH"
        python3 -m pip install --disable-pip-version-check -r scripts/requirements-security.txt
        python3 -m unittest discover -s tests -p 'test_*.py'
        node --test tests/test-preview-files.mjs
        python3 scripts/check-flatpak-security.py
        python3 scripts/check-pages-artifact-contract.py
        for script in startt3code t3code-android-setup t3code-android-diagnostics tests/*.sh; do
            bash -n "$script"
        done
        ;;
    installed)
        if [[ $# != 2 || ${T3_DISPOSABLE_ACCOUNT:-} != 1 ]]; then
            echo 'Installed publication checks require T3_DISPOSABLE_ACCOUNT=1 and a bundle.' >&2
            exit 2
        fi
        bundle=$(realpath -- "$2")
        [[ -f "$bundle" ]]
        bundle_digest=$(sha256sum -- "$bundle")
        bash "$tests_dir/with-flatpak-portal.sh" true
        bash "$tests_dir/check-ssh-boundary.sh" "$bundle"
        # Includes preview, Android, PR-watch, Antigravity, and extension checks.
        bash "$tests_dir/run-tool-extension-check.sh" "$bundle"
        test "$(sha256sum -- "$bundle")" = "$bundle_digest"
        ;;
    *)
        echo "Usage: bash $0 source | installed /path/to/t3code.flatpak" >&2
        exit 2
        ;;
esac
