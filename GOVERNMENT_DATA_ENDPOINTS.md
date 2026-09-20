# Additional high-use government data routes

This catalog records official routes that are useful to Bengaluru residents and
were reachable during the endpoint audit. A route returning an HTML shell is a
verified website route, not proof that every underlying service API is public.
Authenticated, OTP, CAPTCHA, payment, and consent flows remain browser- or
approval-gated.

## Priority additions

| Service | Data the Agent should use | Verified route | Access mode |
| --- | --- | --- | --- |
| BBMP property tax | PID/SAS/ePID lookup, demand, arrears, receipts, payment status, officer jurisdiction, auction notices | [`login.aspx`](https://bbmptax.karnataka.gov.in/login.aspx), [`officialsdetails.aspx`](https://bbmptax.karnataka.gov.in/officialsdetails.aspx), [`Proclamation_AuctionList.aspx`](https://bbmptax.karnataka.gov.in/Forms/Proclamation_AuctionList.aspx) | Browser/session for property data; public read for directories/notices |
| BBMP e-Aasthi | eKhata search, ward/property mapping, property records, mutation status, public ward boundaries | [`citizen_core/`](https://bbmpeaasthi.karnataka.gov.in/citizen_core/), [`ward_boundaries.json`](https://bbmpeaasthi.karnataka.gov.in/citizen_core/data/ward_boundaries.json) | Browser/session; ward JSON is public read |
| KarnatakaOne | Service catalogue, service routes, center locator, receipt lookup, links for BESCOM/BMTC/police/Food/KSRTC | [`Public/Services`](https://www.karnatakaone.gov.in/Public/Services), [`OnlineServiceDetails`](https://www.karnatakaone.gov.in/Public/OnlineServiceDetails), [`ReceiptView`](https://www.karnatakaone.gov.in/ViewReceipt/ReceiptView) | Browser/session |
| Ahara PDS | Ration-card services, application/status routes, ration-card statistics, PDS distribution data, grievance route | [`Home/EServices`](https://ahara.karnataka.gov.in/Home/EServices), [`Stat_AAY_APL_BPL_Details.aspx`](https://ahara.karnataka.gov.in/fcs_office_statistics/Stat_AAY_APL_BPL_Details.aspx), [`stat_ration_taken_details.aspx`](https://ahara.karnataka.gov.in/fcs_office_statistics/stat_ration_taken_details.aspx) | Browser/session for citizen records; public read for reports |
| eJanMa | Birth/death verification, certificate download route, application status, registration counts, vital statistics | [`frmBirthDeathSearch.aspx`](https://ejanma.karnataka.gov.in/frmBirthDeathSearch.aspx), [`frmDownloadCertificate.aspx`](https://ejanma.karnataka.gov.in/frmDownloadCertificate.aspx), [`frmApplicationStatus.aspx`](https://ejanma.karnataka.gov.in/frmApplicationStatus.aspx), [`frmBirthDeathcount.aspx`](https://ejanma.karnataka.gov.in/frmBirthDeathcount.aspx) | Browser/session; do not automate personal-record lookups without user input |
| Karnataka professional tax | Enrolment, taxpayer route, payment and filing guidance | [`ptax.karnataka.gov.in`](https://ptax.karnataka.gov.in/) | Browser/session |

## First-party e-Aasthi API paths observed in the public web application

The e-Aasthi React application declares this API base:

`https://bbmpeaasthi.karnataka.gov.in/BBMPCoreAPI/v1/`

The following paths were observed in the application’s first-party JavaScript
bundle. They are not treated as open unauthenticated APIs until request/response
schemas, rate limits, and authorization requirements are confirmed:

- `GET BBMPCITZAPI/GetMasterZone`
- `GET BBMPCITZAPI/GetMasterWard?ZoneId={zone_id}`
- `GET BBMPCITZAPI/GET_WARD_BY_WARDNUMBER?wardNumber={ward_number}`
- `GET BBMPCITZAPI/LOAD_BBD_RECORDS?ZoneId={zone_id}&WardId={ward_id}&SerachType={type}&Search={query}&page={page}&pageSize={page_size}`
- `GET BBMPCITZAPI/LOAD_BBD_RECORDS_TAX?...`
- `GET SearchAPI/GET_SEARCHPAGE_DATA?PropertyEpid={property_epid}`
- `GET Report/Get_Application_BY_EPID?PropertyEpid={property_epid}&SearchType=PROPERTYID`
- `GET Report/Get_Mutation_application_status?PropertyEpid={property_epid}&SearchType=PROPERTYID`
- `GET Report/Get_MultiUnit_ekhata_details?PropertyId={property_id}`
- `GET KaveriAPI/GET_KAVERI_UPLOAD_DETAILS?...`
- `GET ESwathu/GET_OWNER_DETAILS?propertyId={property_id}&SearchOn={search_mode}`
- `GET Report/GetEAASTHIDailyReport`
- `GET Report/GetEAASTHIDailyReportDetails?wardNumber={ward}&QueryName={query}&pageNo={page}&PageSize={page_size}`

The Agent should use these only through a dedicated adapter that enforces
parameter validation, owner/session scoping, redaction, rate limits, and an
explicit user confirmation for any property-specific lookup. It must not call
mutation, OTP, payment, e-KYC, or document-upload routes as read-only tools.

## Recommended implementation order

1. Index the public report/directory routes first: BBMP officers and auction
   notices, e-Aasthi ward JSON, Ahara statistics, and eJanMa aggregate counts.
2. Add browser-backed, user-entered lookup flows for property tax, eKhata,
   ration-card status, and birth/death records.
3. Add authenticated API adapters only after official credentials, schemas, and
   data-use permissions are obtained.
4. Keep all payments, OTPs, CAPTCHA, e-KYC, mutation, and document downloads
   behind explicit review and user-controlled browser steps.

## Second discovery tier: spatial, elections, cases, and utility tracking

| Service | Data the Agent should use | Verified route | Access mode |
| --- | --- | --- | --- |
| Karnataka GIS / K-GIS | BDA layouts, roads, drains, parks, utilities, survey/admin boundaries, watershed layers | [`K-GIS portal`](https://kgis.ksrsac.in/kgis/), [`BDA MapServer`](https://kgis.ksrsac.in/kgismaps2/rest/services/BDA/BDA/MapServer?f=pjson), [`layout layer`](https://kgis.ksrsac.in/kgismaps2/rest/services/BDA/BDA/MapServer/9?f=pjson), [`watershed WMS`](https://kgis.ksrsac.in/kgismaps1/services/NR_V2/Watershed/MapServer/WMSServer?request=GetCapabilities&service=WMS) | Public read |
| Karnataka electoral services | Electoral notices, Karnataka roll downloads, voter search, polling-station routing | [`CEO Karnataka`](https://ceo.karnataka.gov.in/), [`ECI roll download`](https://voters.eci.gov.in/download-eroll?stateCode=S10), [`ECI elector search`](https://electoralsearch.eci.gov.in/) | Public/browser |
| Karnataka local-body elections | Local election notifications, results, rolls, schedules | [`State Election Commission`](https://karsec.karnataka.gov.in/) | Public read |
| BBMP court cases | Citizen case login, case lookup/status, court-document route | [`BCCMS citizen portal`](https://bbmpenyaya.karnataka.gov.in/Citizen/CitizenLogin.aspx) | Browser/session |
| BESCOM services | Complaint status, billing complaint status, service requests, bill-statement route | [`complaint tracker`](https://www.bescom.co.in/bescom/dashboard/consumer-dashboard/track-complaints), [`service dashboard`](https://www.bescom.co.in/bescom/dashboard/consumer-dashboard/my-services) | Browser/session |
| BBMP GIS | Location details, ward map, civic-asset layers | [`BBMP GIS viewer`](https://www.bbmp.gov.in/gisviewer/) | Browser-only; current connector probe timed out |

### Concrete spatial query template

The K-GIS BDA service exposes an ArcGIS REST layer named `Layout_Boundary`
(layer `9`). A bounded feature query can use:

`https://kgis.ksrsac.in/kgismaps2/rest/services/BDA/BDA/MapServer/9/query?where=1%3D1&outFields=*&returnGeometry=false&resultRecordCount=1&f=json`

The implementation should replace `where=1%3D1` with a validated spatial or
attribute filter, cap `resultRecordCount`, request only necessary fields, and
keep geometry optional. Do not allow arbitrary service URLs or unbounded map
queries.

## Third discovery tier: certificates, land, housing, transport, police, and environment

| Service | Data the Agent should use | Verified route | Access mode |
| --- | --- | --- | --- |
| Nadakacheri / AJSK | Income, caste, residence and other certificate services; application status | [`AJSK portal`](https://nadakacheri.karnataka.gov.in/ajsk), [`status`](https://ajsk.karnataka.gov.in/NK_Status) | Browser/session |
| Sakala | Service catalogue, statutory timelines, application status, performance reports | [`online services`](https://sakala.kar.nic.in/Onlineservices_kan.aspx), [`monthly reports`](https://sakala.kar.nic.in/monthly_report_kan.aspx) | Public/browser |
| Karnataka RTC | RTC/land-record services, mutation routes and citizen land-record workflows | [`RTC services`](https://rtc.karnataka.gov.in/Service78/), [`RTC citizen route`](https://rtc.karnataka.gov.in/Service78/RTC.aspx) | Browser/session |
| Karnataka RERA | Registered projects, agents, complaints, orders and cause lists | [`RERA portal`](https://rera.karnataka.gov.in/), [`projects`](https://rera.karnataka.gov.in/viewAllProjects), [`complaints`](https://rera.karnataka.gov.in/viewAllComplaints), [`cause list`](https://rera.karnataka.gov.in/projectDailyCauseList) | Public read |
| KSPCB | Environmental notices, consent guidance, citizen routes, annual/audit reports | [`KSPCB`](https://kspcb.karnataka.gov.in/), [`e-citizen`](https://kspcb.karnataka.gov.in/index.php/e-citizen) | Public read/browser |
| Karnataka Housing Board | Housing schemes, application notices, public notices, tenders and project documents | [`KHB portal`](https://khb.karnataka.gov.in/) | Public read |
| KSRTC | Bus-service routes, ticket/booking enquiry and service notices | [`KSRTC`](https://ksrtc.in/), [`booking enquiry`](https://ksrtc.in/booking-enquiry) | Browser/session |
| Bengaluru City Police | Police notices, citizen-service routes and lost-property reporting | [`Bengaluru City Police`](https://bcp.karnataka.gov.in/en), [`e-Lost reports`](https://kspapp.ksp.gov.in/ksp/api/elost-reports) | Public/browser |
| Karnataka LRI | Land-resource inventory and watershed information | [`Sujala LRI`](https://sujala3lri.karnataka.gov.in/) | Browser/session |

RERA project and complaint pages are large public HTML datasets. They should be
ingested with pagination/field extraction rather than repeatedly downloading
the full page in every Agent turn.
