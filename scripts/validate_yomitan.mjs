#!/usr/bin/env node
// Validate a built Yomitan ZIP against the pinned official schemas.
//
// Usage: node scripts/validate_yomitan.mjs <path-to-dictionary.zip>
//
// Checks:
//   - data JSON, index.json, styles.css and the LICENSE notices are at the ZIP
//     root (Yomitan requires bank JSON at the root); the only permitted
//     subfolder members are bundled media assets under kanjivg/*.svg
//   - index.json and every sequential term/term-meta bank validate against
//     schemas/*.json using JSON Schema draft-07 (ajv)
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
  "styles.css",
  "LICENSE-data.txt",
];

// Native kanji banks must NOT ship: Yomitan's kanji-click flow routes to the
// unstyleable native renderer, which would silently supersede the rich
// structured term card on the user's ordinary lookup path. The structured
// single-character term entry is the one canonical surface.
const FORBIDDEN_BANK = /^(?:kanji_bank|kanji_meta_bank)_[1-9][0-9]*\.json$/;

const SCHEMA_FOR = {
  "index.json": "dictionary-index-schema.json",
};

const BANK_GROUPS = [
  {
    label: "term bank",
    pattern: /^term_bank_([1-9][0-9]*)\.json$/,
    schema: "dictionary-term-bank-v3-schema.json",
    entryLimit: 1000,
  },
  {
    label: "term meta bank",
    pattern: /^term_meta_bank_([1-9][0-9]*)\.json$/,
    schema: "dictionary-term-meta-bank-v3-schema.json",
    entryLimit: null,
  },
];

function loadSchema(name) {
  return JSON.parse(readFileSync(join(schemaDir, name), "utf-8"));
}

const zip = new AdmZip(zipPath);
const names = zip.getEntries().map((e) => e.entryName);
const nameSet = new Set(names);

let ok = true;

// Only media assets may live in a subfolder; everything else is root-only.
// Permitted media: sanitized KanjiVG stroke SVGs and per-entry
// rank-derived Frequency weight PNG charts.
const MEDIA_MEMBER = /^(?:kanjivg\/[0-9a-f]+\.svg|reading-frequency\/[0-9a-f]+\.png)$/;
for (const name of names) {
  if (name.includes("/") && !MEDIA_MEMBER.test(name)) {
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
for (const forbidden of names.filter((name) => FORBIDDEN_BANK.test(name))) {
  if (nameSet.has(forbidden)) {
    console.error(
      `FAIL: forbidden native kanji bank present: ${forbidden} ` +
        `(would route the kanji-click flow to the unstyleable native renderer)`
    );
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

// Fail fast within each invalid schema branch. This preserves complete draft-07
// validation while avoiding enormous discarded error arrays for the many oneOf
// alternatives in a production structured-content bank.
const ajv = new Ajv({ allErrors: false, strict: false });
const parsedMembers = new Map();

for (const [member, schemaName] of Object.entries(SCHEMA_FOR)) {
  if (!nameSet.has(member)) continue;
  const data = JSON.parse(zip.readAsText(member));
  parsedMembers.set(member, data);
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

const bankMembers = new Map();
for (const group of BANK_GROUPS) {
  const members = names
    .map((name) => {
      const match = group.pattern.exec(name);
      return match ? { name, number: Number(match[1]) } : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.number - b.number);
  bankMembers.set(group.label, members);

  if (members.length === 0) {
    console.error(`FAIL: no ${group.label} members found`);
    ok = false;
    continue;
  }
  const expectedNumbers = members.map((_member, index) => index + 1);
  if (members.some((member, index) => member.number !== expectedNumbers[index])) {
    console.error(
      `FAIL: ${group.label} members are not contiguous from 1: ` +
        members.map((member) => member.name).join(", ")
    );
    ok = false;
  }

  const validate = ajv.compile(loadSchema(group.schema));
  for (const [index, member] of members.entries()) {
    const data = JSON.parse(zip.readAsText(member.name));
    parsedMembers.set(member.name, data);
    if (!validate(data)) {
      ok = false;
      console.error(`FAIL: ${member.name} does not match ${group.schema}`);
      for (const err of validate.errors.slice(0, 5)) {
        console.error(`   ${err.instancePath} ${err.message}`);
      }
      continue;
    }
    if (group.entryLimit !== null) {
      const isFinal = index === members.length - 1;
      if (
        data.length === 0 ||
        data.length > group.entryLimit ||
        (!isFinal && data.length !== group.entryLimit)
      ) {
        console.error(
          `FAIL: ${member.name} has ${data.length} entries; ` +
            `non-final banks must have ${group.entryLimit} and the final bank 1-${group.entryLimit}`
        );
        ok = false;
        continue;
      }
    }
    console.log(`OK: ${member.name} (${data.length} entries) matches ${group.schema}`);
  }
}

// Every referenced structured-content img path must resolve to a bundled asset.
const termBanks = bankMembers.get("term bank") || [];
if (termBanks.length > 0) {
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
  for (const member of termBanks) {
    walk(parsedMembers.get(member.name));
  }
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
