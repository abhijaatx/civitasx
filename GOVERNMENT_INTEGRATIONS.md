# Government integration catalog

CivitasX exposes the requested government integrations through
`GET /api/live/connectors` and the MCP `list_live_sources` tool. Every entry
declares its priority, official URL, transport, capabilities, credentials,
consent scope, and refresh policy.

## What is live today

- Public read-only portals and documents can be refreshed into the grounded
  source index. Their response is hashed and page-addressable.
- Run `cd services/api && uv run python ../../scripts/verify_live_connectors.py --json`
  to check every registered connector. The command uses the
  same bounded GET path as the API, persists successful snapshots under
  `.civitas/live-sources.json`, and records each connector's last status under
  `.civitas/live-connector-status.json`.
- India OGD can be queried directly with
  `GET /api/live/open-data/search?resource_id=...` after setting
  `CIVITAS_DATA_GOV_API_KEY`. The resource ID and filters are validated and only
  GET requests are issued.
- API Setu discovery, Karnataka OGD, Bengaluru Open Data, GBA/BBMP, BDA,
  BMRCL, BMTC, BWSSB, BESCOM, Karnataka iPGRS, myScheme, UMANG, eCourts,
  Bhoomi, Kaveri, e-Swathu, Mojini V3, BBMP property tax, BBMP e-Aasthi,
  KarnatakaOne, Ahara PDS, eJanMa, professional tax, Udyam, MCA,
  e-Procurement, NCS, K-GIS spatial services, CEO Karnataka, ECI electoral
  services, Karnataka local elections, BBMP court cases, BESCOM trackers,
  Nadakacheri, Sakala, Karnataka RTC, RERA Karnataka, KSPCB, Karnataka Housing
  Board, KSRTC, Bengaluru City Police, and Karnataka LRI are represented in the
  catalog with their official routes. See
  [`GOVERNMENT_DATA_ENDPOINTS.md`](GOVERNMENT_DATA_ENDPOINTS.md) for the
  e-Aasthi, K-GIS, RERA, and certificate-route inventory.

## What is intentionally gated

DigiLocker/MeriPehchaan, API Setu document APIs, CPGRAMS submission, VAHAN and
Sarathi verification, GSTN, ABDM/ABHA, NAD, and similar personal or
transactional services are listed but marked `consent_required` or
`approval_required`. The connector sends no request until the official
consumer account, credentials, and user-consent flow are configured.

The catalog does not bypass CAPTCHA, OTP, login, payment, consent, or publisher
approval. A future browser connector can use the same metadata to pause for the
user and resume from a reviewed checkpoint.

An authority-issued API can be enabled through the certified official-API
connector only when its endpoint and scoped credential are configured. The
connector sends an idempotency key derived from the approved preparation hash
and refuses to mark a ticket submitted unless the response contains a verified
authority reference.

Legacy government URLs may redirect to a current official hostname. CivitasX
follows at most four redirects only when every destination remains on the
explicit HTTPS allowlist; arbitrary redirects are rejected.

Official starting points:

- [API Setu](https://docs.apisetu.gov.in/document-central/explore-apisetu/Overview.html)
- [DigiLocker](https://apisetu.gov.in/digilocker)
- [Seva Sindhu](https://sevasindhu.karnataka.gov.in/Sevasindhu/English)
- [Karnataka OGD](https://karnataka.data.gov.in/)
- [India OGD](https://data.gov.in/)
- [CPGRAMS](https://www.pgportal.gov.in/)
