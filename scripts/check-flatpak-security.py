#!/usr/bin/env python3
"""Source-level regression check; not a Flatpak runtime confinement test."""

from pathlib import Path
import posixpath
import re
import sys

import yaml


class ManifestLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate keys instead of silently checking one value."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in keys:
                raise yaml.constructor.ConstructorError(
                    None, None, "manifest keys must be unique strings", key_node.start_mark
                )
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def read_permissions(source):
    manifest = yaml.load(source, Loader=ManifestLoader)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a YAML mapping")
    permissions = manifest.get("finish-args")
    if not isinstance(permissions, list) or not all(
        isinstance(permission, str) for permission in permissions
    ):
        raise ValueError("finish-args must be a list of strings")
    return permissions


def includes_flatpak_bus_name(name):
    target = "org.freedesktop.Flatpak"
    if name in (target, "*"):
        return True
    if name.endswith(".*"):
        prefix = name[:-2]
        return target == prefix or target.startswith(prefix + ".")
    return False


def unsafe_filesystem(value):
    path, separator, mode = value.rpartition(":")
    if not separator or mode not in {"ro", "rw", "create"}:
        path = value
    base, _, relative = path.partition("/")
    if base == "~" or base.startswith("xdg-"):
        # These names expand before Flatpak resolves '..' on the host.
        relative = posixpath.normpath(relative)
        if relative == ".." or relative.startswith("../"):
            return True
    path = posixpath.normpath(path)
    if path.startswith("/"):
        path = "/" + path.lstrip("/")
    if path in {"home", "host", "host-os", "host-etc", "/", "/home", "/var/home"}:
        return True

    # Recognize equivalent absolute home paths as well as Flatpak's ~/ syntax.
    for home in ("/root", "/var/roothome"):
        if path == home or path.startswith(home + "/"):
            path = "~" + path[len(home):]
            break
    for homes in ("/home/", "/var/home/"):
        if path.startswith(homes):
            _, _, relative = path[len(homes):].partition("/")
            path = "~/" + relative if relative else "~"
            break

    protected_paths = (
        "~/.ssh", "~/.gnupg", "~/.local/bin", "~/.config/gnupg", "xdg-config/gnupg"
    )
    # Reject a protected directory, its children, and a parent exposing it.
    return any(
        path == protected
        or path.startswith(protected + "/")
        or protected.startswith(path + "/")
        for protected in protected_paths
    )


def check_permissions(permissions):
    errors = []
    watched = {"--filesystem", "--talk-name", "--own-name", "--socket"}
    index = 0
    while index < len(permissions):
        permission = permissions[index]
        index += 1
        option, separator, value = permission.partition("=")
        if option not in watched:
            continue
        if not separator:
            if index == len(permissions) or permissions[index].startswith("--"):
                errors.append(f"missing value for permission: {option}")
                continue
            value = permissions[index]
            index += 1
        if not value:
            errors.append(f"missing value for permission: {option}")
            continue
        unsafe = (
            (option == "--filesystem" and unsafe_filesystem(value))
            or (option in {"--talk-name", "--own-name"} and includes_flatpak_bus_name(value))
            or (option == "--socket" and value in {"session-bus", "system-bus"})
        )
        if unsafe:
            errors.append(f"unsafe default permission: {option}={value}")
    return errors


def main():
    manifest = Path(sys.argv[1] if len(sys.argv) > 1 else "com.t3tools.t3code.yml")
    launcher = Path(sys.argv[2] if len(sys.argv) > 2 else "startt3code")
    try:
        permissions = read_permissions(manifest.read_text())
        launcher_source = launcher.read_text()
    except (OSError, ValueError, yaml.YAMLError) as error:
        sys.exit(f"FAIL: {error}")
    errors = check_permissions(permissions)
    if re.search(r"\bflatpak-spawn\b", launcher_source):
        errors.append("launcher references flatpak-spawn")
    if re.search(r"\$HOME/\.local/bin|\$\{HOME\}/\.local/bin", launcher_source):
        errors.append("launcher references host ~/.local/bin")
    if errors:
        sys.exit("FAIL:\n" + "\n".join(errors))
    print("PASS: no known default host-command or credential grants in source")


if __name__ == "__main__":
    main()
