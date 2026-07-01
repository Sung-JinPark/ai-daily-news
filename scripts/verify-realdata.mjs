#!/usr/bin/env node
// Real-data smoke verification (X2).
//
// Complement to scripts/verify-populated.mjs: instead of overlaying
// synthetic fixtures, this script builds the site against the *current*
// data/ tree and asserts that each new-feature page rendered at least
// its title and one indicative element. It is intended to run right
// after the first live CI pipeline populates themes / predictions /
// models / quarterly reports, so that a schema drift or a missing
// upstream field surfaces immediately instead of showing up as an
// empty page on the deployed site.
//
// For every page:
//   - If the underlying data file is missing, the assertion is SKIPPED
//     with a log line explaining which upstream step has not run yet
//     (so this script is safe to run against a partly-populated tree).
//   - If the file is present but the rendered HTML lacks the expected
//     marker, the script exits with code 1 and prints the marker + the
//     relative dist path so the schema drift is easy to locate.
//
// Run:
//   node scripts/verify-realdata.mjs

import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), "..");

function log(msg) {
  console.log(`[real] ${msg}`);
}

// { data file (relative to repo), dist file (relative to site/dist),
//   markers (each substring must appear in the dist file) }
const CHECKS = [
  {
    data: "data/themes/rolling.json",
    dist: "themes/index.html",
    markers: ["이번 주 흐름"],
    upstream: "pipeline.themes",
  },
  {
    data: "data/themes/rolling.json",
    dist: "index.html",
    markers: ["이번 주 흐름"],  // homepage strip lives on /
    upstream: "pipeline.themes (homepage strip)",
  },
  {
    data: "data/predictions/registry.json",
    dist: "predictions/index.html",
    markers: ["예측 트래커"],
    upstream: "pipeline.predict_extract",
  },
  {
    data: "data/models/index.json",
    dist: "compare/index.html",
    markers: ["모델 비교"],
    upstream: "pipeline.model_facts",
  },
  {
    data: "data/reports/2026-Q2.json",
    dist: "reports/2026-Q2/index.html",
    markers: ["커버리지"],
    upstream: "pipeline.quarterly_report",
  },
];

function fileExists(rel) {
  return fs.existsSync(path.join(REPO_ROOT, rel));
}

function ensureBuild() {
  log("running npm run build …");
  execSync("npm run build", {
    cwd: path.join(REPO_ROOT, "site"),
    stdio: "inherit",
  });
}

function assertOne(check) {
  const dataAbs = path.join(REPO_ROOT, check.data);
  if (!fs.existsSync(dataAbs)) {
    log(`SKIP ${check.dist} — ${check.data} missing (upstream ${check.upstream} has not run)`);
    return { skipped: true };
  }
  const distAbs = path.join(REPO_ROOT, "site", "dist", check.dist);
  if (!fs.existsSync(distAbs)) {
    throw new Error(`missing dist file: ${check.dist}`);
  }
  const content = fs.readFileSync(distAbs, "utf8");
  for (const m of check.markers) {
    if (!content.includes(m)) {
      throw new Error(`marker "${m}" not found in ${check.dist} (${check.upstream} produced ${check.data} but the page render is missing the expected element — likely a schema drift)`);
    }
  }
  log(`✓ ${check.dist} matches ${check.markers.length} marker(s)`);
  return { skipped: false };
}

let failed = false;
try {
  ensureBuild();
  let ran = 0;
  let skipped = 0;
  for (const c of CHECKS) {
    const r = assertOne(c);
    if (r.skipped) skipped++;
    else ran++;
  }
  log(`summary: ${ran} passed, ${skipped} skipped (upstream not yet run)`);
} catch (err) {
  failed = true;
  console.error(`\n[real] FAILED: ${err.message}`);
}
process.exit(failed ? 1 : 0);
