# Task 05: Selective Project Mount Documentation

You are documenting the safe workflow for giving T3 Code access to project files.

Goal: keep the default Flatpak sandbox closed, then let users grant one project directory at a time only when needed.

Context:

- Task 02 enables portal-backed folder picking for UI ergonomics.
- Long-lived project work should still be documented through explicit Flatpak filesystem overrides so T3 Code's backend, terminal sessions, git, SSH, and provider CLIs all see the same path consistently.

Do not add broad permissions to the manifest:

```yaml
- --filesystem=home
- --filesystem=host
```

Recommended per-project grant:

```bash
flatpak override --user --filesystem=/path/to/project:rw com.t3tools.t3code
```

Recommended revoke command:

```bash
flatpak override --user --nofilesystem=/path/to/project com.t3tools.t3code
```

Inspection:

```bash
flatpak info --show-permissions com.t3tools.t3code
flatpak override --user --show com.t3tools.t3code
```

Secureblue framing:

This fixes project access by relaxing filesystem isolation. Security cost: T3 Code and any provider CLI it runs can read and write the mounted project path. Scope: one Flatpak app and one explicit path. Revert with the `--nofilesystem` command above.

Documentation requirements:

- Explain where app-private data lives: `~/.var/app/com.t3tools.t3code`.
- Explain that provider CLIs installed inside the sandbox can only see mounted paths.
- Explain that SSH auth is not available by default.
- If SSH remotes are needed later, document it as a separate tradeoff rather than enabling it by default.
- Explain that mounting a project is persistent until revoked.
- Explain that `--filesystem=home` and `--filesystem=host` are intentionally not recommended.
- Explain that mounted project paths are exposed to all tools T3 Code runs inside the sandbox, including terminals and provider CLIs.
- Mention that users can keep separate project grants narrow by adding only the repositories they actively use.

Acceptance criteria:

- README documents adding and revoking one project mount.
- README warns that mounted projects are accessible to T3 Code and its provider CLIs.
- Manifest remains free of broad home/host filesystem access.
- README distinguishes temporary portal file picking from persistent project access through explicit overrides.
