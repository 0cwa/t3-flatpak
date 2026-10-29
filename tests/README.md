# Tool extension integration check

Run these checks on a host with Flatpak and flatpak-builder under a disposable
OS account or ephemeral CI container. A separate Flatpak installation still
shares `~/.var/app` state; do not use an account with real T3 Code data or
credentials. The initial build downloads the SDK, runtime, and Electron base.

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
