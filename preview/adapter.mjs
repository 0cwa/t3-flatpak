import { constants, createReadStream, createWriteStream } from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { exposedPath } from "./paths.mjs";

const transfers = new WeakMap();
const wrappers = new WeakMap();
const remove = (directory) => fsp.rm(directory, { recursive: true, force: true });

export function isolatedPlaywright(playwright) {
  let chromium = wrappers.get(playwright.chromium);
  if (!chromium) {
    chromium = new Proxy(playwright.chromium, {
      get(target, property) {
        if (property !== "launch" && property !== "launchPersistentContext") {
          const value = Reflect.get(target, property);
          return typeof value === "function" ? value.bind(target) : value;
        }
        return async (...args) => {
          const directory = await fsp.mkdtemp(path.join(os.tmpdir(), "t3-preview-transfer-"));
          const exposed = exposedPath(directory);
          const options = args[property === "launch" ? 0 : 1] ?? {};
          args[property === "launch" ? 0 : 1] = {
            ...options,
            downloadsPath: exposed,
            args: [...options.args ?? [], `--t3-transfer-dir=${exposed}`],
          };
          try {
            const browser = await target[property](...args);
            transfers.set(browser, exposed);
            browser.once(property === "launch" ? "disconnected" : "close", () => void remove(directory).catch(() => {}));
            return browser;
          } catch (error) {
            await remove(directory);
            throw error;
          }
        };
      },
    });
    wrappers.set(playwright.chromium, chromium);
  }
  return { ...playwright, chromium };
}

function transferDirectory(page) {
  const context = page.context();
  const directory = transfers.get(context) ?? transfers.get(context.browser());
  if (!directory) throw new Error("The isolated browser has no file-transfer directory.");
  return directory;
}

async function copyUpload(source, directory, name, ancestors = new Set()) {
  const stat = await fsp.stat(source);
  const destination = `/proc/self/fd/${directory.fd}/${name}`;
  if (stat.isFile()) {
    await fsp.copyFile(source, destination, constants.COPYFILE_EXCL);
  } else if (stat.isDirectory()) {
    const key = `${stat.dev}:${stat.ino}`;
    if (ancestors.has(key)) throw new Error("Upload directory contains a symlink cycle.");
    await fsp.mkdir(destination, { mode: 0o700 });
    const child = await fsp.open(destination, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try {
      for (const entry of await fsp.readdir(source)) {
        await copyUpload(path.join(source, entry), child, entry, new Set([...ancestors, key]));
      }
    } finally {
      await child.close();
    }
  } else {
    throw new Error("Only regular files and directories can be uploaded.");
  }
}

export async function stageUploads(page, paths) {
  const root = transferDirectory(page);
  const staged = [];
  for (const filename of paths) {
    const source = await fsp.realpath(filename);
    const name = path.basename(path.resolve(filename));
    if (!name) throw new Error("Uploading the filesystem root is unsupported.");
    const directory = await fsp.mkdtemp(path.join(root, "upload-"));
    const handle = await fsp.open(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try {
      await copyUpload(source, handle, name);
    } finally {
      await handle.close();
    }
    page.once("close", () => void remove(directory).catch(() => {}));
    staged.push(path.join(directory, name));
  }
  return staged;
}

export async function saveDownload(download, destination) {
  const root = transferDirectory(download.page());
  const source = await download.path();
  if (!source || path.dirname(source) !== root) throw new Error("Download escaped its staging directory.");
  const directory = await fsp.open(root, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  let input;
  let output;
  try {
    input = await fsp.open(`/proc/self/fd/${directory.fd}/${path.basename(source)}`, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    if (!(await input.stat()).isFile()) throw new Error("Download is not a regular file.");
    output = await fsp.open(destination, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await pipeline(
      createReadStream("", { fd: input.fd, autoClose: false, emitClose: false }),
      createWriteStream("", { fd: output.fd, autoClose: false, emitClose: false }),
    );
  } finally {
    await input?.close();
    await output?.close();
    await directory.close();
  }
}
