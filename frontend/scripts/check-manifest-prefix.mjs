import fs from 'node:fs';
import path from 'node:path';

function walkFiles(rootDir) {
  const out = [];
  const stack = [rootDir];
  while (stack.length) {
    const current = stack.pop();
    if (!current || !fs.existsSync(current)) continue;
    const stat = fs.statSync(current);
    if (!stat.isDirectory()) continue;
    for (const entry of fs.readdirSync(current, {withFileTypes: true})) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
        continue;
      }
      if (entry.isFile() && full.endsWith('page_client-reference-manifest.js')) {
        out.push(full);
      }
    }
  }
  return out.sort();
}

function getPrefix(absPath) {
  const normalized = String(absPath || '').replace(/\\/g, '/');
  if (!normalized.startsWith('/')) return '';
  const parts = normalized.split('/').filter(Boolean);
  if (!parts.length) return '/';
  return `/${parts[0]}`;
}

function pathKeysFromManifest(filePath) {
  const src = fs.readFileSync(filePath, 'utf-8');
  const matches = src.match(/"\/[^"]+"\s*:/g) || [];
  return matches
    .map((item) => item.replace(/"\s*:\s*$/g, '').replace(/^"/, ''))
    .map((item) => item.replace(/"$/, ''))
    .filter((item) => item.startsWith('/'));
}

function main() {
  const distDir = process.argv[2] || process.env.NEXT_DIST_DIR || '.next-runtime';
  const expectedPrefix = process.argv[3] || process.env.MANIFEST_PREFIX_EXPECTED || '';
  const target = path.resolve(process.cwd(), distDir, 'server', 'app');
  const manifestFiles = walkFiles(target);

  if (!manifestFiles.length) {
    console.error(`[manifest-check] no manifest files found in ${target}`);
    process.exit(2);
  }

  const prefixes = new Set();
  for (const filePath of manifestFiles) {
    for (const key of pathKeysFromManifest(filePath)) {
      const prefix = getPrefix(key);
      if (prefix) prefixes.add(prefix);
    }
  }

  const values = Array.from(prefixes).sort();
  if (!values.length) {
    console.error(`[manifest-check] no absolute path keys found in ${manifestFiles.length} manifest files`);
    process.exit(3);
  }

  if (values.length > 1) {
    console.error(`[manifest-check] mixed path prefixes detected: ${values.join(', ')}`);
    process.exit(4);
  }

  if (expectedPrefix && values[0] !== expectedPrefix) {
    console.error(`[manifest-check] prefix mismatch: expected ${expectedPrefix}, got ${values[0]}`);
    process.exit(5);
  }

  console.log(`[manifest-check] ok: ${values[0]} (${manifestFiles.length} files, dist=${distDir})`);
}

main();
