# Task 03: Bundle Core Runtime Tools

You are adding a minimal runtime toolchain inside the T3 Code Flatpak sandbox.

Goal: make the Flatpak able to run basic development/tool installation commands inside its own filesystem without touching the host. Bundle only slow-moving core tools in `/app`; provider CLIs can be installed later into app data.

Target bundled tools:

- Node.js
- npm
- git
- OpenSSH client

Do not bundle in this phase:

- Codex
- Claude
- OpenCode
- Cursor agent
- Grok
- Homebrew

Recommended launcher environment:

```bash
export NPM_CONFIG_PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/npm-global"
export NPM_CONFIG_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/npm"
export PATH="$NPM_CONFIG_PREFIX/bin:/app/bin:$PATH"
```

Also create the npm prefix/cache directories in the launcher before starting T3 Code so provider install commands have a writable target.

Security model:

- Tooling installed through npm must land under Flatpak app data, not the host home.
- Do not mount the host home to make npm, git, or ssh work.
- Do not use `flatpak-spawn`.
- Do not add `--socket=ssh-auth` in this phase.
- Do not rely on T3 Code's login-shell PATH probing to discover host tools. Inside Flatpak, PATH must point at `/app/bin` and app-owned npm globals.
- SSH keys and config remain inside the app sandbox unless a later task documents a deliberate, narrow tradeoff.

Implementation options:

- Prefer Flatpak modules that build or unpack Node/npm, git, and OpenSSH into `/app`.
- Keep module versions/checksums pinned and reproducible.
- If downloading release archives, pin exact URLs and SHA-256 hashes.
- Avoid source builds that add excessive build time unless no sane binary/runtime packaging path exists.
- Keep these tools as real sandbox-local binaries. Do not add `/app/bin/git`, `/app/bin/ssh`, `node`, or `npm` wrappers that call the host.

Context from source review:

- T3 Code starts its own backend by spawning the packaged Electron binary in Node mode. That backend then spawns git, provider CLIs, terminal processes, SSH, and optional Tailscale commands inside the same sandbox.
- Task 03 should make the basic sandbox-local process environment coherent. Provider-specific CLIs still belong to Task 04.

Validation commands:

Run the installed app or a shell in the build and verify:

```bash
node --version
npm --version
git --version
ssh -V
npm config get prefix
npm config get cache
```

Expected locations:

- npm prefix resolves under `~/.var/app/com.t3tools.t3code/data/npm-global`.
- npm cache resolves under `~/.var/app/com.t3tools.t3code/cache/npm`.

Acceptance criteria:

- Flatpak builds successfully.
- T3 Code still launches.
- Node/npm/git/ssh are available inside the sandbox.
- npm global installs write only to app-owned data/cache paths.
- No host filesystem or host command bridge permissions are introduced.
