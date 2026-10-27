# Flatpak permission boundary

The default application must not grant access to `org.freedesktop.Flatpak`, host
credential directories (`~/.ssh`, `~/.gnupg`), or host executable directories
(`~/.local/bin`). Bundled binaries, app-local npm globals, provider installs,
and optional tool extensions run in the **same app sandbox**. An extension is
not a security isolation boundary; its binaries and libraries inherit the
application's effective filesystem, bus, network, and user override grants.

Keep app state persistent using Flatpak's XDG app directories, and preserve
`--persist=.t3`, `--persist=.codex`, and `--persist=.claude` if the launcher
and application require those paths. Allow individual projects by explicit
per-directory overrides, not `home` or `host` defaults.

Users needing host keys can explicitly accept their exposure, for example via
`flatpak override --user --filesystem=~/.ssh:ro com.t3tools.t3code`.
This shares **readable key material with all sandbox processes**, including
provider CLIs and extension commands. Read-only is not confidentiality.
Prefer app-owned credentials, narrowly selected key files, or another
purpose-built authentication flow when practical. Revoke overrides when done.
No automatic host-command bridge is required to run bundled Git, OpenSSH,
Node, or provider CLIs within the sandbox.

Run `python3 scripts/check-flatpak-security.py` and `bash -n startt3code`.
The checker blocks known dangerous manifest defaults and launcher patterns;
it is deliberately not proof of runtime isolation. On a disposable Flatpak
installation, additionally inspect effective metadata and overrides, confirm
that the Flatpak session-bus name is inaccessible, verify that host SSH/GPG
sentinels are unreadable, check permitted project mounts and persisted state,
and exercise an installed and absent tool extension. Account for any
pre-existing user overrides and never use real credentials as test fixtures.
