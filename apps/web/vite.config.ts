import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const nodeProcess = (globalThis as typeof globalThis & { process?: { env?: Record<string, string | undefined> } }).process
const configuredHosts = (nodeProcess?.env?.VITE_ALLOWED_HOSTS ?? '')
  .split(',')
  .map((host: string) => host.trim())
  .filter(Boolean)
const apiProxyTarget = nodeProcess?.env?.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // Keep the dev server private by default. If a temporary tunnel is needed,
    // pass VITE_ALLOWED_HOSTS with the exact tunnel hostname instead of opening
    // every Host header to the local process.
    allowedHosts: configuredHosts.length ? configuredHosts : ['localhost', '127.0.0.1'],
    proxy: {
      '/api': apiProxyTarget,
      '/mcp': apiProxyTarget,
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
    host: '127.0.0.1',
    allowedHosts: configuredHosts.length ? configuredHosts : ['localhost', '127.0.0.1'],
    proxy: {
      '/api': apiProxyTarget,
      '/mcp': apiProxyTarget,
    },
  },
})
