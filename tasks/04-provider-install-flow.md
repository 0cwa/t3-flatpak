# Task 04: Provider Install Flow For Codex, Claude, And OpenCode

You are adding or documenting a sandbox-local provider install flow for T3 Code.

Goal: make Codex, Claude, and OpenCode installable into the Flatpak app data using npm, without host access. The app should use the same app-owned npm prefix configured in the launcher.

Providers in scope:

- Codex: `@openai/codex`
- Claude: `@anthropic-ai/claude-code`
- OpenCode: `opencode-ai`

Providers out of scope:

- Grok
- Cursor agent

Current upstream behavior:

- T3 Code probes configured binaries.
- T3 Code can run one-click update commands for some already-installed providers.
- T3 Code does not currently appear to bootstrap/download missing provider runtimes into an app-owned tools directory.
- Provider detection depends on the sandbox `PATH`; Task 03 should put `$NPM_CONFIG_PREFIX/bin` before `/app/bin` in `startt3code`.
- Upstream provider sessions are spawned by T3 Code's sandbox-local backend, so installed provider binaries must be runnable from inside the Flatpak.

Existing update commands from upstream:

```bash
npm install -g @openai/codex@latest
npm install -g @anthropic-ai/claude-code@latest
npm install -g opencode-ai@latest
```

Possible implementation paths:

1. Documentation-only first pass:
   - Add README instructions for running these installs inside the Flatpak sandbox.
   - Make clear that they install into app data, not the host.

2. Wrapper command first pass:
   - Add an app-owned helper script such as `/app/bin/t3code-install-provider`.
   - It accepts only `codex`, `claude`, or `opencode`.
   - It runs the corresponding pinned npm package install.
   - It must not accept arbitrary package names.
   - It must set or inherit `NPM_CONFIG_PREFIX`, `NPM_CONFIG_CACHE`, and `PATH` exactly like the launcher.

3. T3 Code patch:
   - Patch upstream UI/backend to show explicit install actions when a provider is missing.
   - Use the same restricted install mapping.
   - Avoid silent startup installs.

Recommended first implementation:

- Do not auto-install on startup.
- Prefer an explicit install action or documented command.
- Keep npm installs inside `$NPM_CONFIG_PREFIX`.
- Consider pinning major versions or recording installed versions to make rollback easier.
- Avoid `npm install -g <user input>`. Use a hard-coded provider-to-package mapping.
- Provider updates can use the same hard-coded mapping. They should not use host package managers.

Security statement to include in docs:

Installing a provider downloads executable code into the T3 Code Flatpak sandbox. That code can access T3 Code app data and any project paths explicitly mounted into the Flatpak later. It does not gain broad host filesystem access unless the user grants it.

Expected user-visible command shape:

```bash
flatpak run --command=t3code-install-provider com.t3tools.t3code codex
flatpak run --command=t3code-install-provider com.t3tools.t3code claude
flatpak run --command=t3code-install-provider com.t3tools.t3code opencode
```

Exact helper naming can change, but the command must run inside the Flatpak sandbox and write to the app-owned npm prefix.

Acceptance criteria:

- Codex, Claude, and OpenCode can be installed into the sandbox app data.
- T3 Code provider detection can find installed provider binaries through `PATH`.
- No host filesystem grants are added.
- No host command bridge is added.
- Missing Grok and Cursor support is intentionally documented.
- Provider install/update commands fail closed for unknown provider names.
