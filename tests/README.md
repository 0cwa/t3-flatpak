# Tool extension integration check

The installed check also runs the preview browser regression. It tests both
packaged server browser paths in a disposable parent with an empty home and
no host-command permission, using the exact pinned Chrome and bundled
Playwright. See [the security notes](../docs/flatpak-security.md) for standalone
execution and reuse of an existing browser download.

Run these checks on a host with Flatpak and flatpak-builder under a disposable
OS account or ephemeral CI container. A separate Flatpak installation still
shares `~/.var/app` state; do not use an account with real T3 Code data or
credentials. The initial build downloads the SDK, runtime, and Electron base.

## Publication gates

Candidate updates, GitHub releases, and the Pages repository use the same
source and installed-app checks:

```bash
bash tests/check-publication.sh source
T3_DISPOSABLE_ACCOUNT=1 bash tests/check-publication.sh installed /path/to/t3code.flatpak
```

The source check creates a disposable Python environment, installs the locked
checker dependency, runs all Python fixtures and the Node file-transfer test,
and verifies permissions, artifact handoff, and shell syntax. The installed
check verifies host-portal prerequisites, the sandbox boundary and persisted
state, then all tool-extension, provider, browser, Android, and PR-watch checks.
It must run under the disposable-account requirement above.

Automatic update candidates pass these gates before changing `main` or
creating their release tag. Release and Pages jobs test their own exact bundle
or repository before uploading, publishing, or signing it. Fixture tests also
verify that compatible chunk renames and generated-symbol changes are accepted,
while ambiguous or incompatible integration sites leave the archive unchanged.

## Installed tool checks

CI runs the installed, shared-library, and removed-extension checks after
building the application bundle. To run the same check with an existing bundle:

```bash
T3_DISPOSABLE_ACCOUNT=1 bash tests/run-tool-extension-check.sh /path/to/t3code.flatpak
```

The script creates a temporary user installation and runs the bundled Make,
Node/npm/Corepack, Git, OpenSSH, and hosting CLIs from `/app/bin`. It confirms
that installing the app does not install the fixture, builds the fixture for
`26.08`, verifies command and library loading, and checks the launcher again
after uninstalling the fixture. It removes the temporary installation and build
state on exit; the disposable account's app data may remain. It does not open the GUI or
exercise an agent session.

For manual debugging, run the following commands from the repository root
under the same disposable-account requirement:

```bash
test_root=$(mktemp -d)
export FLATPAK_USER_DIR="$test_root/installation"
flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak-builder --user --force-clean --install-deps-from=flathub \
  --disable-rofiles-fuse --state-dir="$test_root/state" \
  --repo="$test_root/repo" "$test_root/app" com.t3tools.t3code.yml
flatpak remote-add --user --no-gpg-verify local "$test_root/repo"
flatpak install --user -y local com.t3tools.t3code//master

flatpak run --user --filesystem="$PWD/tests:ro" --command=bash \
  com.t3tools.t3code "$PWD/tests/check-tool-extension.sh" absent

flatpak-builder --user --force-clean --disable-rofiles-fuse \
  --state-dir="$test_root/state" --repo="$test_root/repo" \
  "$test_root/extension" tests/extension-fixture/com.t3tools.t3code.Tools.Test.yml
```

```bash
flatpak install --user -y local com.t3tools.t3code.Tools.Test//26.08
flatpak run --user --command=sh com.t3tools.t3code -ec \
  '/app/tools/Test/bin/t3-tool-test; ldconfig -p | grep libt3tooltest'
flatpak run --user --filesystem="$PWD/tests:ro" --command=bash \
  com.t3tools.t3code "$PWD/tests/check-tool-extension.sh" installed

flatpak uninstall --user -y com.t3tools.t3code.Tools.Test//26.08
flatpak run --user --filesystem="$PWD/tests:ro" --command=bash \
  com.t3tools.t3code "$PWD/tests/check-tool-extension.sh" absent
```

The launcher check intercepts the Electron handoff and runs a fresh child
shell. It verifies command lookup, PATH precedence, and library loading without
opening the GUI. The C fixture deliberately has no rpath.

For an end-to-end UI check, install the fixture again, start the app from this
installation, and run `command -v t3-tool-test` and `t3-tool-test` in a terminal
and an agent session. Remove it again and verify the app starts normally.

Inspect app metadata with `flatpak info --user --show-metadata com.t3tools.t3code`.
It should contain the `com.t3tools.t3code.Tools` extension point with branch
`26.08` and `no-autodownload=true`. The app-only installation should contain
no Tools extension. Compare sandbox permissions with the parent revision;
this change does not alter `finish-args`.

Remove the disposable installation and build directory when finished, and
unset `FLATPAK_USER_DIR`. Update the fixture's branch and SDK when changing the
app's Freedesktop runtime version.

## Android setup helper checks

The optional Android bootstrap is covered with local HTTP and archive fixtures;
these checks do not download SDK packages or modify a host SDK. They set
XDG and Android state paths inside the test process and assert their install
roots remain under a per-test scratch directory. GitHub security and Forgejo
build jobs run this suite.

```bash
python3 -m unittest discover -s tests -p 'test_android_setup.py'
```

The fixture tests verify pinned-download failures, safe archive extraction,
explicit license consent, atomic activation and rollback, locking, launcher
state paths, and diagnostics. The installed build check also verifies the
packaged Python runtime and wrappers, runs clean-profile diagnostics, checks
the installed Devices message, and checks Android launcher paths across two
scratch-only invocations. It does not install SDK packages, accept licenses,
create an AVD, or require KVM hardware. The permission check inspects installed
metadata and requires only `dri` and `kvm` device grants.

## Antigravity persistence check

The installed sandbox boundary check also runs
`check-antigravity-persistence.sh`. To run it against an already installed
user Flatpak, use a disposable OS account:

```bash
T3_DISPOSABLE_ACCOUNT=1 bash tests/check-antigravity-persistence.sh com.t3tools.t3code
```

Two separate Flatpak invocations verify that app-private global skills in
`~/.gemini/config/skills` and `~/.gemini/antigravity-cli/skills` survive, while
a synthetic host `~/.gemini` canary stays hidden. Unique fixtures also check
the managed-runtime directory under `~/.t3/tools/antigravity-acp/linux-x64/versions`
and a private provider profile under `~/.t3/userdata/providers/antigravity`.
The second invocation checks runtime read-only modes, profile directory
permissions, and a synthetic token file at mode `600`. The script removes its
fixture children on exit and does not overwrite an active runtime or profile.

This checks filesystem persistence and isolation only. It does not download or
execute Antigravity, sign in, test ACP communication, discover models, or make
a provider request. It must not run against an account with real T3 data.

## Antigravity runtime regression

`bash tests/run-antigravity-runtime-check.sh` tests the actual provider code
from a disposable copy of the installed archive, with networking disabled.
The installer must list and extract a small gzip-compressed archive using the
runtime's `tar`, preserving its binary contents. The profile helper must create distinct private directories, suppress direct
browser launching through the sandbox's Node runtime, and keep selected
credentials separate from ambient Google environment variables. The launch
helper must use `acp --gemini-dir ... --app-data-dir antigravity-acp` and disable
the managed CLI's own updater.

An offline ACP fixture checks the shipped client's subprocess arguments,
working directory, environment, T3 identity, and authentication-required
handling. It never downloads Google code, signs in, or sends a model prompt.
CI runs it alongside the PR-watch and preview checks after the Flatpak build.

For a manual account test, install the PR's bundle under a disposable account,
enable Antigravity, install its managed runtime, and sign in through provider
settings. Confirm models load, run a small task in an explicitly mounted test
project with approvals enabled, restart T3 Code, and check that the account,
thread, and shared skills remain available. This real Google sign-in and model
request are separate from the automated regressions.

## PR watch runtime regression

`bash tests/run-pr-watch-check.sh` exercises the installed PR watcher in a
disposable copy of the application, with networking disabled and deterministic
API responses. A failed comment read must not suppress a successful or failed
check update, end an otherwise readable watch, lose recovered comments, or
repeat delivered notifications after a reactor restart. No GitHub request,
provider session, or live thread is created.

The same check exercises the shipped notification-routing block: PR-watch
updates enter an active goal through existing noninterrupting steering, while
ordinary queued delivery remains in place for idle threads, inactive goals,
stopped runs, and providers that cannot steer safely.

For a compatible installation that already has the preview isolation patch, use
`T3_PR_WATCH_APPLY_PATCH=1 bash tests/run-pr-watch-check.sh` to apply the goal
delivery correction to the disposable copy only, or
`T3_PR_WATCH_APPLY_COMPLETION=1` for the optional-check completion correction.
CI tests the newly built installation without these options. The instrumentation
discovers the active modules and generated identifiers from the actual archive;
it rejects missing or ambiguous contracts rather than requiring a specific
T3 version or chunk filename.
