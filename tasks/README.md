# T3 Code Flatpak Task Handoffs

These prompts split the Filen-to-T3-Code Flatpak adaptation into scoped phases. Each task should be runnable independently by a future agent, but the intended order is:

1. `01-rebrand-repack.md`
2. `01.5-codeberg-forgejo-actions.md`
3. `02-tight-sandbox-baseline.md`
4. `03-bundle-core-runtime.md`
5. `04-provider-install-flow.md`
6. `05-selective-project-mounts.md`

Project direction:

- Package `https://github.com/pingdotgg/t3code` instead of `https://github.com/FilenCloudDienste/filen-desktop`.
- Prefer a sandboxed Flatpak with its own filesystem.
- Do not use `flatpak-spawn` host wrappers.
- Do not grant `--filesystem=home` by default.
- Do not add `--talk-name=org.freedesktop.Flatpak`.
- Add project directory access later through explicit per-path Flatpak overrides.

Current implementation notes:

- T3 Code app id: `com.t3tools.t3code`.
- Manifest: `com.t3tools.t3code.yml`.
- Launcher: `startt3code`.
- Linux executable inside the AppImage: `t3code`.
- Desktop file inside the AppImage: `t3code.desktop`.
- Icon name inside the AppImage: `t3code.png`.
- The manifest is x86_64-only unless upstream starts publishing a Linux aarch64 AppImage.
- The GitHub Actions build, release, and upstream-update workflows build only x86_64 until a Linux aarch64 AppImage exists.
- The Codeberg Forgejo Actions workflow uses a self-hosted `flatpak-x86_64` runner with Flatpak tooling, direct `flatpak-builder` commands, and x86_64 only until a Linux aarch64 AppImage exists.
- The app's in-app electron-updater flow is not the Flatpak update mechanism. Keep `x-checker-data` for packaging automation through `latest-linux.yml`.
- T3 Code's backend, git, SSH, terminals, Tailscale, and provider integrations run as subprocesses inside the sandbox. Support them by bundling or installing sandbox-local tools, not by spawning host commands.
- Persistent project access is documented through explicit one-directory `flatpak override --user --filesystem=/path/to/project:rw com.t3tools.t3code` grants rather than broad manifest permissions.
- GitHub Pages publishing is handled by `.github/workflows/pages-flatpak-repo.yml`. It builds a signed x86_64 OSTree repo at `repo/`, generates `t3code.flatpakrepo` and `t3code.flatpakref` from templates, and deploys the Pages artifact with the official Pages actions. The default generated URL is `https://OWNER.github.io/REPOSITORY/t3code.flatpakrepo`, with `https://OWNER.github.io/t3code.flatpakrepo` for `OWNER.github.io` repositories. Set the public repository variable `FLATPAK_REPO_URL` if Pages uses a custom domain or nonstandard base URL. Automatic publishing requires `PUBLISH_FLATPAK_REPO=true` plus `FLATPAK_GPG_PRIVATE_KEY` and `FLATPAK_GPG_KEY_ID` secrets.
- Remaining implementation work starts after `05-selective-project-mounts.md`; future maintenance should add independent repinning automation for Node.js, Git, and OpenSSH without changing sandbox permissions.

Known upstream details from source/AppImage review:

- Linux executable inside the AppImage: `t3code`.
- Desktop file inside the AppImage: `t3code.desktop`.
- Icon name inside the AppImage: `t3code.png`.
- Latest verified release during this handoff was `v0.0.27` on 2026-06-18, but always verify the latest release before updating checksums or URLs.
