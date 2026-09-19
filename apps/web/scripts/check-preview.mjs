import { readdir, stat } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const baseUrl = (process.argv[2] ?? 'http://127.0.0.1:4173').replace(/\/$/, '')

async function check(path, predicate, label) {
  const response = await fetch(`${baseUrl}${path}`)
  const body = await response.text()
  if (!response.ok || !predicate(body)) {
    throw new Error(`${label} failed: HTTP ${response.status}`)
  }
  console.log(`${label}: ok`)
}

try {
  await check('/', (body) => body.includes('<div id="root"></div>') && !body.includes('/@vite/client') && !body.includes('/src/main.tsx'), 'built frontend')
  await check('/api/health', (body) => body.includes('"status":"ok"'), 'API health')

  const distDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'assets')
  const assetNames = await readdir(distDir)
  const sizes = await Promise.all(assetNames.map(async (name) => ({ name, bytes: (await stat(join(distDir, name))).size })))
  const javascriptBytes = sizes.filter((asset) => asset.name.endsWith('.js')).reduce((total, asset) => total + asset.bytes, 0)
  const stylesheetBytes = sizes.filter((asset) => asset.name.endsWith('.css')).reduce((total, asset) => total + asset.bytes, 0)
  if (javascriptBytes > 420_000) throw new Error(`JavaScript budget exceeded: ${javascriptBytes} bytes`)
  if (stylesheetBytes > 90_000) throw new Error(`CSS budget exceeded: ${stylesheetBytes} bytes`)
  console.log(`asset budgets: ok (${javascriptBytes} JS bytes, ${stylesheetBytes} CSS bytes)`)
} catch (error) {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
}
