#!/usr/bin/env python3
"""Source-level regression check; not a Flatpak runtime confinement test."""
from pathlib import Path
import re
import sys

manifest = Path(sys.argv[1] if len(sys.argv) > 1 else "com.t3tools.t3code.yml")
launcher = Path(sys.argv[2] if len(sys.argv) > 2 else "startt3code")
source = manifest.read_text()
launcher_source = launcher.read_text()
match = re.search(r"(?m)^finish-args:\s*\n((?:[ \t]+.*\n|\s*\n)*)", source)
if not match:
    sys.exit("FAIL: cannot identify finish-args")
permissions = [re.sub(r"\s+#.*$", "", line).strip()[2:].strip(" \"'")
               for line in match.group(1).splitlines() if line.strip().startswith("- ")]
disallowed = (
    "--talk-name=org.freedesktop.Flatpak",
    "--own-name=org.freedesktop.Flatpak",
    "--socket=session-bus",
    "--socket=system-bus",
    "--filesystem=home",
    "--filesystem=host",
    "--filesystem=~/.ssh",
    "--filesystem=~/.gnupg",
    "--filesystem=~/.local/bin",
    "--filesystem=xdg-config/gnupg",
)
errors = [f"unsafe default permission: {p}" for p in permissions if p.startswith(disallowed)]
if re.search(r"\bflatpak-spawn\b", launcher_source):
    errors.append("launcher references flatpak-spawn")
if re.search(r"\$HOME/\.local/bin|\$\{HOME\}/\.local/bin", launcher_source):
    errors.append("launcher prepends host ~/.local/bin")
if errors:
    sys.exit("FAIL:\n" + "\n".join(errors))
print("PASS: no known default host-command or credential grants in source")
