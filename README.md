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
agent or broad host filesystems. It grants D-Bus access to
`org.freedesktop.Flatpak`, which permits host command spawning through Flatpak.
The host's `~/.ssh`,
`~/.gnupg`, and `~/.local/bin` are mounted read-only for SSH/GPG credentials and
user-installed tools.

Provider CLIs can be installed into app data with the sandbox-local helper below.
Project directory access still requires an explicit per-path override.

## Sandbox runtime tools

The Flatpak bundles sandbox-local `node`, `npm`, `npx`, `corepack`, `git`, OpenSSH client tools, and the `gh`, `glab`, and `tea` hosting CLIs. These are real binaries inside `/app`, not host wrappers. The launcher also includes the read-only host `~/.local/bin` in `PATH`. The manifest does not add `flatpak-spawn`, `--filesystem=home`, `--socket=ssh-auth`, or broad host filesystem access.

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

## Persistent data

Without home directory access, `$HOME` inside the sandbox is a temporary
filesystem that is discarded when T3 Code exits. Only
`~/.var/app/com.t3tools.t3code` and explicitly granted project paths survive.
The package therefore maps every state location T3 Code and its tools use into
that app directory:

| Inside the sandbox | Stored on the host | Contents | Mechanism |
|---|---|---|---|
| `~/.t3` | `~/.var/app/com.t3tools.t3code/.t3` | T3 Code settings, keybindings, threads (`state.sqlite`), login tokens, secrets, worktrees | `--persist=.t3` |
| `~/.codex` | `~/.var/app/com.t3tools.t3code/.codex` | Codex CLI config and auth | `--persist=.codex` |
| `~/.claude` | `~/.var/app/com.t3tools.t3code/.claude` | Claude Code config, including `.claude.json` | `--persist=.claude` + `CLAUDE_CONFIG_DIR` |
| `$XDG_CONFIG_HOME/git/config` | `~/.var/app/com.t3tools.t3code/config/git/config` | `git config --global` settings | launcher creates the file so git prefers it over `~/.gitconfig` |
| `$HISTFILE` | `~/.var/app/com.t3tools.t3code/.local/state/bash/history` | terminal shell history | launcher sets `HISTFILE` |
| `$XDG_CONFIG_HOME`, `$XDG_DATA_HOME`, `$XDG_CACHE_HOME` | `~/.var/app/com.t3tools.t3code/{config,data,cache}` | Electron profile, OpenCode, npm globals and cache | Flatpak default |

The app-specific state is separate from the host's `~/.t3`, `~/.codex`,
`~/.claude`, and `~/.gitconfig`. The host's `~/.ssh`, `~/.gnupg`, and
`~/.local/bin` are intentional exceptions: they are visible read-only inside
the sandbox, and `~/.local/bin` is included in `PATH`. T3 Code and subprocesses
can read credentials and execute tools in these directories, but cannot modify
them through these mounts. If you grant `--filesystem=home`, Flatpak ignores
`--persist` and uses the host home directly.

T3 Code worktrees live under `~/.t3/worktrees` inside the sandbox. Host-side
git sees those worktree paths as missing, so `git worktree prune` on the host
(which `git gc` also runs for entries older than `gc.worktreePruneExpire`,
three months by default) would drop their metadata. Manage those worktrees
from inside T3 Code.

## Project access

T3 Code's private app data lives under `~/.var/app/com.t3tools.t3code` (see
[Persistent data](#persistent-data)). The
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

## Building locally

To build the x86_64 Flatpak bundle locally, install Podman and run the Flathub Flatpak build image from the repository root. The named `t3-flatpak-build` volume keeps Flatpak's SDK, build state, and OSTree repository between runs. The generated bundle is written to `out/t3code.flatpak`.

```bash
mkdir -p out
podman run --rm --privileged \
  -v "$PWD":/src:ro \
  -v "$PWD/out":/out \
  -v t3-flatpak-build:/build \
  -e FLATPAK_USER_DIR=/build/flatpak \
  ghcr.io/flathub-infra/flatpak-github-actions:freedesktop-26.08 \
  sh -euxc '
    flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
    flatpak-builder --user --force-clean --install-deps-from=flathub --disable-rofiles-fuse \
      --state-dir=/build/state --repo=/build/repo \
      /build/builddir /src/com.t3tools.t3code.yml
    flatpak build-bundle /build/repo /out/t3code.flatpak com.t3tools.t3code master \
      --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo
  '
```

The container needs network access to Flathub and the upstream source hosts. `--privileged` is required by the supplied build image for its Flatpak build setup. `--force-clean` recreates the build directory, while cached downloads and the repository remain in the named volume. To start with a fresh cache and build repository, remove that volume with `podman volume rm t3-flatpak-build` after stopping other builds that use it.

## Installation and automatic updates

This repository publishes a signed x86_64 Flatpak repository at:

<https://0cwa.github.io/t3-flatpak/>

Add the remote and install T3 Code with:

```bash
flatpak remote-add --user --if-not-exists t3code https://0cwa.github.io/t3-flatpak/t3code.flatpakrepo
flatpak install --user t3code com.t3tools.t3code
```

After installation, normal Flatpak updates discover new T3 Code releases from
the same signed remote:

```bash
flatpak update --user com.t3tools.t3code
```

The standalone app reference is also available for one-click installation:

```bash
flatpak install --user https://0cwa.github.io/t3-flatpak/t3code.flatpakref
```

This package intentionally supports x86_64 only. The manifest and Pages
workflow publish `app/com.t3tools.t3code/x86_64/master`; no aarch64 ref is
promised.

## GitHub Pages publishing setup

The `Publish Flatpak Repository to GitHub Pages` workflow builds an unsigned
x86_64 OSTree repository, transfers it as a short-lived artifact, and uses a
separate trusted job to validate, sign, and publish only the exact application
ref. It generates `t3code.flatpakrepo`, `t3code.flatpakref`, and an install
index under the Pages site.

Before the first publish:

1. Create a dedicated GPG key for this Flatpak repository. Back up the private
   key securely; users trust its public key from the generated descriptors.
2. Add `FLATPAK_GPG_PRIVATE_KEY` as an ASCII-armored GitHub Actions secret.
3. Add `FLATPAK_GPG_KEY_ID` as the signing key ID or fingerprint secret.
4. Add `GH_TOKEN` as a token authorized to push commits and tags. The updater
   uses it because pushes made with the default `github.token` do not trigger
   downstream CI, release, and Pages workflows. The updater fails before
   changing anything when this secret is absent.
5. Set the public repository variable `PUBLISH_FLATPAK_REPO` to `true` after
   the signing secrets are ready. Automatic publication is restricted to
   `refs/heads/main`; manual dispatch is also restricted to that ref.
6. Optionally set `FLATPAK_REPO_URL` to a custom HTTPS Pages site root without
   the trailing slash or `/repo/`. The workflow appends `/repo/` when it
   generates repository metadata. To keep the documented URL when the GitHub
   repository slug is not `t3-flatpak`, set it to
   `https://0cwa.github.io/t3-flatpak`.
7. In repository Settings → Pages, set the source to **GitHub Actions**. A
   branch/folder Pages source will not deploy this workflow's artifact.

Do not rotate or replace the repository signing key casually. Existing clients
that trusted the generated `.flatpakrepo` may reject future updates after a key
change; plan rotation as a migration while the old key remains available.

The workflow keeps branch-scoped Actions caches for the unsigned build
repository and final signed repository. Cache hits preserve OSTree history and
static-delta inputs; a cache miss is safe because the current x86_64 ref is
rebuilt. The final repository is not pruned, so reachable update history is not
discarded between successful publishes.

## CI and update maintenance

GitHub Actions builds the x86_64 Flatpak bundle on pushes, pull requests, and
manual dispatches. Release tags matching `v*` or `t3code-v*` run a verified
release workflow that checks the manifest uses an immutable upstream AppImage
URL and pinned SHA-256 before publishing bundle artifacts.

The scheduled `Update T3 Code AppImage` workflow checks the latest upstream
GitHub release, downloads the Linux x86_64 AppImage, computes its SHA-256
locally, updates the manifest pin when needed, and creates a matching
`t3code-v...` release tag. With `GH_TOKEN` configured, its `main` push triggers
the downstream build, release, and Pages workflows. The Flatpak build removes
upstream Electron updater metadata from the extracted AppImage so application
updates come from the Flatpak remote, not from T3 Code writing into `/app` at
runtime. Keep `x-checker-data` pointed at `latest-linux.yml`; it is packaging
metadata, not a runtime update channel.

The scheduled `Update Bundled Runtime Pins` workflow keeps the sandbox-local
Node.js, Git, and OpenSSH source pins current independently from T3 Code
AppImage releases. It stays within the currently pinned major versions by
default, validates the updater scripts and launchers, and opens a pull request
instead of publishing directly to `main`. Use its manual `allow_major` input
when you intentionally want to review a major runtime upgrade.

Codeberg/Forgejo Actions support is available in `.forgejo/workflows/build.yml`.
It expects a self-hosted x86_64 runner labeled `flatpak-x86_64` with `flatpak`,
`flatpak-builder`, network access to Flathub and GitHub release assets, and
enough permissions for Flatpak builds. If hosted Codeberg CI is required instead
of a maintained runner, Woodpecker is the practical Codeberg-hosted path.
