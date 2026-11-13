#!/usr/bin/env python3
"""Install optional Android tools in T3 Code's private Flatpak data."""

from contextlib import contextmanager
import argparse
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import urllib.error
import uuid
import xml.etree.ElementTree as ET
import zipfile


JAVA_VERSION = "17.0.21.0.2"
JAVA_URL = (
    "https://aka.ms/download-jdk/microsoft-jdk-17.0.21.0.2-linux-x64.tar.gz"
)
JAVA_SHA256 = "f7230e6d76ccfd62ceb11687a24e93d1f63d236e6a5fdeb8d923700075de42f8"
TOOLS_VERSION = "15859902"
TOOLS_URL = (
    "https://dl.google.com/android/repository/"
    "commandlinetools-linux-15859902_latest.zip"
)
TOOLS_SHA256 = "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583"
MAX_JAVA_BYTES = 400_000_000
MAX_TOOLS_BYTES = 250_000_000
MAX_EXTRACTED_BYTES = 1_500_000_000
MAX_ARCHIVE_ENTRIES = 100_000
APP_DATA = Path("t3code/android")
ANDROID_TERMS_URL = "https://developer.android.com/studio/terms"


class SetupError(Exception):
    """An actionable setup failure that leaves active installs untouched."""


def data_root(environ=None):
    environ = os.environ if environ is None else environ
    base = Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / APP_DATA


def download_verified(url, sha256, destination, max_bytes):
    """Stream a pinned archive to disk and remove it if any check fails."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise SetupError("invalid pinned SHA-256 value")
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "T3-Code-Flatpak"})
        with urllib.request.urlopen(request, timeout=60) as response, open(destination, "xb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise SetupError(f"download exceeds the {max_bytes} byte limit")
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != sha256:
            raise SetupError(f"SHA-256 check failed for {url}")
    except urllib.error.HTTPError as error:
        error.close()
        Path(destination).unlink(missing_ok=True)
        raise SetupError(f"download failed with HTTP {error.code}: {url}") from error
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise


def _archive_path(name):
    if "\x00" in name or "\\" in name or name.startswith("/"):
        raise SetupError(f"unsafe archive path: {name!r}")
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise SetupError(f"archive path escapes its destination: {name!r}")
        parts.append(part)
    return PurePosixPath(*parts)


def _link_target(member_path, target):
    if not target or target.startswith("/") or "\x00" in target or "\\" in target:
        raise SetupError(f"unsafe archive link target: {target!r}")
    parts = list(member_path.parts[:-1])
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise SetupError(f"archive link escapes its destination: {target!r}")
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts)


def extract_tar_safely(archive, destination):
    """Extract regular files, directories, and confined relative symlinks."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise SetupError("Java archive contains too many entries")
        expanded = sum(member.size for member in members if member.isfile())
        if expanded > MAX_EXTRACTED_BYTES:
            raise SetupError("Java archive expands beyond the extraction limit")

        seen = set()
        files, directories, links = [], [], []
        for member in members:
            path = _archive_path(member.name)
            if not path.parts:
                if member.isdir():
                    continue
                raise SetupError("archive contains a file at its root")
            if path in seen:
                raise SetupError(f"archive contains a duplicate path: {path}")
            seen.add(path)
            if member.isdir():
                directories.append((member, path))
            elif member.isfile():
                files.append((member, path))
            elif member.issym():
                _link_target(path, member.linkname)
                links.append((member, path))
            else:
                raise SetupError(f"Java archive contains an unsupported entry: {path}")

        link_paths = {path for _, path in links}
        for member, path in files + directories + links:
            if any(parent in link_paths for parent in path.parents):
                raise SetupError(f"Java archive path has a symlink ancestor: {path}")

        # Create all ordinary paths before links, so an archive link cannot redirect
        # a later file write. O_NOFOLLOW and O_EXCL add a second boundary.
        for _, path in sorted(directories, key=lambda item: len(item[1].parts)):
            (destination / Path(*path.parts)).mkdir(parents=True, exist_ok=True)
        for member, path in files:
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags, member.mode & 0o777 or 0o644)
            with os.fdopen(descriptor, "wb") as output:
                stream = source.extractfile(member)
                if stream is None:
                    raise SetupError(f"cannot read Java archive entry: {path}")
                with stream:
                    shutil.copyfileobj(stream, output)
        for member, path in links:
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(member.linkname, target)

    root = destination.resolve()
    for member, path in links:
        target = destination.joinpath(*path.parts)
        resolved = target.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise SetupError(f"Java archive link escapes its destination: {path}")


def extract_zip_safely(archive, destination):
    """Extract regular files and directories; reject links and special entries."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise SetupError("Android command-line tools archive contains too many entries")
        expanded = sum(info.file_size for info in infos)
        if expanded > MAX_EXTRACTED_BYTES:
            raise SetupError("Android command-line tools archive expands beyond the limit")

        entries = []
        seen = set()
        for info in infos:
            path = _archive_path(info.filename)
            if not path.parts:
                if info.is_dir():
                    continue
                raise SetupError("archive contains a file at its root")
            if path in seen:
                raise SetupError(f"archive contains a duplicate path: {path}")
            seen.add(path)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                if kind not in (0, stat.S_IFDIR):
                    raise SetupError(f"command-line tools archive has an unsafe entry: {path}")
                entries.append((info, path, True, mode))
            else:
                if kind not in (0, stat.S_IFREG):
                    raise SetupError(f"command-line tools archive has an unsafe entry: {path}")
                entries.append((info, path, False, mode))

        for _, path, is_directory, _ in sorted(entries, key=lambda item: len(item[1].parts)):
            if is_directory:
                destination.joinpath(*path.parts).mkdir(parents=True, exist_ok=True)
        for info, path, is_directory, mode in entries:
            if is_directory:
                continue
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags, mode & 0o777 or 0o644)
            with os.fdopen(descriptor, "wb") as output, source.open(info) as stream:
                shutil.copyfileobj(stream, output)


def _java_home(extracted):
    candidates = [path.parent.parent for path in Path(extracted).rglob("bin/java")]
    candidates = [path for path in candidates if (path / "bin/java").is_file()]
    if len(candidates) != 1:
        raise SetupError("Microsoft JDK archive did not contain exactly one bin/java")
    return candidates[0]


def _system_image_id(value):
    match = re.fullmatch(
        r"system-images;android-(\d+);([a-z0-9_]+);(x86_64)", value
    )
    if not match:
        raise SetupError(
            "choose an x86_64 system image such as "
            "system-images;android-36;google_apis;x86_64"
        )
    return value


def _check_explicit_overrides(root, names=("ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME")):
    private_root = Path(root).resolve()
    for name in names:
        value = os.environ.get(name)
        if not value:
            continue
        configured = Path(value).resolve()
        if configured != private_root and private_root not in configured.parents:
            raise SetupError(
                f"{name} explicitly selects {value}; setup only installs into T3 Code app data "
                "and will not modify that SDK or Java. Clear the override to use the private install."
            )


def _revision_tuple(value, package_id):
    if not re.fullmatch(r"\d+(?:\.\d+)*", value or ""):
        raise SetupError(f"SDK package revision is invalid: {package_id}")
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _xml_local_name(element):
    return element.tag.rsplit("}", 1)[-1]


def _package_metadata(package_dir, package_id):
    metadata = package_dir / "source.properties"
    if not metadata.is_file():
        raise SetupError(f"SDK package {package_id} is missing source.properties")
    properties = {}
    for line in metadata.read_text(errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key.strip()] = value.strip()
    source_path = properties.get("Pkg.Path")
    source_revision = properties.get("Pkg.Revision")
    if not source_revision:
        raise SetupError(f"SDK package metadata is incomplete or mismatched: {package_id}")
    if source_path and source_path != package_id:
        raise SetupError(f"SDK package metadata is incomplete or mismatched: {package_id}")
    source_revision = _revision_tuple(source_revision, package_id)

    package_xml = package_dir / "package.xml"
    if not package_xml.is_file():
        if source_path == package_id:
            return
        raise SetupError(f"SDK package metadata has no authoritative package path: {package_id}")
    try:
        document = ET.parse(package_xml).getroot()
    except ET.ParseError as error:
        raise SetupError(f"SDK package metadata XML is invalid: {package_id}") from error
    local_packages = [
        element for element in document.iter()
        if _xml_local_name(element) == "localPackage"
    ]
    if len(local_packages) != 1 or local_packages[0].get("path") != package_id:
        raise SetupError(f"SDK package metadata is incomplete or mismatched: {package_id}")
    revisions = [
        element for element in local_packages[0]
        if _xml_local_name(element) == "revision"
    ]
    if len(revisions) != 1:
        raise SetupError(f"SDK package metadata has no authoritative revision: {package_id}")
    revision_parts = {
        _xml_local_name(element): (element.text or "").strip()
        for element in revisions[0]
    }
    if not revision_parts.get("major"):
        raise SetupError(f"SDK package metadata has no authoritative revision: {package_id}")
    xml_revision = ".".join(
        (revision_parts.get(name) or "0") for name in ("major", "minor", "micro")
    )
    if source_revision != _revision_tuple(xml_revision, package_id):
        raise SetupError(f"SDK package source and XML revisions conflict: {package_id}")


def _sdkmanager_run(command, env, accept_licenses, runner):
    input_text = "y\n" * 100 if accept_licenses else None
    result = runner(command, env=env, input_text=input_text)
    if result.returncode:
        raise SetupError(f"sdkmanager failed ({result.returncode}): {' '.join(command)}")
    return result


def _atomic_current(link, target):
    if os.path.lexists(link) and not link.is_symlink():
        raise SetupError(
            f"refusing to replace existing non-managed path: {link}; "
            "configure it explicitly with ANDROID_HOME or JAVA_HOME"
        )
    temporary = link.with_name(f".{link.name}.{uuid.uuid4().hex}.next")
    relative_target = os.path.relpath(target, link.parent)
    try:
        os.symlink(relative_target, temporary)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def _current_target(link):
    if not link.is_symlink():
        return None
    target = Path(os.readlink(link))
    if not target.is_absolute():
        target = link.parent / target
    return Path(os.path.abspath(target))


def _restore_current(link, previous_target):
    current_target = _current_target(link)
    if current_target == previous_target:
        return
    if previous_target is None:
        if os.path.lexists(link):
            if not link.is_symlink():
                raise SetupError(f"cannot roll back managed current path: {link}")
            link.unlink()
        return
    _atomic_current(link, previous_target)


def _new_generation(directory, prefix):
    while True:
        generation = directory / f"{prefix}-{uuid.uuid4().hex[:10]}"
        if not os.path.lexists(generation):
            return generation


def _remove_generation(generation):
    if generation.is_symlink():
        generation.unlink()
    elif generation.is_dir():
        shutil.rmtree(generation)
    else:
        generation.unlink(missing_ok=True)


def _rollback_generations(previous_targets, generations):
    failures = []
    for link, previous_target in previous_targets:
        try:
            _restore_current(link, previous_target)
        except BaseException as error:
            failures.append(f"restore {link}: {error}")
    active_targets = set()
    active_state_unknown = False
    for link, _ in previous_targets:
        try:
            target = _current_target(link)
            if target is not None:
                active_targets.add(target)
        except BaseException as error:
            failures.append(f"inspect {link}: {error}")
            active_state_unknown = True
    for generation in generations:
        if (
            active_state_unknown
            or generation is None
            or Path(os.path.abspath(generation)) in active_targets
        ):
            continue
        try:
            _remove_generation(generation)
        except BaseException as error:
            failures.append(f"remove unactivated generation {generation}: {error}")
    if failures:
        raise SetupError("activation rollback was incomplete: " + "; ".join(failures))


@contextmanager
def _setup_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".setup.lock"
    descriptor = os.open(
        lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _run_process(command, env=None, input_text=None):
    return subprocess.run(command, env=env, input=input_text, text=True, check=False)


def _probe_java(java):
    return subprocess.run([str(java), "-version"], capture_output=True, text=True, check=False)


def _prepare_java(stage, downloader):
    java_archive = stage / "microsoft-jdk.tar.gz"
    print(f"Downloading Microsoft OpenJDK {JAVA_VERSION}…", flush=True)
    downloader(JAVA_URL, JAVA_SHA256, java_archive, MAX_JAVA_BYTES)
    java_unpacked = stage / "java-unpacked"
    extract_tar_safely(java_archive, java_unpacked)
    java_home = _java_home(java_unpacked)
    try:
        java_result = _probe_java(java_home / "bin/java")
    except OSError as error:
        raise SetupError(f"downloaded Java runtime could not start: {error}") from error
    java_output = (java_result.stdout or "") + (java_result.stderr or "")
    if java_result.returncode or not re.search(r'(?<!\d)17(?:\.0)?[. "]', java_output):
        raise SetupError("downloaded Java runtime is not a working Java 17 runtime")
    return java_home


def _promote_java(java_home, java_versions, root, created_generations=None):
    java_versions.mkdir(parents=True, exist_ok=True)
    generation = _new_generation(java_versions, f"microsoft-jdk-{JAVA_VERSION}")
    if created_generations is not None:
        created_generations.append(generation)
    link = root / "java/current"
    previous_target = _current_target(link)
    staged_java = java_home.parent.parent / "java-install"
    try:
        os.replace(java_home, staged_java)
        os.replace(staged_java, generation)
        _atomic_current(link, generation)
    except BaseException as error:
        try:
            _rollback_generations(((link, previous_target),), (generation,))
        except SetupError as rollback_error:
            raise SetupError(f"Java activation failed and {rollback_error}") from error
        raise
    return generation


def install_private_java(root=None, downloader=None):
    """Install the pinned JDK privately without touching an explicit SDK."""
    root = Path(root or data_root())
    _check_explicit_overrides(root, names=("JAVA_HOME",))
    downloader = download_verified if downloader is None else downloader
    java_root = root / "java"
    java_versions = java_root / "versions"
    with _setup_lock(root):
        link = java_root / "current"
        if os.path.lexists(link) and not link.is_symlink():
            raise SetupError(
                f"refusing to replace existing non-managed path: {link}; configure JAVA_HOME explicitly"
            )
        with tempfile.TemporaryDirectory(prefix=".setup-", dir=root) as temporary:
            java_home = _prepare_java(Path(temporary), downloader)
            generation = _promote_java(java_home, java_versions, root)
    print(f"Java ready: {java_root / 'current'}")
    print(f"Installed generation: {generation.name}")
    print("Restart T3 Code so its Android device host discovers Java.")


def setup_android(system_image, accept_licenses=False, root=None, downloader=None, runner=None):
    """Build a complete new SDK generation and atomically activate it."""
    system_image = _system_image_id(system_image)
    root = Path(root or data_root())
    _check_explicit_overrides(root)
    if not accept_licenses:
        if not sys.stdin.isatty():
            raise SetupError(
                "license review needs a terminal, or pass --accept-licenses after "
                f"reviewing Google's Android SDK terms at {ANDROID_TERMS_URL}"
            )
        print(f"Review Google's Android SDK terms at {ANDROID_TERMS_URL}", file=sys.stderr)
        if input("Type 'accept' to review and accept the SDK licenses in sdkmanager: ").strip() != "accept":
            raise SetupError("Android SDK licenses were not accepted; no files were downloaded")
        accept_licenses = True
    downloader = download_verified if downloader is None else downloader
    runner = _run_process if runner is None else runner
    sdk_root = root / "sdk"
    java_root = root / "java"
    sdk_versions = sdk_root / "versions"
    java_versions = java_root / "versions"

    with _setup_lock(root):
        for link in (root / "sdk/current", root / "java/current"):
            if os.path.lexists(link) and not link.is_symlink():
                raise SetupError(
                    f"refusing to replace existing non-managed path: {link}; "
                    "configure it explicitly with ANDROID_HOME or JAVA_HOME"
                )
        sdk_versions.mkdir(parents=True, exist_ok=True)
        java_versions.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".setup-", dir=root) as temporary:
            stage = Path(temporary)
            tools_archive = stage / "android-command-line-tools.zip"
            unpacked_java_home = _prepare_java(stage, downloader)
            print(f"Downloading Android command-line tools {TOOLS_VERSION}…", flush=True)
            downloader(TOOLS_URL, TOOLS_SHA256, tools_archive, MAX_TOOLS_BYTES)

            sdk_stage = stage / "sdk"
            sdk_stage.mkdir()
            tools_unpacked = stage / "tools-unpacked"
            extract_zip_safely(tools_archive, tools_unpacked)
            packaged_tools = tools_unpacked / "cmdline-tools"
            sdkmanager_archive_entry = packaged_tools / "bin/sdkmanager"
            if not sdkmanager_archive_entry.is_file() or not os.access(sdkmanager_archive_entry, os.X_OK):
                raise SetupError("Android tools archive is missing cmdline-tools/bin/sdkmanager")
            (sdk_stage / "cmdline-tools").mkdir()
            os.replace(packaged_tools, sdk_stage / "cmdline-tools/latest")

            sdk_generation = _new_generation(sdk_versions, f"android-sdk-{TOOLS_VERSION}")
            sdkmanager = sdk_stage / "cmdline-tools/latest/bin/sdkmanager"
            java_env = os.environ.copy()
            java_env["JAVA_HOME"] = str(unpacked_java_home)
            java_env["PATH"] = str(unpacked_java_home / "bin") + os.pathsep + java_env.get("PATH", "")
            android_user_home = Path(os.environ.get("ANDROID_USER_HOME", root / "user"))
            android_avd_home = Path(os.environ.get("ANDROID_AVD_HOME", android_user_home / "avd"))
            android_user_home.mkdir(parents=True, exist_ok=True)
            android_avd_home.mkdir(parents=True, exist_ok=True)
            sdkmanager_env = java_env.copy()
            sdkmanager_env["ANDROID_HOME"] = str(sdk_stage)
            sdkmanager_env["ANDROID_SDK_ROOT"] = str(sdk_stage)
            sdkmanager_env["ANDROID_USER_HOME"] = str(android_user_home)
            sdkmanager_env["ANDROID_AVD_HOME"] = str(android_avd_home)

            print("Reviewing Android SDK licenses…", flush=True)
            if (sdk_stage / "licenses/android-sdk-license").exists():
                raise SetupError("bootstrap tools unexpectedly contain SDK license data")
            _sdkmanager_run(
                [str(sdkmanager), f"--sdk_root={sdk_stage}", "--licenses"],
                sdkmanager_env, accept_licenses, runner,
            )
            license_file = sdk_stage / "licenses/android-sdk-license"
            if not license_file.is_file() or not license_file.read_text(errors="replace").strip():
                raise SetupError("sdkmanager did not record acceptance of the Android SDK license")
            packages = ["platform-tools", "emulator", system_image]
            print("Installing Android SDK packages…", flush=True)
            _sdkmanager_run(
                [str(sdkmanager), f"--sdk_root={sdk_stage}", "--install", *packages],
                sdkmanager_env, accept_licenses, runner,
            )

            required_tools = (
                (sdk_stage / "platform-tools/adb", "platform-tools adb"),
                (sdk_stage / "emulator/emulator", "emulator"),
                (sdk_stage / "cmdline-tools/latest/bin/avdmanager", "avdmanager"),
            )
            missing = [label for path, label in required_tools if not path.is_file() or not os.access(path, os.X_OK)]
            if missing:
                raise SetupError("sdkmanager completed without executable tools: " + ", ".join(missing))
            image_dir = sdk_stage / "system-images" / system_image.split(";")[1] / system_image.split(";")[2] / system_image.split(";")[3]
            if not image_dir.is_dir():
                raise SetupError(f"sdkmanager did not install the selected image: {system_image}")
            system_image_file = image_dir / "system.img"
            if not system_image_file.is_file() or system_image_file.stat().st_size == 0:
                raise SetupError(f"selected system image is incomplete: {system_image}")
            _package_metadata(sdk_stage / "platform-tools", "platform-tools")
            _package_metadata(sdk_stage / "emulator", "emulator")
            _package_metadata(image_dir, system_image)

            # Keep prior generations intact. Only a same-filesystem rename and
            # symlink replacement change the active SDK or Java installation.
            sdk_link = sdk_root / "current"
            java_link = java_root / "current"
            previous_sdk = _current_target(sdk_link)
            previous_java = _current_target(java_link)
            java_generations = []
            try:
                os.replace(sdk_stage, sdk_generation)
                _promote_java(
                    unpacked_java_home, java_versions, root,
                    created_generations=java_generations,
                )
                _atomic_current(sdk_link, sdk_generation)
            except BaseException as error:
                try:
                    _rollback_generations(
                        ((sdk_link, previous_sdk), (java_link, previous_java)),
                        (sdk_generation, *java_generations),
                    )
                except SetupError as rollback_error:
                    raise SetupError(f"SDK activation failed and {rollback_error}") from error
                raise

    print(f"Android SDK ready: {sdk_root / 'current'}")
    print(f"Java ready: {java_root / 'current'}")
    print(f"Selected image: {system_image}")
    print("Restart T3 Code so its Android device host discovers the new SDK.")


def _which_java(environ):
    java_home = environ.get("JAVA_HOME")
    if java_home:
        return Path(java_home) / "bin/java"
    private = data_root(environ) / "java/current/bin/java"
    if private.is_file() and os.access(private, os.X_OK):
        return private
    java = shutil.which("java", path=environ.get("PATH"))
    return Path(java) if java else None


def _sdk_path(environ):
    explicit = environ.get("ANDROID_HOME") or environ.get("ANDROID_SDK_ROOT")
    if explicit:
        return Path(explicit), "environment"
    private = data_root(environ) / "sdk/current"
    return private, "app-private"


def diagnose(environ=None, output=None):
    environ = os.environ if environ is None else environ
    output = sys.stdout if output is None else output
    sdk, source = _sdk_path(environ)
    print(f"Android SDK ({source}): {sdk}", file=output)
    sdk_ready = True
    for relative in (
        "platform-tools/adb", "emulator/emulator",
        "cmdline-tools/latest/bin/avdmanager", "cmdline-tools/latest/bin/sdkmanager",
    ):
        path = sdk / relative
        state = "ready" if path.is_file() and os.access(path, os.X_OK) else "missing"
        sdk_ready = sdk_ready and state == "ready"
        print(f"  {relative}: {state}", file=output)

    android_user_home = Path(environ.get("ANDROID_USER_HOME", data_root(environ) / "user"))
    android_avd_home = Path(environ.get("ANDROID_AVD_HOME", android_user_home / "avd"))
    print(f"Android preferences: {android_user_home}", file=output)
    print(f"AVD storage: {android_avd_home}", file=output)

    java = _which_java(environ)
    java_ready = False
    if java and java.is_file() and os.access(java, os.X_OK):
        try:
            result = subprocess.run([str(java), "-version"], capture_output=True, text=True, check=False)
        except OSError as error:
            print(f"Java: {java} (failed to start: {error})", file=output)
        else:
            version = (result.stderr or result.stdout).strip().splitlines()
            print(f"Java: {java} ({version[0] if version else 'version unavailable'})", file=output)
            java_ready = result.returncode == 0
            if not java_ready:
                print(f"  state: failed to start (exit {result.returncode})", file=output)
    else:
        print("Java: unavailable", file=output)

    kvm = Path("/dev/kvm")
    if kvm.exists():
        access = "read/write available" if os.access(kvm, os.R_OK | os.W_OK) else "present, access denied"
        print(f"KVM: {access} ({kvm})", file=output)
        emulator = sdk / "emulator/emulator"
        if emulator.is_file() and os.access(emulator, os.X_OK):
            try:
                result = subprocess.run([str(emulator), "-accel-check"], capture_output=True, text=True, check=False)
            except OSError as error:
                print(f"  emulator acceleration check failed to start: {error}", file=output)
            else:
                detail = (result.stdout or result.stderr).strip().replace("\n", "; ")
                status = "" if result.returncode == 0 else f" (exit {result.returncode})"
                print(f"  emulator acceleration check{status}: {detail or 'no detail'}", file=output)
    else:
        print("KVM: unavailable; Flatpak needs the narrow --device=kvm permission for acceleration", file=output)

    print("Diagnostics command:", file=output)
    print("  flatpak run --command=t3code-android-diagnostics com.t3tools.t3code", file=output)
    print("Setup command:", file=output)
    print(
        "  flatpak run --command=t3code-android-setup com.t3tools.t3code "
        "--system-image 'system-images;android-36;google_apis;x86_64'",
        file=output,
    )
    print(
        "  For an explicit read-only SDK, install only Java privately with: "
        "flatpak run --command=t3code-android-setup com.t3tools.t3code install-java",
        file=output,
    )
    print("  Add --accept-licenses only after reviewing Google's Android SDK terms.", file=output)
    if not sdk_ready or not java_ready:
        print(
            "An existing host SDK can be exposed as a read-only directory grant; "
            "do not grant home or all-device access.",
            file=output,
        )
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Optional Android tools for T3 Code Flatpak")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="install pinned Java and SDK tools in app data")
    setup.add_argument(
        "--system-image", required=True,
        help="an x86_64 sdkmanager image id, such as system-images;android-36;google_apis;x86_64",
    )
    setup.add_argument(
        "--accept-licenses", action="store_true",
        help="explicitly accept Android SDK licenses after reviewing Google's terms",
    )
    commands.add_parser("diagnostics", help="show SDK, Java, and KVM state")
    commands.add_parser(
        "install-java",
        help="install pinned Java privately for an existing read-only SDK",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            setup_android(args.system_image, args.accept_licenses)
            return 0
        if args.command == "install-java":
            install_private_java()
            return 0
        return diagnose()
    except (SetupError, OSError, tarfile.TarError, zipfile.BadZipFile, urllib.error.URLError) as error:
        print(f"Android setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
