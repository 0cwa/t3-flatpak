# SSH boundary runtime checks

Run this check on a Flatpak-capable host under a disposable OS account or
an ephemeral CI container. `flatpak`, Python 3, and OpenSSH tools are required.
Build or download the bundle from the branch under test first.

```bash
T3_DISPOSABLE_ACCOUNT=1 bash tests/check-ssh-boundary.sh /path/to/t3code.flatpak
```

The script installs the bundle into a temporary user installation. This alone
does not isolate app data: Flatpak installations can share `~/.var/app` state.
Do not run it under an account containing real T3 Code data or credentials.

The check:

- Inspects installed permissions for host directory, host-command, and default
  SSH-agent grants, and checks that app-private `.ssh` and `.pi` persist.
- Creates dummy canary files in the test account's `.ssh`, `.gnupg`, and
  `.local/bin` directories. They contain no credentials.
- Starts a dedicated temporary agent with a generated test key, leaving any
  existing agent untouched.
- Verifies that a default sandbox cannot read the host canaries or access the
  agent socket.
- Enables `--socket=ssh-auth` for one run and uses `ssh-add -T` to request a test
  signature. Only the public key is mounted; the private-key file and host
  canaries must remain inaccessible.
- Runs the default sandbox again to check that the temporary socket grant did
  not persist.
- Invokes `check-pi-persistence.sh` to write synthetic settings, an **artificial**
  token, and session data in `~/.pi/agent`; a separate sandbox invocation
  reads them back. The host's Pi canary must remain unreadable, and the data
  must be backed under the application's private `~/.var/app` directory.

The EXIT trap stops the temporary agent and removes canaries and temporary
installation files. The disposable account's app data may remain. No server
connection, real private key, or hosting-account configuration is needed.

This exercises installed filesystem, persisted Pi mounts, and agent boundaries.
It does not launch the actual Pi agent or establish GUI session restoration.
It does not prove
that all sandbox interfaces are secure or that agent access is harmless:
subprocesses can request authentication with any key available in the agent.
