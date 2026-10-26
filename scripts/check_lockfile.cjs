/* A lockfile entry whose `version` disagrees with the tarball it points at
 * installs something different from what `npm ls` (and an audit) reports.
 * Both esquery@1.8.0 and picocolors@1.1.4 were hand-edited into this file;
 * neither version has ever been published. Catch the whole class.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');

const lock = JSON.parse(fs.readFileSync('package-lock.json', 'utf8'));
let checked = 0;
const problems = [];
for (const [name, meta] of Object.entries(lock.packages || {})) {
  if (!name || !meta.resolved || !meta.version) continue;
  const match = /\/([^/]+)-(\d[^/]*?)\.tgz$/.exec(meta.resolved);
  if (!match) continue;
  checked++;
  if (match[2] !== meta.version) {
    problems.push(`${name}: version=${meta.version} but tarball=${match[2]}`);
  }
}
assert.ok(checked > 50, `only ${checked} entries checked — is this a v3 lockfile?`);
assert.deepEqual(problems, [], `lockfile version/tarball mismatches:\n${problems.join('\n')}`);
console.log(`lockfile consistent — ${checked} tarball versions match their metadata`);
