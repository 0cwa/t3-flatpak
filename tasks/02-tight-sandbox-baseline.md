# Task 02: Tight Sandbox Baseline

You are hardening the T3 Code Flatpak permissions after the basic AppImage repack works.

Goal: keep T3 Code confined to its Flatpak app filesystem by default. It should not see the user's full home directory and should not be able to spawn host commands.

Required permission posture:

Keep:

```yaml
- --socket=wayland
- --socket=fallback-x11
- --share=ipc
- --share=network
- --env=GTK_USE_PORTAL=1
- --env=T3CODE_DESKTOP_PROTOCOL_REGISTRATION_MANAGED=1
- --env=XCURSOR_PATH=/run/host/user-share/icons:/run/host/share/icons
```

Omit:

```yaml
- --filesystem=home
- --socket=ssh-auth
- --talk-name=org.freedesktop.Flatpak
```

Do not add:

- `flatpak-spawn` wrappers.
- Broad host filesystem grants.
- `--filesystem=host`.
- `--device=all`.
- Global Flatpak overrides.
- `--socket=pulseaudio` unless a future source review finds active audio playback/capture support that needs it.
- Tray/AppMenu D-Bus permissions unless T3 Code adds a tray integration.

Secureblue framing:

This package is intended to work well on secureblue. Do not weaken secureblue defaults to make this app work. In particular, do not ask the user to enable broad unconfined user namespaces, disable SELinux, disable hardened malloc globally, or use host command wrappers.

Implementation notes:

- Keep all app state in Flatpak paths such as `~/.var/app/com.t3tools.t3code`.
- If the app needs a temp dir, use `XDG_RUNTIME_DIR` as the current launcher already does.
- Force portal-backed file picking with `GTK_USE_PORTAL=1`.
- Add `x-scheme-handler/t3code` to the installed desktop file and set `T3CODE_DESKTOP_PROTOCOL_REGISTRATION_MANAGED=1`, so the Flatpak desktop file owns the `t3code:` cloud auth callback instead of the app trying to self-register it.
- If provider CLIs are unavailable, that is acceptable for this phase.
- If project files are unavailable, that is acceptable for this phase.

Source review notes:

- The desktop app uses Electron folder dialogs, external `http`/`https` link opening, clipboard copy, a local backend process, local HTTP/WebSocket ports, a `t3code:` auth callback scheme, optional SSH/Tailscale/provider subprocesses, git, and terminals.
- No current desktop source usage was found for tray icons, AppMenu, notifications, camera, microphone, desktop capture, or active renderer audio playback.
- SSH, git, Tailscale, terminals, and provider runtimes should run inside the sandbox after later tasks bundle/install the needed tools. Do not solve them through host spawning or broad host permissions.

Acceptance criteria:

- `flatpak info --show-permissions com.t3tools.t3code` shows no home filesystem grant.
- Permissions do not include `org.freedesktop.Flatpak` talk access.
- Permissions do not include `pulseaudio`, tray/AppMenu D-Bus names, `ssh-auth`, `host`, or `device=all`.
- The installed desktop file has `MimeType=x-scheme-handler/t3code;`.
- The app still launches.
- Any missing provider/source-control tools are documented as expected until later phases.
