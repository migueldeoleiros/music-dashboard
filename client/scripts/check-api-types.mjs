// Fails if src/api/schema.d.ts is stale vs ../contracts/openapi.json.
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const contract = '../contracts/openapi.json'
const committed = 'src/api/schema.d.ts'
for (const f of [contract, committed]) {
  if (!existsSync(f)) {
    console.error(`missing ${f}; run npm run generate:api`)
    process.exit(1)
  }
}
const dir = mkdtempSync(join(tmpdir(), 'api-types-'))
const out = join(dir, 'schema.d.ts')
try {
  const r = spawnSync('npx', ['--no-install', 'openapi-typescript', contract, '-o', out], { stdio: 'inherit' })
  if (r.status !== 0) process.exit(r.status ?? 1)
  if (readFileSync(out, 'utf8') !== readFileSync(committed, 'utf8')) {
    console.error(`${committed} is stale; run npm run generate:api`)
    process.exit(1)
  }
  console.log('API types up to date')
} finally {
  rmSync(dir, { recursive: true, force: true })
}
