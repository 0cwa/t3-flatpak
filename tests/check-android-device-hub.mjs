// Run against the installed expo-device-hub, with a fake adb client.
import assert from "node:assert/strict";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const hub = pathToFileURL(process.argv[2]);
const routerModule = new URL("../../vendor/serve-emu/dist/middleware.js", hub);
if (process.env.T3_TEST_APPLY_ANDROID_HUB_PATCH === "1") {
  const { patchDeviceHub } = await import("../android/device-hub.mjs");
  patchDeviceHub(hub);
}
const { createRouter } = await import(routerModule);

const scratch = await mkdtemp(join(process.env.T3_TEST_SCRATCH, "android-hub."));
const adb = join(scratch, "adb");
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfxcAAAAASUVORK5CYII=",
  "base64",
);
await writeFile(adb, `#!${process.execPath}
const expected = ["-s", "emulator-5556", "exec-out", "screencap", "-p"];
if (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(expected)) process.exit(1);
process.stdout.write(Buffer.from("${png.toString("base64")}", "base64"));
`);
await chmod(adb, 0o755);
process.env.PATH = scratch;
delete process.env.EXPO_DEVICE_HUB_SCREENSHOT_DIRECTORY;

let starts = 0;
const token = "android-test-session";
const router = createRouter({ sessionToken: token }, {
  listDevices: async () => [{ serial: "emulator-5556" }, { serial: "emulator-5560" }],
  createApp: async () => {
    starts += 1;
    throw new Error("emulator video stream unavailable");
  },
});
const request = (method, device, headers = { Authorization: `Bearer ${token}` }) =>
  router.handleRequest(new Request(`http://localhost/api/screenshot?device=${device}`, {
    method, headers,
  }));

try {
  assert.equal((await request("POST", "emulator-5556", {})).status, 401);
  assert.equal((await request("POST", "emulator-5556", {
    Authorization: `Bearer ${token}`, Origin: "https://example.invalid",
  })).status, 403);
  assert.equal((await request("GET", "emulator-5556")).status, 405);
  assert.equal((await request("POST", "emulator-5598")).status, 503);

  const response = await request("POST", "emulator-5556");
  assert.equal(response.status, 200, "An unavailable stream must not prevent a screenshot");
  assert.equal(response.headers.get("content-type"), "image/png");
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), png);
  assert.equal(starts, 0, "Taking a screenshot must not start streaming");

  const rejected = await request("POST", "emulator-5560");
  assert.equal(rejected.status, 400, "Capture errors must remain errors");
  await router.stopAll();
  assert.equal((await request("POST", "emulator-5556")).status, 503);
  console.log("Android screenshot passed independently of video startup, with scope and failure guards.");
} finally {
  await router.stopAll();
  await rm(scratch, { recursive: true, force: true });
}
