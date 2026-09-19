# Web preview and deployment

The Vite dev server is for local development only. It exposes source modules,
React Refresh, and an in-process API proxy, so it should not be the public
preview target.

## Stable local preview

```bash
npm run build
npm run preview
```

The preview server serves the built `dist/` artifact on `127.0.0.1:4173`.
Use `npm run preview:public` only when a temporary tunnel is explicitly
required.

## Temporary tunnel

Allow only the exact tunnel hostname:

```bash
VITE_ALLOWED_HOSTS=your-subdomain.trycloudflare.com \
VITE_API_PROXY_TARGET=http://127.0.0.1:8001 \
npm run preview:public
cloudflared tunnel --url http://127.0.0.1:4173
```

Do not use `allowedHosts: true` or tunnel the Vite development server for a
shared review. For a public preview, point `VITE_API_PROXY_TARGET` at a
separate preview API process with `CIVITAS_AGENT_PROVIDER=ollama` and a
separate SQLite database; do not tunnel the developer's paid-provider API.
Keep `/api` behind the same origin and verify these before sharing a link:

```bash
curl -fsS http://127.0.0.1:4173/
curl -fsS http://127.0.0.1:8000/api/config
```

For production, serve `dist/` from a stable HTTPS host and proxy `/api` to the
API service. Add CSP, HSTS, and an uptime/health check at the hosting layer.

## Local-pilot password recovery

Local authentication recovery is disabled unless the API process receives a
secret recovery code through `CIVITAS_LOCAL_RECOVERY_CODE`. Set it through the
process environment or a secret manager; never commit it to the repository.
The sign-in screen then exposes a recovery form that rotates the password and
revokes the account's existing local sessions. Cognito deployments should use
the identity provider's recovery flow instead.
