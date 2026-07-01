#!/usr/bin/env node
// Overview counts — replaces hand-tallying of "new pages / modules /
// components / streams" in review overview documents. Walks the working
// tree and prints a table; the operator compares to the previous
// snapshot instead of counting by eye.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), "..");

function walk(dir, filter) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walk(full, filter));
    } else if (filter(full)) {
      out.push(full);
    }
  }
  return out;
}

function relOf(files) {
  return files.map((f) => path.relative(REPO_ROOT, f).replace(/\\/g, "/"));
}

const buckets = {
  "site pages (.astro + .json.ts + .xml.ts)": walk(
    path.join(REPO_ROOT, "site/src/pages"),
    (f) => /\.(astro|json\.ts|xml\.ts)$/.test(f),
  ),
  "site components (.astro)": walk(
    path.join(REPO_ROOT, "site/src/components"),
    (f) => f.endsWith(".astro"),
  ),
  "site libs (.ts)": walk(
    path.join(REPO_ROOT, "site/src/lib"),
    (f) => f.endsWith(".ts"),
  ),
  "pipeline modules (.py)": walk(
    path.join(REPO_ROOT, "pipeline"),
    (f) => f.endsWith(".py") && !f.includes("__pycache__"),
  ),
  "data aggregates (.jsonl)": walk(
    path.join(REPO_ROOT, "data/aggregates"),
    (f) => f.endsWith(".jsonl"),
  ),
  "data corpus days": walk(
    path.join(REPO_ROOT, "data/corpus"),
    (f) => path.basename(f) === "bodies.jsonl",
  ),
  "review markdown files": walk(
    path.join(REPO_ROOT, "review/review-2026-07-01"),
    (f) => f.endsWith(".md"),
  ),
  "reviews (persistent) markdown": walk(
    path.join(REPO_ROOT, "reviews"),
    (f) => f.endsWith(".md"),
  ),
  "notebooks (.ipynb)": walk(
    path.join(REPO_ROOT, "notebooks"),
    (f) => f.endsWith(".ipynb"),
  ),
  "workflows": walk(
    path.join(REPO_ROOT, ".github/workflows"),
    (f) => f.endsWith(".yml"),
  ),
};

let maxLabel = 0;
for (const label of Object.keys(buckets)) {
  if (label.length > maxLabel) maxLabel = label.length;
}

console.log(`Overview counts — ${new Date().toISOString().slice(0, 10)}\n`);
console.log(`${"label".padEnd(maxLabel)}  count  files`);
console.log(`${"-".repeat(maxLabel)}  -----  -----`);
for (const [label, files] of Object.entries(buckets)) {
  const n = files.length;
  const first = relOf(files.slice(0, 2)).join(", ") + (files.length > 2 ? ", …" : "");
  console.log(`${label.padEnd(maxLabel)}  ${String(n).padStart(5)}  ${first}`);
}
