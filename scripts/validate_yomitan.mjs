#!/usr/bin/env node
// Validate a built Yomitan ZIP against the pinned official schemas.
//
// Usage: node scripts/validate_yomitan.mjs <path-to-dictionary.zip>
//
// Checks:
//   - data JSON, index.json, styles.css and the LICENSE notices are at the ZIP
//     root (Yomitan requires bank JSON at the root); the only permitted
//     subfolder members are bundled media assets under kanjivg/*.svg
//   - index.json, term/kanji bank, and term/kanji meta bank each validate
//     against schemas/*.json using JSON Schema draft-07 (ajv)
//   - every bundled kanjivg/*.svg is sanitized (no <script>, kvg: attrs, xlink,
//     event handlers, or external <image>)
//   - every structured-content img path referenced by the term bank resolves to
//     a bundled asset (no dangling media references)
//
// Dependencies: ajv, adm-zip (installed on demand by the workflow / dev setup).

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const AdmZip = require("adm-zip");
const Ajv = require("ajv");

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const schemaDir = join(root, "schemas");

const zipPath = process.argv[2];
if (!zipPath) {
  console.error("usage: validate_yomitan.mjs <dictionary.zip>");
  process.exit(2);
}

// Members that MUST live at the ZIP root.
const EXPECTED_ROOT = [
  "index.json",
  "MANIFEST.json",
  "term_bank_1.json",
  "term_meta_bank_1.json",
  "kanji_bank_1.json",
  "kanji_meta_bank_1.json",
  "styles.css",
  "LICENSE-data.txt",
];

const SCHEMA_FOR = {
  "index.json": "dictionary-index-schema.json",
  "term_bank_1.json": "dictionary-term-bank-v3-schema.json",
  "term_meta_bank_1.json": "dictionary-term-meta-bank-v3-schema.json",
  "kanji_bank_1.json": "dictionary-kanji-bank-v3-schema.json",
  "kanji_meta_bank_1.json": "dictionary-kanji-meta-bank-v3-schema.json",
};

function loadSchema(name) {
  return JSON.parse(readFileSync(join(schemaDir, name), "utf-8"));
}

const zip = new AdmZip(zipPath);
const names = zip.getEntries().map((e) => e.entryName);
const nameSet = new Set(names);

let ok = true;

// Only media assets may live in a subfolder; everything else is root-only.
for (const name of names) {
  if (name.includes("/") && !/^kanjivg\/[0-9a-f]+\.svg$/.test(name)) {
    console.error(`FAIL: unexpected non-root member: ${name}`);
    ok = false;
  }
}
for (const want of EXPECTED_ROOT) {
  if (!nameSet.has(want)) {
    console.error(`FAIL: missing expected member: ${want}`);
    ok = false;
  }
}

// Bundled SVG assets must be sanitized and license notice present when shipped.
const svgAssets = names.filter((n) => n.startsWith("kanjivg/"));
if (svgAssets.length > 0 && !nameSet.has("LICENSE-kanjivg.txt")) {
  console.error("FAIL: KanjiVG assets present but LICENSE-kanjivg.txt missing");
  ok = false;
}
const FORBIDDEN = [/<script/i, /kvg:/, /xlink/i, /\son\w+=/i, /<image/i, /<!doctype/i];
for (const asset of svgAssets) {
  const text = zip.readAsText(asset);
  for (const re of FORBIDDEN) {
    if (re.test(text)) {
      console.error(`FAIL: unsanitized content ${re} in ${asset}`);
      ok = false;
    }
  }
}

const ajv = new Ajv({ allErrors: true, strict: false });

for (const [member, schemaName] of Object.entries(SCHEMA_FOR)) {
  if (!nameSet.has(member)) continue;
  const data = JSON.parse(zip.readAsText(member));
  const validate = ajv.compile(loadSchema(schemaName));
  if (!validate(data)) {
    ok = false;
    console.error(`FAIL: ${member} does not match ${schemaName}`);
    for (const err of validate.errors.slice(0, 5)) {
      console.error(`   ${err.instancePath} ${err.message}`);
    }
  } else {
    const count = Array.isArray(data) ? data.length : 1;
    console.log(`OK: ${member} (${count} entries) matches ${schemaName}`);
  }
}

// Every referenced structured-content img path must resolve to a bundled asset.
if (nameSet.has("term_bank_1.json")) {
  const terms = JSON.parse(zip.readAsText("term_bank_1.json"));
  const referenced = new Set();
  const walk = (node) => {
    if (Array.isArray(node)) {
      node.forEach(walk);
    } else if (node && typeof node === "object") {
      if (node.tag === "img" && typeof node.path === "string") {
        referenced.add(node.path);
      }
      for (const v of Object.values(node)) walk(v);
    }
  };
  walk(terms);
  for (const path of referenced) {
    if (!nameSet.has(path)) {
      console.error(`FAIL: dangling img asset reference: ${path}`);
      ok = false;
    }
  }
  console.log(`OK: ${referenced.size} referenced img assets all resolve`);
}

// The source/revision manifest must be present, valid JSON, and carry the core
// provenance fields so a released package is always traceable to its sources.
if (nameSet.has("MANIFEST.json")) {
  try {
    const m = JSON.parse(zip.readAsText("MANIFEST.json"));
    for (const field of ["revision", "contentHash", "buildDate", "sources", "records"]) {
      if (!(field in m)) {
        console.error(`FAIL: MANIFEST.json missing field: ${field}`);
        ok = false;
      }
    }
    if (m.sources && (!m.sources.jiten || !m.sources.kanjidic2)) {
      console.error("FAIL: MANIFEST.json missing source provenance (jiten/kanjidic2)");
      ok = false;
    } else {
      console.log("OK: MANIFEST.json carries revision + source provenance");
    }
  } catch (e) {
    console.error(`FAIL: MANIFEST.json is not valid JSON: ${e.message}`);
    ok = false;
  }
}

if (!ok) {
  console.error("Yomitan validation FAILED");
  process.exit(1);
}
console.log("Yomitan validation passed");
