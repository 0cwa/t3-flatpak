import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Flatpak persist mounts retain their parent-side alias in realpath(), while
// Spawn exposes their backing app-private path in the new sandbox.
export function exposedPath(filename) {
  const resolved = fs.realpathSync(filename);
  const home = fs.realpathSync(os.homedir());
  const prefix = path.join(home, ".t3");
  if (resolved === prefix || resolved.startsWith(prefix + path.sep)) {
    const backing = path.join(home, ".var/app/com.t3tools.t3code/.t3", path.relative(prefix, resolved));
    try {
      const a = fs.statSync(resolved);
      const b = fs.statSync(backing);
      if (a.dev === b.dev && a.ino === b.ino) return fs.realpathSync(backing);
    } catch {}
  }
  return resolved;
}
