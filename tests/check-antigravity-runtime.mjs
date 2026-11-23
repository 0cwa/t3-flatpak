// Run actual bundled provider functions without downloading Google code or signing in.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { t, services, runPromise, provide } from "./antigravity-test-bindings.mjs";

const [root] = process.argv.slice(2);
assert(root && path.isAbsolute(root));
const home = path.join(root, "home");
assert.equal(process.env.HOME, home, "The check must use its disposable home");
const stateDir = path.join(home, ".t3", "userdata");
const project = path.join(root, "project with spaces");
await fs.mkdir(project, { recursive: true });
const watchdog = setTimeout(() => { console.error("Antigravity regression timed out"); process.exit(1); }, 45_000);
const run = (effect) => runPromise(provide(effect, services));
const personal = { authMethod: "oauth-personal", apiKey: "", gcpProject: "", gcpLocation: "" };
const ambientCredentials = {
  GEMINI_API_KEY: "synthetic-ambient-gemini-key",
  Google_Api_Key: "synthetic-case-insensitive-key",
  GOOGLE_APPLICATION_CREDENTIALS: path.join(root, "unselected-credentials.json"),
  GOOGLE_CLOUD_PROJECT: "unselected-project", GOOGLE_CLOUD_LOCATION: "unselected-location",
  GOOGLE_CLOUD_QUOTA_PROJECT: "unselected-quota", GOOGLE_GENAI_USE_VERTEXAI: "true",
  GCLOUD_PROJECT: "unselected-project", CLOUDSDK_CORE_PROJECT: "unselected-project",
  AGY_ACP_CCPA_PROJECT: "unselected-project", AGY_ACP_ENABLE_OAUTH: "0",
  CHROME_CRASHPAD_PIPE_NAME: "unselected-pipe",
};
const baseEnv = {
  HOME: home, PATH: process.env.PATH,
  ...ambientCredentials,
  GEMINI_HOME: path.join(root, "unselected-profile"),
  AGY_ACP_FORCE_FILE_STORAGE: "0", AGY_CLI_DISABLE_AUTO_UPDATE: "0",
  BROWSER: "do-not-run-an-inherited-browser", PYTHONUNBUFFERED: "0",
  ELECTRON_RUN_AS_NODE: "0", TMPDIR: path.join(root, "unselected-temp"),
};

try {
  for (const version of [undefined, null, "", "invalid", "1.4.5", "1.5.0", "1.5.15"]) {
    const identity = t.antigravityClientInfo(version);
    assert.equal(identity.name, "t3-code", `Client identity must remain honest for ${version}`);
    assert.equal(identity.title, "T3 Code");
    assert(identity.version);
  }

  const directories = await Promise.all(["Antigravity", "antigravity"].map(
    (instance) => run(t.resolveAntigravityInstanceDirectories(stateDir, instance)),
  ));
  assert.notEqual(directories[0].profile, directories[1].profile, "Provider instances must not share credentials");
  assert.notEqual(directories[0].runtimeTemp, directories[1].runtimeTemp);
  assert.deepEqual(await run(t.resolveAntigravityInstanceDirectories(stateDir, "Antigravity")), directories[0]);
  const profiles = [];
  for (const directory of directories) {
    assert.equal(path.dirname(directory.profile), path.join(stateDir, "providers", "antigravity"));
    assert.match(path.basename(directory.profile), /^[a-f0-9]{64}$/);
    assert.equal(path.dirname(directory.runtimeTemp), path.join(stateDir, "antigravity-tmp"));
    const profile = await run(t.prepareAntigravityProfile({
      profileDirectory: directory.profile, tempDirectory: directory.runtimeTemp,
      platform: "linux", userHome: home, runtimeExecutablePath: process.execPath,
      baseEnv, auth: personal,
    }));
    assert.equal(profile.geminiHome, directory.profile);
    assert.equal(profile.tempDirectory, directory.runtimeTemp);
    assert.equal(profile.tokenPath, path.join(directory.profile, "antigravity-acp", "acp_token.json"));
    for (const location of [profile.geminiHome, profile.acpDirectory, profile.tempDirectory]) {
      assert.equal((await fs.stat(location)).mode & 0o777, 0o700);
    }
    const settings = JSON.parse(await fs.readFile(path.join(profile.acpDirectory, "settings.json"), "utf8"));
    assert.deepEqual(settings, { auth: { type: "oauth-personal" } });
    await assert.rejects(fs.stat(profile.tokenPath), { code: "ENOENT" });
    assert(profile.browserCommand.includes(process.execPath), "Browser hook must use the sandbox's runtime");
    assert(profile.browserCommand.includes("__T3_ANTIGRAVITY_AUTH_URL__"));
    assert(profile.caBundlePath && (await fs.stat(profile.caBundlePath)).isFile());
    profiles.push(profile);
  }

  const binary = path.join(home, ".t3", "tools", "antigravity-acp", "linux-x64", "fixture", "agy");
  const capture = path.join(root, "fixture.jsonl");
  const makeSpawn = (profile, auth = personal, extra = {}) => t.buildAntigravityAcpSpawnInput({
    installation: { executablePath: binary }, profile, cwd: project,
    baseEnv: { ...baseEnv, T3_ANTIGRAVITY_TEST_CAPTURE: capture }, auth, ...extra,
  });
  const spawn = makeSpawn(profiles[0]);
  assert.equal(spawn.command, binary);
  assert.deepEqual(spawn.args, ["acp", "--gemini-dir", profiles[0].geminiHome, "--app-data-dir", "antigravity-acp"]);
  assert.equal(spawn.cwd, project);
  assert.equal(spawn.extendEnv, false, "Ambient credentials must not be merged back at spawn");
  for (const key of Object.keys(ambientCredentials)) assert.equal(spawn.env[key], undefined, key);
  assert.equal(spawn.env.GEMINI_HOME, profiles[0].geminiHome);
  assert.equal(spawn.env.TMPDIR, profiles[0].tempDirectory);
  assert.equal(spawn.env.AGY_ACP_FORCE_FILE_STORAGE, "1");
  assert.equal(spawn.env.AGY_CLI_DISABLE_AUTO_UPDATE, "1");
  assert.equal(spawn.env.ELECTRON_RUN_AS_NODE, "1");
  assert.equal(spawn.env.BROWSER, profiles[0].browserCommand);
  assert.equal(spawn.env.SSL_CERT_FILE, profiles[0].caBundlePath);
  assert.equal(makeSpawn(profiles[1]).env.GEMINI_HOME, profiles[1].geminiHome);
  assert.equal(makeSpawn(profiles[1]).env.TMPDIR, profiles[1].tempDirectory);
  const turnTemp = path.join(profiles[0].tempDirectory, "turn");
  assert.equal(makeSpawn(profiles[0], personal, { runtimeTempDirectory: turnTemp }).env.TMPDIR, turnTemp);
  const apiAuth = { ...personal, authMethod: "gemini-api-key", apiKey: "synthetic-selected-key" };
  const apiSpawn = makeSpawn(profiles[0], apiAuth);
  assert.equal(apiSpawn.env.GEMINI_API_KEY, apiAuth.apiKey);
  assert.equal(apiSpawn.env.GOOGLE_API_KEY, undefined);
  assert.match(t.antigravityAuthConfigIssue({ ...apiAuth, apiKey: "" }), /Enter a Gemini API key/);

  // This fixture speaks only enough ACP to reject authentication. The actual
  // bundled client launches it and must propagate that failure without models.
  await fs.mkdir(path.dirname(binary), { recursive: true });
  await fs.writeFile(binary, `#!${process.execPath}
const fs = require("node:fs");
const log = value => fs.appendFileSync(process.env.T3_ANTIGRAVITY_TEST_CAPTURE, JSON.stringify(value) + "\\n");
log({ argv: process.argv.slice(2), cwd: process.cwd(), env: process.env });
require("node:readline").createInterface({ input: process.stdin }).on("line", line => {
  const request = JSON.parse(line); log({ request });
  if (request.id === undefined) return;
  const response = request.method === "initialize" ? { result: {
    protocolVersion: 1, agentInfo: { name: "Antigravity ACP Server", version: "1.5.15" },
    agentCapabilities: { loadSession: true, sessionCapabilities: { resume: {} }, auth: { logout: {} } },
    authMethods: [{ id: "oauth-personal", name: "Log in with Google" }]
  } } : { error: { code: -32000, message: "Authentication required" } };
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, ...response }) + "\\n");
});
`, { mode: 0o700 });
  await run(t.scoped(t.gen(function* () {
    const runtime = yield* t.makeAntigravityAcpRuntime({
      spawn, cwd: project, runtimeVersion: "1.5.15",
      childProcessSpawner: yield* t.ChildProcessSpawner,
    });
    const initialized = yield* runtime.initialize();
    assert.equal(initialized.agentInfo.version, "1.5.15");
    const result = yield* runtime.start().pipe(
      t.map((value) => ({ value })), t.catch((error) => t.succeed({ error })),
    );
    assert(result.error, "A runtime requiring sign-in must not create a usable session");
    assert.equal(t.isAntigravitySignInRequiredError(result.error), true);
    assert.deepEqual(yield* runtime.getConfigOptions, []);
  })));
  const records = (await fs.readFile(capture, "utf8")).trim().split("\n").map(JSON.parse);
  assert.deepEqual(records[0].argv, spawn.args);
  assert.equal(records[0].cwd, project);
  assert.equal(records[0].env.GEMINI_HOME, profiles[0].geminiHome);
  assert.equal(records[0].env.TMPDIR, profiles[0].tempDirectory);
  for (const key of Object.keys(ambientCredentials)) assert.equal(records[0].env[key], undefined, key);
  const requests = records.flatMap((record) => record.request ? [record.request] : []);
  assert.equal(requests[0].params.clientInfo.name, "t3-code");
  assert(requests.some((request) => request.method === "authenticate"));
  assert(!requests.some((request) => request.method === "session/new"));
  console.log("Antigravity passed: private profiles, browser hook, modern CLI launch, honest identity, and required sign-in.");
} finally {
  clearTimeout(watchdog);
}
