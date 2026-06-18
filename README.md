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

## CI

GitHub Actions builds the x86_64 Flatpak bundle on pushes, pull requests, and
manual dispatches. Release tags matching `v*` or `t3code-v*` run a verified
release workflow that checks the manifest uses an immutable upstream AppImage URL
and pinned SHA-256 before publishing bundle artifacts.

The scheduled `Update T3 Code AppImage` workflow checks the latest upstream
GitHub release, updates the manifest pin when a new Linux x86_64 AppImage is
published, and creates a matching `t3code-v...` release tag.

Codeberg/Forgejo Actions support is available in `.forgejo/workflows/build.yml`.
It expects a self-hosted x86_64 runner labeled `flatpak-x86_64` with `flatpak`,
`flatpak-builder`, network access to Flathub and GitHub release assets, and
enough permissions for Flatpak builds. If hosted Codeberg CI is required instead
of a maintained runner, Woodpecker is the practical Codeberg-hosted path.
