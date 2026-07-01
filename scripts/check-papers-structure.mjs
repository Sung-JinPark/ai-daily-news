#!/usr/bin/env node
// Structure-readiness check for the private local paper corpus at
// data/papers_private/. Scope of this script is deliberately narrow:
//
//   1. Ensure the required directories + template index.json/README
//      exist on the researcher's machine (idempotent scaffold).
//   2. Verify that .gitignore actually hides the whole tree AND that
//      git has zero tracked files under it — this is the leak guard.
//   3. Sanity-check the manifest, pdf<->meta pairing, and naming
//      conventions so the future ingest step has a clean starting
//      point.
//
// It does NOT: download PDFs, parse metadata, extract text, or call any
// external API. Those live in the next-session ingest pipeline.
//
// Run:  node scripts/check-papers-structure.mjs
// Exit: 0 when every check PASSes; 1 as soon as one FAILs.

import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), "..");
const ROOT = path.join(REPO_ROOT, "data", "papers_private");
const SUBDIRS = ["inbox", "pdf", "meta", "text"];
const INDEX_PATH = path.join(ROOT, "index.json");
const README_PATH = path.join(ROOT, "README.md");

const README_BODY = `# data/papers_private/ — local reference paper corpus

Scope: this tree is **local-only and gitignored**. It holds reference
papers (PDF + metadata + extracted text) that inform the AI-trend
paper's literature review. Do not commit anything under this path.

## Layout

- \`inbox/\`  — drop new PDFs here; ingest picks them up next run.
- \`pdf/\`    — canonical PDFs, filename = \`<key>.pdf\`.
- \`meta/\`   — one JSON per paper, filename = \`<key>.json\`.
- \`text/\`   — plaintext extraction (populated by the next-session ingest).
- \`index.json\` — full manifest.
- \`README.md\` — this file.

## Stable key

- If the paper has an \`arxiv_id\` (e.g. \`2606.23662\`), that is the key.
- Otherwise: \`sha256(pdf bytes)[:16]\` — matches the repo convention
  used by \`pipeline/state.py:url_hash\`.

## Verification

\`node scripts/check-papers-structure.mjs\` must exit 0 before ingest.
It also guards against accidental git leak of the private tree.

## Next step

Drop reference PDFs into \`inbox/\` and (in the next session) run the
ingest script that will compute keys, fill \`meta/<key>.json\`, extract
text into \`text/<key>.txt\`, and update \`index.json\`.
`;

const results = [];
function record(check, ok, detail = "") {
  results.push({ check, ok, detail });
}

function ensureScaffold() {
  fs.mkdirSync(ROOT, { recursive: true });
  for (const d of SUBDIRS) {
    fs.mkdirSync(path.join(ROOT, d), { recursive: true });
  }
  if (!fs.existsSync(INDEX_PATH)) {
    const template = {
      schema_version: 1,
      generated_at: new Date().toISOString(),
      count: 0,
      entries: [],
    };
    fs.writeFileSync(INDEX_PATH, JSON.stringify(template, null, 2) + "\n", "utf-8");
  }
  if (!fs.existsSync(README_PATH)) {
    fs.writeFileSync(README_PATH, README_BODY, "utf-8");
  }
}

function checkStructure() {
  for (const d of SUBDIRS) {
    const p = path.join(ROOT, d);
    record(`structure: ${d}/`, fs.existsSync(p) && fs.statSync(p).isDirectory(), p);
  }
  record("structure: index.json", fs.existsSync(INDEX_PATH), INDEX_PATH);
  record("structure: README.md", fs.existsSync(README_PATH), README_PATH);
}

function checkGitLeak() {
  // (a) The whole tree must be gitignored. `git check-ignore` exits
  //     0 when the path IS ignored, 1 when it is not.
  let ignored = false;
  let checkDetail = "";
  try {
    execSync("git check-ignore data/papers_private", {
      cwd: REPO_ROOT,
      stdio: ["ignore", "pipe", "pipe"],
    });
    ignored = true;
  } catch (e) {
    ignored = false;
    checkDetail = "git check-ignore returned non-zero — .gitignore is NOT hiding data/papers_private/";
  }
  record("leak: gitignored", ignored, checkDetail);

  // (b) No file under the tree may be tracked.
  let tracked = "";
  try {
    tracked = execSync("git ls-files data/papers_private/", {
      cwd: REPO_ROOT,
      encoding: "utf-8",
    }).trim();
  } catch (e) {
    tracked = `git ls-files failed: ${e.message}`;
  }
  record(
    "leak: no tracked files",
    tracked === "",
    tracked === "" ? "" : `git ls-files reported: ${tracked}`,
  );
}

function checkManifest() {
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(INDEX_PATH, "utf-8"));
  } catch (e) {
    record("manifest: valid JSON", false, `parse error: ${e.message}`);
    return null;
  }
  record("manifest: valid JSON", true);
  const required = ["schema_version", "generated_at", "count", "entries"];
  for (const key of required) {
    record(`manifest: field ${key}`, Object.prototype.hasOwnProperty.call(manifest, key));
  }
  record(
    "manifest: entries is array",
    Array.isArray(manifest.entries),
    Array.isArray(manifest.entries) ? "" : `got ${typeof manifest.entries}`,
  );
  return manifest;
}

function listStems(dir, ext) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(ext))
    .map((f) => f.slice(0, -ext.length));
}

function checkPairing() {
  const pdfKeys = new Set(listStems(path.join(ROOT, "pdf"), ".pdf"));
  const metaKeys = new Set(listStems(path.join(ROOT, "meta"), ".json"));

  const orphanPdf = [...pdfKeys].filter((k) => !metaKeys.has(k));
  const orphanMeta = [...metaKeys].filter((k) => !pdfKeys.has(k));

  record(
    "pairing: pdf has meta",
    orphanPdf.length === 0,
    orphanPdf.length ? `orphan PDFs: ${orphanPdf.join(", ")}` : "",
  );
  record(
    "pairing: meta has pdf",
    orphanMeta.length === 0,
    orphanMeta.length ? `orphan metas: ${orphanMeta.join(", ")}` : "",
  );
  return { pdfKeys, metaKeys };
}

function checkNaming(metaKeys) {
  const arxivRe = /^\d{4}\.\d{4,5}(v\d+)?$/;
  const mismatches = [];
  const badArxiv = [];
  for (const key of metaKeys) {
    const metaPath = path.join(ROOT, "meta", `${key}.json`);
    let m;
    try {
      m = JSON.parse(fs.readFileSync(metaPath, "utf-8"));
    } catch (e) {
      mismatches.push(`${key} (unparseable: ${e.message})`);
      continue;
    }
    if (m.id !== key) mismatches.push(`${key} (id=${m.id})`);
    if (m.arxiv_id && !arxivRe.test(m.arxiv_id)) badArxiv.push(`${key} (arxiv_id=${m.arxiv_id})`);
  }
  record(
    "naming: meta.id matches filename",
    mismatches.length === 0,
    mismatches.length ? mismatches.join("; ") : "",
  );
  record(
    "naming: arxiv_id format sane",
    badArxiv.length === 0,
    badArxiv.length ? badArxiv.join("; ") : "",
  );
}

function checkCounts(manifest, keys) {
  const pdfN = keys.pdfKeys.size;
  const metaN = keys.metaKeys.size;
  const textN = listStems(path.join(ROOT, "text"), ".txt").length;
  const inboxP = fs.existsSync(path.join(ROOT, "inbox"))
    ? fs.readdirSync(path.join(ROOT, "inbox")).filter((f) => f.toLowerCase().endsWith(".pdf")).length
    : 0;
  const manifestCount = manifest?.count ?? -1;
  record(
    "counts: index.count matches meta count",
    manifestCount === metaN,
    manifestCount === metaN ? "" : `index.count=${manifestCount} meta=${metaN}`,
  );
  return { pdfN, metaN, textN, inboxP, manifestCount };
}

function printReport(counts) {
  const pad = (s, n) => String(s).padEnd(n);
  const width = Math.max(...results.map((r) => r.check.length), 20);
  console.log(`papers_private structure check — ${new Date().toISOString().slice(0, 19)}Z\n`);
  console.log(`root: ${path.relative(REPO_ROOT, ROOT).replace(/\\/g, "/")}\n`);
  console.log(`${pad("check", width)}  status  detail`);
  console.log(`${"-".repeat(width)}  ------  ------`);
  for (const r of results) {
    const status = r.ok ? "PASS" : "FAIL";
    console.log(`${pad(r.check, width)}  ${status.padEnd(6)}  ${r.detail}`);
  }
  if (counts) {
    console.log("");
    console.log("counts:");
    console.log(`  pdf/    ${counts.pdfN}`);
    console.log(`  meta/   ${counts.metaN}`);
    console.log(`  text/   ${counts.textN}`);
    console.log(`  inbox/  ${counts.inboxP} (waiting to ingest)`);
    console.log(`  index.count ${counts.manifestCount}`);
  }
  console.log("");
  console.log("next step: drop reference PDFs into data/papers_private/inbox/ and run the ingest script (planned for the next session).");
}

ensureScaffold();
checkStructure();
checkGitLeak();
const manifest = checkManifest();
const keys = checkPairing();
checkNaming(keys.metaKeys);
const counts = manifest ? checkCounts(manifest, keys) : null;
printReport(counts);

const failed = results.filter((r) => !r.ok);
if (failed.length > 0) {
  console.error(`\n${failed.length} check(s) FAILED. Fix the above before ingest.`);
  process.exit(1);
}
console.log("all checks PASSED.");
