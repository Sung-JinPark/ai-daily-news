#!/usr/bin/env node
// Populated-state verification harness (N6).
//
// F12 / F13 / F14 / M6 pages were shipped with empty-state renders only.
// This script overlays a small synthetic fixture set on top of the real
// data/ tree, runs the Astro build, greps dist/ HTML for the elements
// that should exist when data is present, then restores the original
// data files.  Nothing here mutates data/ permanently — even on
// assertion failure the finally-block puts everything back.
//
// Run:
//   node scripts/verify-populated.mjs
//
// Exit code 0 = all assertions passed; non-zero = failure with detail
// printed to stderr.

import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), "..");
const BACKUP_ROOT = path.join(REPO_ROOT, ".verify-backup");
const FIXTURES_ROOT = path.join(REPO_ROOT, "fixtures");

// [fixture path relative to fixtures/, target path relative to repo root]
const OVERLAYS = [
  ["themes/rolling.json", "data/themes/rolling.json"],
  ["predictions/registry.json", "data/predictions/registry.json"],
  ["models/index.json", "data/models/index.json"],
  ["reports/2026-Q2.json", "data/reports/2026-Q2.json"],
];

// [dist file relative to site/dist/, substring that MUST appear]
const ASSERTIONS = [
  ["themes/index.html", "테스트 서사"],
  ["themes/index.html", "이번 주 흐름"],
  ["predictions/index.html", "테스트 예측"],
  ["predictions/index.html", "확인됨"],
  ["predictions/index.html", "반박됨"],
  ["compare/index.html", "TestModel-GPT5-v1"],
  ["compare/index.html", "TestModel-Claude-v1"],
  ["reports/index.html", "테스트 리포트 헤드라인"],
  ["reports/2026-Q2/index.html", "커버리지"],
  ["reports/2026-Q2/index.html", "부분 커버리지"],
];

function log(msg) {
  console.log(`[verify] ${msg}`);
}

function overlayFixtures() {
  const restore = [];
  fs.mkdirSync(BACKUP_ROOT, { recursive: true });
  for (const [rel, dest] of OVERLAYS) {
    const src = path.join(FIXTURES_ROOT, rel);
    if (!fs.existsSync(src)) throw new Error(`missing fixture: ${src}`);
    const destAbs = path.join(REPO_ROOT, dest);
    const backupAbs = path.join(BACKUP_ROOT, dest);
    fs.mkdirSync(path.dirname(backupAbs), { recursive: true });
    const existed = fs.existsSync(destAbs);
    if (existed) {
      fs.copyFileSync(destAbs, backupAbs);
    }
    fs.mkdirSync(path.dirname(destAbs), { recursive: true });
    fs.copyFileSync(src, destAbs);
    restore.push({ destAbs, backupAbs, existed });
    log(`overlay ${dest} ${existed ? "(backed up)" : "(new)"}`);
  }
  return restore;
}

function restore(restoreList) {
  for (const { destAbs, backupAbs, existed } of restoreList) {
    if (existed) {
      fs.copyFileSync(backupAbs, destAbs);
    } else if (fs.existsSync(destAbs)) {
      fs.unlinkSync(destAbs);
    }
  }
  fs.rmSync(BACKUP_ROOT, { recursive: true, force: true });
  log("restored original data files");
}

function runBuild() {
  log("running npm run build …");
  execSync("npm run build", {
    cwd: path.join(REPO_ROOT, "site"),
    stdio: "inherit",
  });
}

function assertContains(distRel, needle) {
  const p = path.join(REPO_ROOT, "site", "dist", distRel);
  if (!fs.existsSync(p)) {
    throw new Error(`missing dist file: ${distRel}`);
  }
  const content = fs.readFileSync(p, "utf8");
  if (!content.includes(needle)) {
    throw new Error(`assertion failed: "${needle}" not in ${distRel}`);
  }
  log(`✓ ${distRel} contains "${needle}"`);
}

let restoreList = null;
let failed = false;
try {
  restoreList = overlayFixtures();
  runBuild();
  for (const [distRel, needle] of ASSERTIONS) {
    assertContains(distRel, needle);
  }
  log("all assertions passed");
} catch (err) {
  failed = true;
  console.error(`\n[verify] FAILED: ${err.message}`);
} finally {
  if (restoreList) restore(restoreList);
}
process.exit(failed ? 1 : 0);
