#!/usr/bin/env node

const fs = require("node:fs");

const manifestPath = "com.t3tools.t3code.yml";
const allowMajor = /^(1|true|yes)$/i.test(process.env.ALLOW_MAJOR || "");

function fail(message) {
  console.error(message);
  process.exit(1);
}

async function fetchText(url) {
  const response = await fetch(url, {
    headers: { "User-Agent": "t3code-flatpak-runtime-updater" },
  });
  if (!response.ok) {
    fail(`Failed to fetch ${url}: ${response.status} ${response.statusText}`);
  }
  return response.text();
}

async function fetchJson(url) {
  return JSON.parse(await fetchText(url));
}

function compareVersions(a, b) {
  const pa = a.split(/[.p]/).map(Number);
  const pb = b.split(/[.p]/).map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const delta = (pa[i] || 0) - (pb[i] || 0);
    if (delta !== 0) return delta;
  }
  return 0;
}

function majorOf(version) {
  const match = /^(?:v)?(\d+)/.exec(version);
  if (!match) fail(`Could not determine major version from ${version}`);
  return Number(match[1]);
}

function assertSha256Hex(value, label) {
  if (!/^[a-f0-9]{64}$/.test(value)) {
    fail(`${label} sha256 is not 64 lowercase hex characters: ${value}`);
  }
}

function replaceExactlyOnce(manifest, pattern, replacement, label) {
  const matches = manifest.match(pattern) || [];
  if (matches.length !== 1) {
    fail(`Expected exactly one ${label} source block; found ${matches.length}`);
  }
  return manifest.replace(pattern, replacement);
}

function readCurrentPins(manifest) {
  const node = /url: https:\/\/nodejs\.org\/dist\/(v(\d+)\.\d+\.\d+)\/node-\1-linux-x64\.tar\.xz\n\s+sha256: ([a-f0-9]{64})/.exec(manifest);
  const git = /url: https:\/\/mirrors\.edge\.kernel\.org\/pub\/software\/scm\/git\/git-((\d+)\.\d+\.\d+)\.tar\.xz\n\s+sha256: ([a-f0-9]{64})/.exec(manifest);
  const openssh = /url: https:\/\/cdn\.openbsd\.org\/pub\/OpenBSD\/OpenSSH\/portable\/openssh-((\d+)\.\d+p\d+)\.tar\.gz\n\s+sha256: ([a-f0-9]{64})/.exec(manifest);

  if (!node) fail("Could not find current Node.js source pin in manifest");
  if (!git) fail("Could not find current Git source pin in manifest");
  if (!openssh) fail("Could not find current OpenSSH source pin in manifest");

  return {
    node: { version: node[1], major: Number(node[2]), sha256: node[3] },
    git: { version: git[1], major: Number(git[2]), sha256: git[3] },
    openssh: { version: openssh[1], major: Number(openssh[2]), sha256: openssh[3] },
  };
}

async function latestNode(current) {
  const index = await fetchJson("https://nodejs.org/dist/index.json");
  const currentRecord = index.find((entry) => entry.version === current.version);
  const requireLts = currentRecord ? currentRecord.lts !== false : true;
  const candidates = index
    .filter((entry) => /^v\d+\.\d+\.\d+$/.test(entry.version))
    .filter((entry) => allowMajor || majorOf(entry.version) === current.major)
    .filter((entry) => Array.isArray(entry.files) && entry.files.includes("linux-x64"))
    .filter((entry) => !requireLts || entry.lts !== false)
    .map((entry) => entry.version)
    .sort((a, b) => compareVersions(b.slice(1), a.slice(1)));

  if (candidates.length === 0) fail("No suitable Node.js linux-x64 releases found");
  const version = candidates[0];
  const shasums = await fetchText(`https://nodejs.org/dist/${version}/SHASUMS256.txt`);
  const filename = `node-${version}-linux-x64.tar.xz`;
  const match = new RegExp(`^([a-f0-9]{64})  ${filename.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`, "m").exec(shasums);
  if (!match) fail(`Could not find ${filename} in Node.js SHASUMS256.txt`);
  return {
    version,
    url: `https://nodejs.org/dist/${version}/${filename}`,
    sha256: match[1],
  };
}

async function latestGit(current) {
  const sums = await fetchText("https://mirrors.edge.kernel.org/pub/software/scm/git/sha256sums.asc");
  const candidates = [];
  for (const match of sums.matchAll(/^([a-f0-9]{64})  git-((\d+)\.\d+\.\d+)\.tar\.xz$/gm)) {
    const [, sha256, version, major] = match;
    if (!allowMajor && Number(major) !== current.major) continue;
    candidates.push({ version, sha256 });
  }
  if (candidates.length === 0) fail("No suitable Git tar.xz releases found in sha256sums.asc");
  candidates.sort((a, b) => compareVersions(b.version, a.version));
  const latest = candidates[0];
  return {
    version: latest.version,
    url: `https://mirrors.edge.kernel.org/pub/software/scm/git/git-${latest.version}.tar.xz`,
    sha256: latest.sha256,
  };
}

async function latestOpenSsh(current) {
  const notes = await fetchText("https://www.openssh.org/releasenotes.html");
  const candidates = [];
  const pattern = /SHA256 \(<a href='(https:\/\/cdn\.openbsd\.org\/pub\/OpenBSD\/OpenSSH\/portable\/openssh-((\d+)\.\d+p\d+)\.tar\.gz)'>openssh-\2\.tar\.gz<\/a>\) = ([A-Za-z0-9+/=]+)/g;
  for (const match of notes.matchAll(pattern)) {
    const [, url, version, major, base64Sha] = match;
    if (!allowMajor && Number(major) !== current.major) continue;
    const sha256 = Buffer.from(base64Sha, "base64").toString("hex");
    candidates.push({ version, url, sha256 });
  }
  if (candidates.length === 0) fail("No suitable OpenSSH portable releases found in release notes");
  candidates.sort((a, b) => compareVersions(b.version, a.version));
  return candidates[0];
}

function replaceNode(manifest, next) {
  assertSha256Hex(next.sha256, "Node.js");
  const blockPattern = /url: https:\/\/nodejs\.org\/dist\/v\d+\.\d+\.\d+\/node-v\d+\.\d+\.\d+-linux-x64\.tar\.xz\n\s+sha256: [a-f0-9]{64}/g;
  return replaceExactlyOnce(
    manifest,
    blockPattern,
    `url: ${next.url}\n        sha256: ${next.sha256}`,
    "Node.js",
  );
}

function replaceGit(manifest, next) {
  assertSha256Hex(next.sha256, "Git");
  const blockPattern = /url: https:\/\/mirrors\.edge\.kernel\.org\/pub\/software\/scm\/git\/git-\d+\.\d+\.\d+\.tar\.xz\n\s+sha256: [a-f0-9]{64}/g;
  return replaceExactlyOnce(
    manifest,
    blockPattern,
    `url: ${next.url}\n        sha256: ${next.sha256}`,
    "Git",
  );
}

function replaceOpenSsh(manifest, next) {
  assertSha256Hex(next.sha256, "OpenSSH");
  const blockPattern = /url: https:\/\/cdn\.openbsd\.org\/pub\/OpenBSD\/OpenSSH\/portable\/openssh-\d+\.\d+p\d+\.tar\.gz\n\s+sha256: [a-f0-9]{64}/g;
  return replaceExactlyOnce(
    manifest,
    blockPattern,
    `url: ${next.url}\n        sha256: ${next.sha256}`,
    "OpenSSH",
  );
}

function validateMajor(current, next, label) {
  if (!allowMajor && majorOf(next.version) !== current.major) {
    fail(`${label} update crosses major version: ${current.version} -> ${next.version}`);
  }
}

async function main() {
  const original = fs.readFileSync(manifestPath, "utf8");
  const current = readCurrentPins(original);

  const [node, git, openssh] = await Promise.all([
    latestNode(current.node),
    latestGit(current.git),
    latestOpenSsh(current.openssh),
  ]);

  validateMajor(current.node, node, "Node.js");
  validateMajor(current.git, git, "Git");
  validateMajor(current.openssh, openssh, "OpenSSH");

  let updated = original;
  updated = replaceNode(updated, node);
  updated = replaceGit(updated, git);
  updated = replaceOpenSsh(updated, openssh);

  if (/^\s+url: https:\/\/github\.com\/pingdotgg\/t3code\/releases\/(latest\/download|download\/latest)\/T3-Code-.*\.AppImage$/m.test(updated)) {
    fail("Manifest contains an unexpected moving T3 Code AppImage source URL after update");
  }

  fs.writeFileSync(manifestPath, updated);

  const changed = updated !== original;
  if (process.env.GITHUB_OUTPUT) {
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `changed=${changed}\n`);
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `node=${node.version}\n`);
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `git=${git.version}\n`);
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `openssh=${openssh.version}\n`);
  }

  console.log(`Node.js: ${current.node.version} -> ${node.version}`);
  console.log(`Git: ${current.git.version} -> ${git.version}`);
  console.log(`OpenSSH: ${current.openssh.version} -> ${openssh.version}`);
  console.log(changed ? `Updated ${manifestPath}` : `${manifestPath} already has current bundled runtime pins`);
}

main().catch((error) => fail(error.stack || error.message));
