# T3 Code Flatpak

[T3 Code](https://github.com/pingdotgg/t3code) repackaged as a Flatpak from the upstream AppImage.

It is still technically the upstream AppImage payload, installed into a Flatpak wrapper for immutable and atomic systems. This repository is based on the packaging pattern used by the [Vesktop Flathub repo](https://github.com/flathub/dev.vencord.Vesktop).

## Wayland

T3 Code runs natively on Wayland when launched from a Wayland session. X11 is still
available as a fallback on non-Wayland sessions through Flatpak's `fallback-x11`
socket.

## Sandbox

The default sandbox grants display, IPC, network, portal-based file picking, and
cursor theme access. It does not grant access to the full home directory, SSH
agent, host command spawning, or broad host filesystems.

Provider CLIs can be installed into app data with the sandbox-local helper below.
Project directory access still requires an explicit per-path override.

## Sandbox runtime tools

The Flatpak bundles sandbox-local `node`, `npm`, `npx`, `corepack`, `git`, and OpenSSH client tools. These are real binaries inside `/app`, not host wrappers. The manifest does not add `flatpak-spawn`, `--filesystem=home`, `--socket=ssh-auth`, or broad host filesystem access to make them work.

The launcher configures npm globals and cache under T3 Code app data:

```bash
NPM_CONFIG_PREFIX=${XDG_DATA_HOME:-$HOME/.local/share}/npm-global
NPM_CONFIG_CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/npm
PATH=$NPM_CONFIG_PREFIX/bin:/app/bin:$PATH
```

When installed normally, those resolve under `~/.var/app/com.t3tools.t3code/data/npm-global` and `~/.var/app/com.t3tools.t3code/cache/npm`.

## Provider CLIs

Install supported provider CLIs from inside the Flatpak sandbox:

```bash
flatpak run --command=t3code-install-provider com.t3tools.t3code codex
flatpak run --command=t3code-install-provider com.t3tools.t3code claude
flatpak run --command=t3code-install-provider com.t3tools.t3code opencode
```

The helper only accepts `codex`, `claude`, or `opencode`, and installs the
mapped npm package into the same app-owned npm prefix used by the launcher.
Grok and Cursor agent installs are intentionally not supported by this helper.

Installing a provider downloads executable code into the T3 Code Flatpak
sandbox. That code can access T3 Code app data and any project paths explicitly
mounted into the Flatpak later. It does not gain broad host filesystem access
unless you grant it.

## Project access

T3 Code's private app data lives under `~/.var/app/com.t3tools.t3code`. The
default Flatpak sandbox does not grant access to your repositories.

For persistent access to one project directory, add a narrow user override:

```bash
flatpak override --user --filesystem=/path/to/project:rw com.t3tools.t3code
```

To revoke that project later:

```bash
flatpak override --user --nofilesystem=/path/to/project com.t3tools.t3code
```

Inspect active permissions with:

```bash
flatpak info --show-permissions com.t3tools.t3code
flatpak override --user --show com.t3tools.t3code
```

Mounted project paths are visible to T3 Code and any sandbox-local tools it runs,
including terminals and provider CLIs. Broad `home` or `host` filesystem grants
are intentionally not recommended.

## GitHub Pages Flatpak repo

The `Publish Flatpak Repo to GitHub Pages` workflow builds a signed OSTree
Flatpak repository under `repo/` and publishes it with generated
`t3code.flatpakrepo` and `t3code.flatpakref` files. GitHub Pages must be
configured to use GitHub Actions as its source.

Maintainer setup before the first publish:

1. Create a dedicated GPG signing key for this Flatpak repository.
2. Add the ASCII-armored private key as the `FLATPAK_GPG_PRIVATE_KEY` GitHub
   Actions secret.
3. Add the signing key id as the `FLATPAK_GPG_KEY_ID` GitHub Actions secret.
4. Set the public repository variable `PUBLISH_FLATPAK_REPO` to `true` when the
   signing secrets are ready and automatic publishes from `main` should begin.
5. If the repository uses a custom Pages domain or another published base URL,
   set the public repository variable `FLATPAK_REPO_URL` to that base URL
   without `/repo` at the end.

Use a dedicated repository signing key whose private key is usable
non-interactively by CI, and keep a backup. Losing or rotating the key affects
users who have already trusted the generated `.flatpakrepo`.

The default install path is:

```text
https://OWNER.github.io/REPOSITORY/t3code.flatpakrepo
```

For a user or organization Pages repository named `OWNER.github.io`, the workflow
uses:

```text
https://OWNER.github.io/t3code.flatpakrepo
```

Users can add the remote and install with:

```bash
flatpak remote-add --user --if-not-exists t3code https://OWNER.github.io/REPOSITORY/t3code.flatpakrepo
flatpak install --user t3code com.t3tools.t3code
flatpak update --user com.t3tools.t3code
```

Alternatively, install the generated app ref directly:

```bash
flatpak install --user https://OWNER.github.io/REPOSITORY/t3code.flatpakref
```

Replace the URL with the Pages URL printed by the deploy job. The release bundle
workflow remains available for producing standalone `.flatpak` release assets.

## CI

GitHub Actions builds the x86_64 Flatpak bundle on pushes, pull requests, and
manual dispatches. Release tags matching `v*` or `t3code-v*` run a verified
release workflow that checks the manifest uses an immutable upstream AppImage URL
and pinned SHA-256 before publishing bundle artifacts.

The GitHub Pages workflow builds the same x86_64 manifest into a signed OSTree
repo for Flatpak remote installs and `flatpak update`. It restores the previous
repo from GitHub Actions cache when available so static deltas and old objects can
be retained across publishes. Publishing requires the `FLATPAK_GPG_PRIVATE_KEY`
and `FLATPAK_GPG_KEY_ID` repository secrets.

The scheduled `Update T3 Code AppImage` workflow checks the latest upstream
GitHub release, updates the manifest pin when a new Linux x86_64 AppImage is
published, and creates a matching `t3code-v...` release tag.

Codeberg/Forgejo Actions support is available in `.forgejo/workflows/build.yml`.
It expects a self-hosted x86_64 runner labeled `flatpak-x86_64` with `flatpak`,
`flatpak-builder`, network access to Flathub and GitHub release assets, and
enough permissions for Flatpak builds. If hosted Codeberg CI is required instead
of a maintained runner, Woodpecker is the practical Codeberg-hosted path.
