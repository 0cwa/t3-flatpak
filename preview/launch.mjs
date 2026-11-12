#!/app/bin/node
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { exposedPath } from "./paths.mjs";

function takeOption(args, name, required = true) {
  const matches = args.filter((arg) => arg.startsWith(name + "="));
  if (matches.length > 1 || (required && matches.length !== 1)) throw new Error(`Expected one ${name}.`);
  const value = matches[0]?.slice(name.length + 1);
  if (value !== undefined && !path.isAbsolute(value)) throw new Error(`${name} must be an absolute path.`);
  return value;
}

function exitCode(code, signal) {
  return code ?? 128 + (os.constants.signals[signal] ?? 1);
}

async function parent(args) {
  const executable = takeOption(args, "--t3-browser-executable");
  const profile = takeOption(args, "--user-data-dir");
  const transfer = takeOption(args, "--t3-transfer-dir", false);
  if (path.basename(executable) !== "chrome-headless-shell") throw new Error("Unsupported preview executable.");
  if (!args.includes("--remote-debugging-pipe") || args.some((arg) => arg.startsWith("--remote-debugging-port"))) {
    throw new Error("Preview isolation requires CDP pipes.");
  }
  if (transfer && !path.basename(transfer).startsWith("t3-preview-transfer-")) throw new Error("Invalid transfer directory.");
  await fsp.mkdir(profile, { recursive: true, mode: 0o700 });
  const visibleExecutable = exposedPath(executable);
  const visibleProfile = exposedPath(profile);
  const exposed = [path.dirname(visibleExecutable), visibleProfile, ...(transfer ? [exposedPath(transfer)] : [])];
  for (let a = 0; a < exposed.length; a++) {
    for (let b = a + 1; b < exposed.length; b++) {
      const contains = (parent, filename) => {
        const relative = path.relative(parent, filename);
        return relative === "" || (relative !== ".." && !relative.startsWith(".." + path.sep) && !path.isAbsolute(relative));
      };
      if (contains(exposed[a], exposed[b]) || contains(exposed[b], exposed[a])) {
        throw new Error("Preview resource, profile and transfer directories must not overlap.");
      }
    }
  }
  if (args.some((arg) => arg.startsWith("--t3-") && !arg.startsWith("--t3-browser-executable=") && !arg.startsWith("--t3-transfer-dir="))) {
    throw new Error("Unsupported preview isolation option.");
  }
  const chromeArgs = args.filter((arg) => !arg.startsWith("--t3-") && !arg.startsWith("--user-data-dir="));
  chromeArgs.push(`--user-data-dir=${visibleProfile}`);
  const command = [
    "--sandbox", "--clear-env", "--watch-bus", "--directory=/",
    `--sandbox-expose-path-ro=${path.dirname(visibleExecutable)}`,
    `--sandbox-expose-path=${visibleProfile}`,
    ...(transfer ? [`--sandbox-expose-path=${exposedPath(transfer)}`] : []),
    "--forward-fd=3", "--forward-fd=4", "--forward-fd=5",
    "/usr/bin/env", "-i", "PATH=/app/bin:/usr/bin", "HOME=/tmp/home", "LANG=C.UTF-8", "TMPDIR=/tmp",
    "XDG_CONFIG_HOME=/tmp/config", "XDG_CACHE_HOME=/tmp/cache", "XDG_DATA_HOME=/tmp/data", "XDG_RUNTIME_DIR=/tmp/run",
    "/app/bin/node", "--input-type=module", "--eval", fs.readFileSync(new URL("./supervisor.mjs", import.meta.url), "utf8"),
    "--", visibleExecutable, ...chromeArgs,
  ];
  const sandbox = spawn("/usr/bin/flatpak-spawn", command, { stdio: [0, 1, 2, 3, 4, "pipe"] });
  let timer;
  const stop = () => {
    sandbox.stdio[5].destroy();
    timer ??= setTimeout(() => sandbox.kill("SIGKILL"), 2000);
  };
  process.on("SIGTERM", stop).on("SIGINT", stop).on("SIGHUP", stop);
  sandbox.on("error", (error) => { console.error(`Preview isolation: ${error.message}`); process.exitCode = 1; });
  sandbox.on("exit", async (code, signal) => {
    clearTimeout(timer);
    if (transfer) await fsp.rm(transfer, { recursive: true, force: true });
    process.exit(exitCode(code, signal));
  });
}

try {
  const args = process.argv.slice(2);
  await parent(args);
} catch (error) {
  console.error(`Preview isolation: ${error.message}`);
  process.exitCode = 1;
}
