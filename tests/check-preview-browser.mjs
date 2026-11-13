import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { spawn } from "node:child_process";
import { ServerBrowserContexts, captureHtmlScreenshot, measureHtmlHeights, setInputFiles } from "/app/bin/t3code/resources/app.asar/apps/server/dist/server-bi3w5spO.mjs";
import { previewTestNodeServices } from "/app/bin/t3code/resources/app.asar/apps/server/dist/ManagedEndpointRuntime-B-62Mn5X.mjs";
import { es as runPromise, Uo as provide } from "/app/bin/t3code/resources/app.asar/apps/server/dist/Schema-znhrSbG8.mjs";
import { saveDownload, stageUploads } from "/app/lib/t3-preview/adapter.mjs";

const [root, executable] = process.argv.slice(2);
assert(root && executable);
const results = {};
const watchdog = setTimeout(() => { console.error("Preview regression timed out"); process.exit(1); }, 90_000);
const home = process.env.HOME;
let contexts;
const markerPaths = [".t3/userdata/secrets", ".codex", ".claude", ".ssh", ".var/app/com.t3tools.t3code/data", ".var/app/com.t3tools.t3code/config", ".var/app/com.t3tools.t3code/cache"]
  .map((directory) => path.join(home, directory, "disposable-preview-marker.js"));
markerPaths.push(path.join(root, "project-marker.js"));
let blockedRequests = 0;
const server = http.createServer((request, response) => {
  if (request.url.startsWith("/blocked")) blockedRequests++;
  if (request.url === "/download") {
    response.writeHead(200, { "Content-Disposition": 'attachment; filename="fixture.txt"' });
    response.end("disposable download");
  } else {
    response.end('<!doctype html><title>Preview fixture</title><body>Browser works<input id="upload" type="file"><input id="directory" type="file" webkitdirectory><button onclick="document.querySelector(\'#upload\').click()">Choose</button>');
  }
});
server.on("upgrade", (request, socket) => { blockedRequests++; socket.destroy(); });

function manager() {
  return new ServerBrowserContexts({
    profilesDir: path.join(root, "profiles"), executable: async () => executable,
    env: { ...process.env, T3CODE_SERVER_BROWSER_SANDBOX: "0", T3_PRIVATE_TEST_ENV: "disposable" },
  });
}

async function nativeProbe(mode, signal) {
  const resources = path.join(root, `native-${mode}-${signal ?? "normal"}`);
  const profile = path.join(root, `native-profile-${mode}-${signal ?? "normal"}`);
  await fs.mkdir(resources, { recursive: true });
  await fs.mkdir(profile, { recursive: true });
  const fake = path.join(resources, "chrome-headless-shell");
  const parentNamespace = await fs.readlink("/proc/self/ns/pid");
  await fs.writeFile(path.join(resources, "config.json"), JSON.stringify({ markerPaths, profile, parentPid: process.pid, mode }));
  await fs.writeFile(fake, `#!/app/bin/node
import fs from 'node:fs'; import path from 'node:path'; import {spawn} from 'node:child_process';
const config=JSON.parse(fs.readFileSync(new URL('./config.json',import.meta.url)));
if(config.mode==='lifecycle') {
 process.on('SIGTERM',()=>{});
 const code='process.on("SIGTERM",()=>{});console.log("ready");setInterval(()=>{},1000);setTimeout(()=>process.exit(),10000)';
 spawn(process.execPath,['-e',code],{stdio:'inherit'});setTimeout(()=>process.exit(),10000);
} else {
 const read=p=>{try{fs.readFileSync(p);return true}catch{return false}};
 let readOnly=false;try{fs.writeFileSync(new URL('./forbidden',import.meta.url),'bad')}catch(e){readOnly=e.code==='EROFS'};
 fs.writeFileSync(path.join(config.profile,'positive'),'writable');
 const descriptors=fs.readdirSync('/proc/self/fd').map(fd=>{try{return fs.readlinkSync('/proc/self/fd/'+fd)}catch{return 'closed'}});
 console.log(JSON.stringify({markers:config.markerPaths.map(read),parentProc:read('/proc/'+config.parentPid+'/root'+config.markerPaths[0]),pidNamespace:fs.readlinkSync('/proc/self/ns/pid'),descriptors,env:Object.keys(process.env).sort(),readOnly,profileWritable:read(path.join(config.profile,'positive')),sessionBus:fs.existsSync('/run/flatpak/bus'),agent:fs.existsSync('/run/flatpak/ssh-auth'),gpu:fs.existsSync('/dev/dri')}));
}
`, { mode: 0o700 });
  const credentialFd = await fs.open(markerPaths[0]);
  const child = spawn("/app/lib/t3-preview/launch.mjs", [
    `--t3-browser-executable=${fake}`, `--user-data-dir=${profile}`, "--remote-debugging-pipe", "--no-sandbox",
  ], { env: { ...process.env, T3_PRIVATE_TEST_ENV: "disposable" }, stdio: ["ignore", "pipe", "pipe", "pipe", "pipe", "ignore", "ignore", "ignore", "ignore", credentialFd.fd] });
  await credentialFd.close();
  const closed = new Promise((resolve) => child.on("close", (code, signal) => resolve({ code, signal })));
  let stdout = "";
  let stderr = "";
  child.stderr.on("data", (data) => { stderr += data; });
  if (mode === "lifecycle") {
    await new Promise((resolve, reject) => {
      child.stdout.on("data", (data) => { stdout += data; if (stdout.includes("ready")) resolve(); });
      child.on("exit", () => reject(new Error(`Lifecycle fixture exited before ready: ${stderr}`)));
    });
    child.kill(signal);
    const exit = await closed;
    assert(signal === "SIGKILL" ? exit.signal === "SIGKILL" : exit.code === 143, JSON.stringify({ exit, stderr }));
    return exit;
  }
  child.stdout.on("data", (data) => { stdout += data; });
  const exit = await closed;
  assert.equal(exit.code, 0, stderr);
  const native = JSON.parse(stdout);
  assert(native.markers.every((readable) => !readable));
  assert.equal(native.parentProc, false);
  assert.notEqual(native.pidNamespace, parentNamespace);
  assert(!native.descriptors.some((target) => markerPaths.includes(target)));
  assert.equal(native.sessionBus, false);
  assert.equal(native.agent, false);
  assert.equal(native.gpu, false);
  assert.equal(native.readOnly, true);
  assert.equal(native.profileWritable, true);
  assert.deepEqual(native.env, ["HOME", "LANG", "PATH", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"]);
  return native;
}

try {
  // This parent has an empty home; these are exclusively disposable markers.
  for (const marker of markerPaths) {
    await fs.mkdir(path.dirname(marker), { recursive: true });
    await fs.writeFile(marker, 'console.log("DISPOSABLE_SECRET_EXECUTED")', { flag: "wx", mode: 0o600 });
    assert((await fs.readFile(marker, "utf8")).includes("DISPOSABLE_SECRET"));
  }
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  results.native = await nativeProbe("read");
  results.shutdown = { term: await nativeProbe("lifecycle", "SIGTERM"), kill: await nativeProbe("lifecycle", "SIGKILL") };
  contexts = manager();
  const persistent = await contexts.contextFor("persistent");
  const page = await persistent.newPage();
  await page.goto(origin);
  assert.equal(await page.title(), "Preview fixture");
  await page.evaluate(() => { localStorage.setItem("persist", "yes"); document.cookie = "persist=yes; max-age=3600"; });
  const isolated = await contexts.contextFor("incognito", "isolated-test");
  const other = await isolated.newPage();
  await other.goto(origin);
  assert.equal(await other.evaluate(() => localStorage.getItem("persist")), null);
  assert.equal(await other.evaluate(() => document.cookie), "");
  for (const marker of markerPaths) await assert.rejects(other.goto("file://" + marker), /ERR_FILE_NOT_FOUND|ERR_ACCESS_DENIED/);
  const png = await page.screenshot();
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  const upload = path.join(root, "authorized.txt");
  await fs.writeFile(upload, "disposable upload");
  assert.equal(await setInputFiles(page, { locator: "#upload", paths: [upload] }), true);
  assert.equal(await page.locator("#upload").evaluate(async (input) => input.files[0].text()), "disposable upload");
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByText("Choose", { exact: true }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles(await stageUploads(page, [upload]));
  assert.equal(await page.locator("#upload").evaluate((input) => input.files[0].name), "authorized.txt");
  const directory = path.join(root, "upload-directory");
  await fs.mkdir(directory);
  await fs.writeFile(path.join(directory, "nested.txt"), "nested upload");
  await page.locator("#directory").setInputFiles(await stageUploads(page, [directory]));
  assert.equal(await page.locator("#directory").evaluate(async (input) => input.files[0].text()), "nested upload");
  const downloaded = page.waitForEvent("download");
  await page.evaluate((url) => { const a = document.createElement("a"); a.href = url; a.click(); }, origin + "/download");
  const destination = path.join(root, "saved-download");
  await saveDownload(await downloaded, destination);
  assert.equal(await fs.readFile(destination, "utf8"), "disposable download");
  await contexts.close();
  contexts = manager();
  const restarted = await contexts.contextFor("persistent");
  const restored = await restarted.newPage();
  await restored.goto(origin);
  assert.equal(await restored.evaluate(() => localStorage.getItem("persist")), "yes");
  assert.equal(await restored.evaluate(() => document.cookie), "persist=yes");
  await contexts.close();
  contexts = undefined;
  results.interactive = { rendering: true, screenshot: true, isolatedContext: true, persistence: true, upload: true, directoryUpload: true, chooserUpload: true, download: true, privilegedFileReadsDenied: markerPaths.length };

  const html = `<!doctype html><body><div style="height:180px">HTML preview works</div><script src="file://${markerPaths[0]}"></script><iframe src="file://${markerPaths[0]}"></iframe><img src="${origin}/blocked-image"><script>console.log('rtc='+typeof RTCPeerConnection);fetch('${origin}/blocked-fetch').catch(()=>{});try{new WebSocket('ws://127.0.0.1:${server.address().port}/blocked-ws')}catch{}</script>`;
  const input = { executable, noSandbox: true, html, width: 700, appearance: "dark", urlFragment: "" };
  const capture = await runPromise(provide(captureHtmlScreenshot(input), previewTestNodeServices));
  assert.equal(Buffer.from(capture.png, "base64").subarray(1, 4).toString(), "PNG");
  assert(capture.consoleMessages.some((entry) => entry.text === "rtc=undefined"));
  assert(!capture.consoleMessages.some((entry) => entry.text.includes("DISPOSABLE_SECRET_EXECUTED")));
  assert.equal(blockedRequests, 0);
  const measured = await runPromise(provide(measureHtmlHeights({ ...input, widths: [400, 700] }), previewTestNodeServices));
  assert.equal(measured.length, 2);
  results.html = { rendering: true, width: Buffer.from(capture.png, "base64").readUInt32BE(16), contentHeight: capture.contentHeight, loopbackRequests: blockedRequests, fileScriptDenied: true, webRtcDisabled: true, measurement: true };
  await assert.rejects(runPromise(provide(captureHtmlScreenshot({ ...input, noSandbox: false }), previewTestNodeServices)), (error) => /No usable sandbox|Running as root without --no-sandbox/.test(error.output ?? ""));
  results.failClosed = true;
  results.passed = true;
} catch (error) {
  results.error = error.stack;
  process.exitCode = 1;
} finally {
  await contexts?.close();
  server.closeAllConnections();
  server.close();
  for (const marker of markerPaths) await fs.rm(marker, { force: true });
  clearTimeout(watchdog);
  console.log(JSON.stringify(results, null, 2));
}
