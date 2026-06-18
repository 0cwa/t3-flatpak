# Task 01: Rebrand And Repack T3 Code AppImage

You are adapting this repository from a Filen Flatpak repack to a T3 Code Flatpak repack.

Goal: produce a working Flatpak manifest that repackages the upstream T3 Code AppImage and launches the GUI. Keep the scope to app identity, AppImage source, extraction paths, wrapper name, metadata, and build commands. Do not add runtime tooling, npm, provider installers, host wrappers, or project mounts in this task.

Current repository files of interest:

- `com.t3tools.t3code.yml`
- `com.t3tools.t3code.metainfo.xml`
- `startt3code`
- `Justfile`
- `README.md`
- `.github/workflows/build.yml`

Required direction:

- New app id: `com.t3tools.t3code`
- New manifest name: `com.t3tools.t3code.yml`
- New metainfo name: `com.t3tools.t3code.metainfo.xml`
- New launcher name: `startt3code`
- Flatpak command: `startt3code`
- Installed app payload path: `/app/bin/t3code`
- Electron executable path inside payload: `/app/bin/t3code/t3code`

Upstream facts to verify before editing:

- Latest T3 Code release on GitHub.
- Current Linux AppImage asset name and SHA-256.
- Whether Linux `aarch64` AppImage exists. If no Linux arm64 AppImage exists, keep the manifest x86_64-only.
- Current `latest-linux.yml` updater metadata URL.

Known values from initial research, to be rechecked:

- `https://github.com/pingdotgg/t3code/releases/download/v0.0.27/T3-Code-0.0.27-x86_64.AppImage`
- SHA-256: `00b926ef04956c395947b4d655a8373516cfd64bc6250aa6a51d66999bd20805`
- Updater metadata: `https://github.com/pingdotgg/t3code/releases/latest/download/latest-linux.yml`
- AppImage internal desktop file: `squashfs-root/t3code.desktop`
- AppImage internal executable: `squashfs-root/t3code`
- AppImage icon files: `squashfs-root/usr/share/icons/hicolor/*/apps/t3code.png`
- Icon sizes include `16 22 24 32 48 64 128 256 512`.

Verification notes from the first implementation pass:

- GitHub latest release was `v0.0.27`.
- No Linux aarch64 AppImage was present in that release.
- The x86_64 AppImage SHA-256 matched `00b926ef04956c395947b4d655a8373516cfd64bc6250aa6a51d66999bd20805`.
- `latest-linux.yml` pointed at `T3-Code-0.0.27-x86_64.AppImage`.
- The AppImage extracted with `t3code.desktop`, executable `t3code`, and icon sizes `16 22 24 32 48 64 128 256 512`.
- `flatpak-builder` was unavailable in the local sandbox and on the host, so full build verification must run in CI or another environment with `flatpak-builder`.

Implementation notes:

- Keep `org.electronjs.Electron2.BaseApp`.
- Keep Wayland-friendly Electron flags from `startfilen`, but rename the script and update paths.
- Update desktop file `Exec` to `startt3code %U`.
- Update desktop file icon to `$FLATPAK_ID`.
- Install icons as `com.t3tools.t3code.png`.
- Install desktop file as `/app/share/applications/com.t3tools.t3code.desktop`.
- Install metainfo as `/app/share/metainfo/com.t3tools.t3code.metainfo.xml`.
- Move `squashfs-root` to `/app/bin/t3code`.
- Update `Justfile` to build `com.t3tools.t3code.yml` and bundle `t3code.flatpak`.
- Update GitHub Actions to use `com.t3tools.t3code.yml`, output `t3code.flatpak`, and build x86_64 only unless upstream publishes a Linux aarch64 AppImage.
- Keep `x-checker-data` pointed at `https://github.com/pingdotgg/t3code/releases/latest/download/latest-linux.yml` for package update automation.
- Do not rely on the upstream in-app electron-updater to modify `/app`; Flatpak updates should happen through the package/bundle.

Out of scope:

- No `flatpak-spawn`.
- No `/app/bin/codex`, `/app/bin/claude`, `/app/bin/opencode`, `/app/bin/git`, or similar wrappers.
- No Node/npm bundling.
- No provider install buttons.
- No `--filesystem=home`.
- No `--socket=ssh-auth`.
- No `--talk-name=org.freedesktop.Flatpak`.

Acceptance criteria:

- `flatpak-builder --force-clean --install-deps-from=flathub --repo=repo builddir com.t3tools.t3code.yml` completes.
- `flatpak build-bundle repo t3code.flatpak com.t3tools.t3code --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo` completes.
- Manifest does not reference Filen.
- Launcher does not reference Filen.
- Metadata does not reference Filen.
- The app launches at least to its GUI without requiring host filesystem access.
