#!/usr/bin/env node

const fs = require("node:fs");

const manifestPath = "com.t3tools.t3code.yml";
const repo = "pingdotgg/t3code";
const apiUrl = `https://api.github.com/repos/${repo}/releases/latest`;

function fail(message) {
  console.error(message);
  process.exit(1);
}

async function fetchText(url, headers = {}) {
  const response = await fetch(url, { headers });
  if (!response.ok) {
    fail(`Failed to fetch ${url}: ${response.status} ${response.statusText}`);
  }

  return response.text();
}

function findLinuxAppImage(release) {
  const assets = release.assets.filter((asset) =>
    /^T3-Code-[^/]+-x86_64\.AppImage$/.test(asset.name),
  );

  if (assets.length !== 1) {
    fail(`Expected exactly one x86_64 AppImage in ${release.tag_name}; found ${assets.length}`);
  }

  return assets[0];
}

function sha256FromDigest(asset) {
  const match = /^sha256:([a-f0-9]{64})$/.exec(asset.digest || "");
  if (!match) {
    fail(`Missing or invalid GitHub sha256 digest for ${asset.name}`);
  }

  return match[1];
}

function replaceSource(manifest, tagName, assetName, sha256) {
  const sourcePattern = new RegExp(
    "url: https://github\\.com/pingdotgg/t3code/releases/download/[^/]+/T3-Code-[^/]+-x86_64\\.AppImage\\n" +
      "        dest-filename: T3-Code\\.AppImage\\n" +
      "        sha256: [a-f0-9]{64}\\n" +
      "        only-arches: \\[x86_64\\]",
  );
  const replacement =
    `url: https://github.com/pingdotgg/t3code/releases/download/${tagName}/${assetName}\n` +
    "        dest-filename: T3-Code.AppImage\n" +
    `        sha256: ${sha256}\n` +
    "        only-arches: [x86_64]";

  if (!sourcePattern.test(manifest)) {
    fail(`Could not find x86_64 AppImage source block in ${manifestPath}`);
  }

  return manifest.replace(sourcePattern, replacement);
}

async function main() {
  const headers = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };

  if (process.env.GITHUB_TOKEN) {
    headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  }

  const release = JSON.parse(await fetchText(apiUrl, headers));
  const tagName = release.tag_name;

  if (!tagName) {
    fail("Latest T3 Code release did not include a tag name");
  }

  const asset = findLinuxAppImage(release);
  const sha256 = sha256FromDigest(asset);

  if (!asset.browser_download_url.endsWith(`/${tagName}/${asset.name}`)) {
    fail(`Unexpected download URL for ${asset.name}: ${asset.browser_download_url}`);
  }

  const manifest = fs.readFileSync(manifestPath, "utf8");
  fs.writeFileSync(manifestPath, replaceSource(manifest, tagName, asset.name, sha256));

  if (process.env.GITHUB_OUTPUT) {
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `version=${tagName}\n`);
  }

  console.log(`Updated ${manifestPath} to T3 Code ${tagName}`);
}

main().catch((error) => fail(error.stack || error.message));
