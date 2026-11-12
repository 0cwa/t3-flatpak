import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import { spawn } from "node:child_process";

const [executable, ...args] = process.argv.slice(1);
fs.mkdirSync("/tmp/run", { mode: 0o700, recursive: true });
const lifetime = new net.Socket({ fd: 5, readable: true, writable: false });
let browser;
let stopping = false;
const kill = (signal) => {
  if (browser?.pid) {
    try { process.kill(-browser.pid, signal); } catch (error) { if (error.code !== "ESRCH") throw error; }
  }
};
const stop = () => {
  if (stopping) return;
  stopping = true;
  kill("SIGTERM");
  setTimeout(() => { kill("SIGKILL"); process.exit(143); }, 1000).unref();
};
lifetime.on("end", stop).on("error", stop).resume();
process.on("SIGTERM", stop).on("SIGINT", stop).on("SIGHUP", stop);
browser = spawn(executable, args, { detached: true, stdio: [0, 1, 2, 3, 4] });
browser.on("error", (error) => { console.error(`Preview isolation: ${error.message}`); process.exit(1); });
browser.on("exit", (code, signal) => {
  kill("SIGKILL");
  process.exit(code ?? 128 + (os.constants.signals[signal] ?? 1));
});
