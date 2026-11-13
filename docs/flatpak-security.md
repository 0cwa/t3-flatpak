# Flatpak permission boundary

The default application must not grant access to `org.freedesktop.Flatpak`, host
credential directories (`~/.ssh`, `~/.gnupg`), or host executable directories
(`~/.local/bin`). Bundled binaries, app-local npm globals, provider installs,
and optional tool extensions run in the **same app sandbox**. An extension is
not a security isolation boundary; its binaries and libraries inherit the
application's effective filesystem, bus, network, and user override grants.

Keep app state persistent using Flatpak's XDG app directories, and preserve
`--persist=.t3`, `--persist=.pi`, `--persist=.codex`, `--persist=.claude`,
`--persist=.ssh`, and `--persist=.local/bin` if the launcher and application require those paths.
Allow individual projects by explicit per-directory overrides, not `home`
or `host` defaults. Pi's `auth.json` can contain provider tokens; never
replace `--persist=.pi` with a host `--filesystem=~/.pi` mount, including a
read-only mount. User overrides can still weaken the packaged boundary.

For SSH remotes, prefer the documented
[opt-in SSH-agent access](../README.md#optional-ssh-agent-access). It lets all
sandbox processes request authentication with the agent's loaded keys without
mounting private-key files; use the dedicated-key and confirmation precautions
described there. Host credential-directory overrides, including older
`~/.ssh:ro` grants, expose **readable key material to all sandbox processes**.
Read-only is not confidentiality. Revoke those overrides using the instructions
in the same section, or use app-owned credentials when practical.
No automatic host-command bridge is required to run bundled Git, OpenSSH,
Node, or provider CLIs within the sandbox.

Install the pinned PyYAML dependency from `scripts/requirements-security.txt`
in your Python environment, then run `python3 scripts/check-flatpak-security.py`,
`python3 -m unittest discover -s tests -p test_flatpak_security.py`, and
`bash -n startt3code`.
The checker parses YAML before examining permissions, so quoted values, aliases,
flow lists, and folded scalars receive the same checks. It rejects duplicate keys
and malformed permission lists, host filesystem and credential grants, wildcard
access to the Flatpak bus name, and known literal launcher bridge patterns.
App-private persistence such as `--persist=.ssh` does not grant host key access.
The check does not interpret arbitrary shell code or prove runtime isolation.

Server-side interactive tabs and HTML captures use a reduced child Flatpak
sandbox. It exposes the browser installation read-only, one profile read-write,
and (for interactive tabs) a per-browser transfer directory. Uploads copy only
explicitly supplied paths into staging; download copies reject symlinks and
paths outside staging. Provider state and projects are not browser mounts.
The child receives an explicit environment and CDP pipes, with a separate PID
namespace and no display, GPU, bus or agent sockets. Chrome's own renderer
sandbox remains disabled by default. Network access is shared: native browser
compromise can still reach local services, and the selected browser profile's
cookies remain accessible. The HTML renderer retains its existing proxy and
content controls, which are not a native-process network boundary.

The packaging patch checks the exact reviewed server module hash and every
launch/transfer call site. An upstream bundle change stops the build until the
integration is reviewed. Run the installed payload regression through
`agent-heavy -- bash tests/run-preview-browser-check.sh` on a Flatpak host.
It uses a disposable parent with an empty home, instruments only a throwaway
archive to expose test functions, and checks native marker denial, descriptors,
namespace separation, rendering, transfers, persistent profiles, content
restrictions and shutdown. Set `T3_TEST_BROWSER` to an existing pinned Chrome
157 executable to avoid downloading the test browser again. For development
against an unmodified installed 0.1.9 payload, also set
`T3_PREVIEW_APPLY_PATCH=1`. No installed files or user overrides are changed.

Use a disposable OS account or ephemeral CI container for runtime checks:
separate Flatpak installations still share `~/.var/app` state. Additionally
inspect effective metadata and overrides, confirm
that the Flatpak session-bus name is inaccessible, verify that host SSH/GPG
sentinels are unreadable, check permitted project mounts and persisted state,
and exercise an installed and absent tool extension. Account for any
pre-existing user overrides and never use real credentials as test fixtures.
