#!/usr/bin/env node
import fs from "node:fs";
import process from "node:process";
import { markdownToBlocks } from "@tryfabric/martian";

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

const filePath = process.argv[2];
if (!filePath) {
  fail("Usage: node scripts/martian_to_blocks.mjs <markdown-file>");
}

try {
  const markdown = fs.readFileSync(filePath, "utf8");
  const blocks = markdownToBlocks(markdown);
  process.stdout.write(JSON.stringify({ blocks }, null, 2));
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  fail(`Martian convert error: ${message}`);
}
