# Government integration catalog

CivitasX exposes the requested government integrations through
`GET /api/live/connectors` and the MCP `list_live_sources` tool. Every entry
declares its priority, official URL, transport, capabilities, credentials,
consent scope, and refresh policy.

## What is live today

- Public read-only portals and documents can be refreshed into the grounded
  source index. Their response is hashed and page-addressable.
- India OGD can be queried directly with
  `GET /api/live/open-data/search?resource_id=...` after setting
  `CIVITAS_DATA_GOV_API_KEY`. The resource ID and filters are validated and only
  GET requests are issued.
- API Setu discovery, Karnataka OGD, Bengaluru Open Data, GBA/BBMP, BDA,
  BMRCL, BMTC, BWSSB, BESCOM, myScheme, UMANG, eCourts, Bhoomi, Kaveri,
  e-Swathu, Mojini, Udyam, MCA, e-Procurement, and NCS are represented in the
  catalog with their official routes.

## What is intentionally gated

DigiLocker/MeriPehchaan, API Setu document APIs, CPGRAMS submission, VAHAN and
Sarathi verification, GSTN, ABDM/ABHA, NAD, and similar personal or
transactional services are listed but marked `consent_required` or
`approval_required`. The connector sends no request until the official
consumer account, credentials, and user-consent flow are configured.

The catalog does not bypass CAPTCHA, OTP, login, payment, consent, or publisher
approval. A future browser connector can use the same metadata to pause for the
user and resume from a reviewed checkpoint.

Official starting points:

- [API Setu](https://docs.apisetu.gov.in/document-central/explore-apisetu/Overview.html)
- [DigiLocker](https://apisetu.gov.in/digilocker)
- [Seva Sindhu](https://sevasindhu.karnataka.gov.in/Sevasindhu/English)
- [Karnataka OGD](https://karnataka.data.gov.in/)
- [India OGD](https://data.gov.in/)
- [CPGRAMS](https://www.pgportal.gov.in/)
