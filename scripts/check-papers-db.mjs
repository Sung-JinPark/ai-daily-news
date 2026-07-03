#!/usr/bin/env node
// Readiness check for the private arXiv-paper SQLite DB at
// data/papers_private/papers.db. Supersedes the earlier manual-drop
// scaffold check (scripts/check-papers-structure.mjs — removed).
//
// This script does NOT scaffold data — the DB is materialized by
// `python -m pipeline.collect_papers`. It only verifies:
//
//   1. No git leak (whole tree gitignored + zero tracked files under it).
//   2. papers.db exists, opens, and holds the expected schema.
//   3. papers <-> paper_mentions counts + enrichment coverage.
//   4. Integrity: enriched rows have abstract/authors, arxiv_id looks
//      like an arxiv id, and any pdf_path column points at a real file.
//   5. meta.last_run bookkeeping is reachable.
//
// Run:  node scripts/check-papers-db.mjs
// Exit: 0 when every check PASSes; 1 as soon as one FAILs.

import fs from "node:fs";
import path from "node:path";
import { execSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), "..");
const PRIVATE_ROOT = path.join(REPO_ROOT, "data", "papers_private");
const DB_PATH = path.join(PRIVATE_ROOT, "papers.db");
const PDF_DIR = path.join(PRIVATE_ROOT, "pdf");

const results = [];
function record(check, ok, detail = "") {
  results.push({ check, ok, detail });
}

// --- git leak guard ---

function checkGitLeak() {
  let ignored = false;
  let detail = "";
  try {
    execSync("git check-ignore data/papers_private", {
      cwd: REPO_ROOT,
      stdio: ["ignore", "pipe", "pipe"],
    });
    ignored = true;
  } catch {
    detail = ".gitignore does NOT hide data/papers_private/";
  }
  record("leak: tree gitignored", ignored, detail);

  let tracked = "";
  try {
    tracked = execSync("git ls-files data/papers_private/", {
      cwd: REPO_ROOT,
      encoding: "utf-8",
    }).trim();
  } catch (e) {
    tracked = `git ls-files failed: ${e.message}`;
  }
  record("leak: no tracked files", tracked === "", tracked || "");
}

// --- python-driven DB inspection ---
//
// SQLite access from Node without bringing a native better-sqlite3
// dependency: shell out to the project's Python (which already has
// stdlib sqlite3) with a JSON dump script. This mirrors the
// project's other .mjs verifiers that use execSync.

const INSPECT_PY = `
import json, sqlite3, os, sys
db = sys.argv[1]
if not os.path.exists(db):
    print(json.dumps({"exists": False}))
    sys.exit(0)
try:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
except Exception as e:
    print(json.dumps({"exists": True, "openable": False, "error": str(e)}))
    sys.exit(0)
try:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts = {}
    for t in ("papers", "paper_mentions", "meta"):
        if t in tables:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    schema_version = None
    last_run = None
    if "meta" in tables:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        schema_version = row[0] if row else None
        row = conn.execute("SELECT value FROM meta WHERE key='last_run'").fetchone()
        last_run = row[0] if row else None
    enriched = 0
    orphan_meta = 0
    bad_arxiv = []
    missing_pdf = []
    kind_counts = {}
    has_kind_col = False
    if "paper_mentions" in tables:
        has_kind_col = any(
            r[1] == "mention_kind"
            for r in conn.execute("PRAGMA table_info(paper_mentions)")
        )
        if has_kind_col:
            for k, n in conn.execute("SELECT mention_kind, COUNT(*) FROM paper_mentions GROUP BY mention_kind"):
                kind_counts[k] = n
    if "papers" in tables:
        enriched = conn.execute("SELECT COUNT(*) FROM papers WHERE enriched=1").fetchone()[0]
        orphan_meta = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE enriched=1 AND (abstract IS NULL OR abstract='' OR authors_json IS NULL OR authors_json='[]')"
        ).fetchone()[0]
        rows = conn.execute("SELECT arxiv_id FROM papers").fetchall()
        import re
        rx = re.compile(r"^(\\d{4}\\.\\d{4,5}|[a-z\\-]+(?:\\.[A-Z]{2})?/\\d{7})$")
        for (aid,) in rows:
            if not rx.match(aid or ""):
                bad_arxiv.append(aid)
        for (aid, pdfp) in conn.execute("SELECT arxiv_id, pdf_path FROM papers WHERE pdf_path IS NOT NULL AND pdf_path <> ''"):
            if not os.path.exists(pdfp):
                missing_pdf.append(aid)
    print(json.dumps({
        "exists": True, "openable": True,
        "tables": sorted(tables),
        "counts": counts,
        "schema_version": schema_version,
        "last_run": last_run,
        "enriched": enriched,
        "orphan_enriched": orphan_meta,
        "bad_arxiv": bad_arxiv,
        "missing_pdf": missing_pdf,
        "has_kind_col": has_kind_col,
        "kind_counts": kind_counts,
    }))
finally:
    conn.close()
`;

function inspectDb() {
  const out = spawnSync("python", ["-c", INSPECT_PY, DB_PATH], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  if (out.status !== 0) {
    return { exists: fs.existsSync(DB_PATH), openable: false, error: out.stderr || "python inspect failed" };
  }
  try {
    return JSON.parse(out.stdout.trim());
  } catch (e) {
    return { exists: fs.existsSync(DB_PATH), openable: false, error: `bad JSON: ${e.message} :: ${out.stdout}` };
  }
}

function checkDb(state) {
  record("db: papers.db exists", !!state.exists, state.exists ? DB_PATH : "run `python -m pipeline.collect_papers` first");
  if (!state.exists) return;
  record("db: opens", !!state.openable, state.error || "");
  if (!state.openable) return;
  for (const t of ["papers", "paper_mentions", "meta"]) {
    record(`db: table ${t}`, state.tables.includes(t));
  }
  record(
    "db: schema_version=2",
    String(state.schema_version) === "2",
    `got ${state.schema_version}`,
  );
  record(
    "db: paper_mentions.mention_kind column",
    !!state.has_kind_col,
    state.has_kind_col ? "" : "v1 DB - run `python -m pipeline.collect_papers` to migrate",
  );
  record(
    "db: meta.last_run present",
    !!state.last_run,
    state.last_run ? "" : "no last_run row yet - run collect once",
  );
  record(
    "integrity: enriched rows have abstract/authors",
    state.orphan_enriched === 0,
    state.orphan_enriched ? `${state.orphan_enriched} enriched rows missing metadata` : "",
  );
  record(
    "integrity: arxiv_id format",
    state.bad_arxiv.length === 0,
    state.bad_arxiv.length ? `bad: ${state.bad_arxiv.slice(0, 5).join(", ")}` : "",
  );
  record(
    "integrity: pdf_path files exist",
    state.missing_pdf.length === 0,
    state.missing_pdf.length ? `missing: ${state.missing_pdf.slice(0, 5).join(", ")}` : "",
  );
}

function countPdfs() {
  if (!fs.existsSync(PDF_DIR)) return 0;
  return fs.readdirSync(PDF_DIR).filter((f) => f.toLowerCase().endsWith(".pdf")).length;
}

function printReport(state) {
  const pad = (s, n) => String(s).padEnd(n);
  const width = Math.max(...results.map((r) => r.check.length), 20);
  console.log(`papers.db readiness check - ${new Date().toISOString().slice(0, 19)}Z\n`);
  console.log(`db:  ${path.relative(REPO_ROOT, DB_PATH).replace(/\\/g, "/")}`);
  console.log(`pdf: ${path.relative(REPO_ROOT, PDF_DIR).replace(/\\/g, "/")}\n`);
  console.log(`${pad("check", width)}  status  detail`);
  console.log(`${"-".repeat(width)}  ------  ------`);
  for (const r of results) {
    const status = r.ok ? "PASS" : "FAIL";
    console.log(`${pad(r.check, width)}  ${status.padEnd(6)}  ${r.detail}`);
  }
  console.log("");
  if (state.openable) {
    console.log("counts:");
    console.log(`  papers          ${state.counts.papers ?? 0}`);
    console.log(`  paper_mentions  ${state.counts.paper_mentions ?? 0}`);
    for (const [k, n] of Object.entries(state.kind_counts ?? {})) {
      console.log(`    kind=${k.padEnd(9)} ${n}`);
    }
    console.log(`  enriched        ${state.enriched}`);
    console.log(`  pdf files       ${countPdfs()}`);
    if (state.last_run) {
      try {
        const lr = JSON.parse(state.last_run);
        console.log(`  last_run        ${lr.at}`);
      } catch {
        console.log(`  last_run        ${state.last_run}`);
      }
    }
  }
  console.log("");
  console.log("next: enrich more (--sleep 3), or run --with-pdf to snapshot PDFs, or move on to text extraction (next session).");
}

// E4 (AUD-019): research-artifact surface checks. Everything here is
// local/private or day-file based — a missing artifact SKIPs (other
// machines / CI won't have the private tree), a present-but-broken one
// FAILs.
function checkResearchArtifacts() {
  // 1. arxiv_refs.json — newest day that has one: schema + deterministic sort
  const dataDir = path.join(REPO_ROOT, "data");
  const days = fs.readdirSync(dataDir).filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)).sort().reverse();
  const refDay = days.find((d) => fs.existsSync(path.join(dataDir, d, "arxiv_refs.json")));
  if (!refDay) {
    console.log("  SKIP arxiv_refs (no day file yet)");
  } else {
    try {
      const refs = JSON.parse(fs.readFileSync(path.join(dataDir, refDay, "arxiv_refs.json"), "utf-8"));
      record("arxiv_refs schema", typeof refs.schema_version === "number" && Array.isArray(refs.refs),
        `${refDay}: schema_version=${refs.schema_version} rows=${refs.refs?.length}`);
      const keys = (refs.refs ?? []).map((r) => `${r.article_id} ${r.arxiv_id}`);
      const sorted = [...keys].sort();
      record("arxiv_refs deterministic sort", JSON.stringify(keys) === JSON.stringify(sorted),
        `${refDay}: ${keys.length} rows`);
    } catch (e) {
      record("arxiv_refs schema", false, String(e));
    }
  }

  // 2. db_exports — newest cold checkpoints: integrity + retention policy
  const dbxDir = path.join(REPO_ROOT, "data", "research_private", "db_exports");
  if (!fs.existsSync(dbxDir)) {
    console.log("  SKIP db_exports (private tree absent on this machine)");
  } else {
    for (const prefix of ["papers", "research"]) {
      const files = fs.readdirSync(dbxDir).filter((f) => f.startsWith(`${prefix}-`) && f.endsWith(".db")).sort();
      if (files.length === 0) {
        record(`db_exports ${prefix} checkpoint exists`, false, "none found");
        continue;
      }
      const newest = path.join(dbxDir, files[files.length - 1]);
      let ok = false;
      try {
        ok = execSync(`python -c "import sqlite3;print(sqlite3.connect(r'${newest.replace(/\\/g, "/")}').execute('PRAGMA integrity_check').fetchone()[0])"`,
          { encoding: "utf-8" }).trim() === "ok";
      } catch { /* fall through */ }
      record(`db_exports ${prefix} integrity`, ok, files[files.length - 1]);
      // retention: >7-day-old non-Monday files should have been pruned
      const stale = files.filter((f) => {
        const m = f.match(/-(\d{4}-\d{2}-\d{2})\.db$/);
        if (!m) return false;
        const d = new Date(m[1] + "T00:00:00Z");
        const age = (Date.now() - d.getTime()) / 86400000;
        return age > 8 && d.getUTCDay() !== 1;
      });
      record(`db_exports ${prefix} retention`, stale.length === 0,
        stale.length ? `stale: ${stale.join(", ")}` : `${files.length} file(s), policy holds`);
    }
  }

  // 3. weekly brief — newest one carries both DB status lines
  const briefDir = path.join(REPO_ROOT, "data", "research_private", "briefs");
  const briefs = fs.existsSync(briefDir) ? fs.readdirSync(briefDir).filter((f) => f.endsWith(".md")).sort() : [];
  if (briefs.length === 0) {
    console.log("  SKIP briefs (private tree absent or none written yet)");
  } else {
    const txt = fs.readFileSync(path.join(briefDir, briefs[briefs.length - 1]), "utf-8");
    record("brief has paper-DB status line", txt.includes("논문 DB 상태"), briefs[briefs.length - 1]);
    record("brief has research-DB status line", txt.includes("연구 DB 상태"), briefs[briefs.length - 1]);
  }

  // 4. dashboard.html — exists, has section markers, reasonably fresh
  const dash = path.join(REPO_ROOT, "data", "research_private", "dashboard.html");
  if (!fs.existsSync(dash)) {
    console.log("  SKIP dashboard (private tree absent)");
  } else {
    const html = fs.readFileSync(dash, "utf-8");
    record("dashboard section markers",
      html.includes("논문 DB") && html.includes("개념 원장") && html.includes("백업"),
      "papers/concepts/backups sections");
    const ageH = (Date.now() - fs.statSync(dash).mtimeMs) / 3600000;
    record("dashboard freshness (<48h)", ageH < 48, `${ageH.toFixed(1)}h old`);
  }
}

checkGitLeak();
const state = inspectDb();
checkDb(state);
checkResearchArtifacts();
printReport(state);

const failed = results.filter((r) => !r.ok);
if (failed.length > 0) {
  console.error(`\n${failed.length} check(s) FAILED.`);
  process.exit(1);
}
console.log("all checks PASSED.");
