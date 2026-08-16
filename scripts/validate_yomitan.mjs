#!/usr/bin/env node
// Validate a built Yomitan ZIP against the pinned official schemas.
//
// Usage: node scripts/validate_yomitan.mjs <path-to-dictionary.zip>
//
// Checks:
//   - all expected members are present at the ZIP root (no subfolders)
//   - index.json, term/kanji bank, and term/kanji meta bank each validate
//     against schemas/*.json using JSON Schema draft-07 (ajv)
//
// Dependencies: ajv (installed on demand by the workflow / dev setup).

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

const EXPECTED = [
  "index.json",
  "term_bank_1.json",
  "term_meta_bank_1.json",
  "kanji_bank_1.json",
  "kanji_meta_bank_1.json",
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

let ok = true;

// All members present, at the root only.
for (const name of names) {
  if (name.includes("/")) {
    console.error(`FAIL: member not at ZIP root: ${name}`);
    ok = false;
  }
}
for (const want of EXPECTED) {
  if (!names.includes(want)) {
    console.error(`FAIL: missing expected member: ${want}`);
    ok = false;
  }
}

const ajv = new Ajv({ allErrors: true, strict: false });

for (const [member, schemaName] of Object.entries(SCHEMA_FOR)) {
  if (!names.includes(member)) continue;
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

if (!ok) {
  console.error("Yomitan validation FAILED");
  process.exit(1);
}
console.log("Yomitan validation passed");
