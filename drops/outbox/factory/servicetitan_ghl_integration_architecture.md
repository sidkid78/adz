# Technical Architecture & End-to-End Data Flow

## 1. Executive Summary & Business Context
This architecture establishes an enterprise-grade, bidirectional synchronization engine between GoHighLevel (v2 SaaS Engine) and ServiceTitan (v2 REST API) for Apex Comfort Solutions (Central Texas HVAC). The architecture eliminates manual Google Sheets bottlenecks, guarantees sub-60-second speed-to-lead via automated missed-call text-back and Google Local Services Ads (LSA) intake, and provides a closed-loop quote re-engagement pipeline designed to recover $37,500/month ($450,000/year) in unsold estimate revenue leakage.

```
[ INBOUND REVENUE CHANNELS ]
  ├── Google Local Services Ads (Direct Call / LSA Web Lead)
  ├── Twilio / GHL LC-Phone Tracking Pool (After-Hours DNI Numbers)
  └── Emergency Web Inquiries (WordPress/Elementor HVAC Landing Pages)
                               │
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   GOHIGHLEVEL (SPEED-TO-LEAD & CRM)                     │
│  - Sub-30s Automated Missed-Call Text-Back Engine (Twilio/LC-Phone)    │
│  - 24/7 Conversational SMS Lead Qualification Workflows                │
│  - Unsold Quote Nurture Sequences (Email/SMS Drips: Day 1, 3, 5, 7)    │
│  - Contact Identity Ingestion & E.164 Normalization                    │
└────────────────────────────────────────────────────────────────────────┘
                               │ 
        Outbound Webhook       │ Inbound Webhook / Poller
        (HMAC-SHA256 Signed)   │ (OAuth 2.0 JWT Bearer Token)
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│             REVOPS INTEGRATION ENGINE (Middleware Layer)               │
│  - Payload Validation & Schema Transformation (AWS Lambda / Node.js)   │
│  - Waterfall Deduplication Engine (Phone -> Email -> Address)          │
│  - Redis Idempotency Cache & Mutual Exclusion Locks (TTL 86400s)       │
│  - ServiceTitan v2 Token Lifecycle Manager (Auto-Refresh @ 12m)        │
│  - Outbound Leaky-Bucket Rate Limiter (Max 250 req/min)                │
└────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│              SERVICETITAN ENTERPRISE (SYSTEM OF RECORD)                │
│  - /jpm/v2/tenant/{tenantId}/bookings  (Provisional Dispatch Tray)     │
│  - /crm/v2/tenant/{tenantId}/customers (Master Customer Directory)     │
│  - /crm/v2/tenant/{tenantId}/locations (Physical Service Properties)   │
│  - /dispatch/v2/tenant/{tenantId}/jobs (Operational Work Orders)       │
│  - /accounting/v2/tenant/{tenantId}/estimates (Estimate Lifecycle)     │
└────────────────────────────────────────────────────────────────────────┘
```

## 2. Authentication Strategy & Token Management

### ServiceTitan V2 REST API
- **Grant Type:** `client_credentials`
- **Token Endpoint:** `https://auth.servicetitan.io/connect/token`
- **Headers:**
  - `ST-App-Key`: `82f9d3b1-4c56-42d8-9e12-b13c9a174092`
  - `Authorization`: `Bearer <access_token>`
- **Token Expiry:** 900 seconds (15 minutes). The middleware executes active renewal at 720 seconds (12 minutes) via an automated task deployed in AWS Secrets Manager and AWS Lambda.
- **Tenant Context:** Injected as route parameter across all endpoints: `tenantId = 392817492`.

### GoHighLevel V2 API (LeadConnector)
- **Grant Type:** `authorization_code` (OAuth 2.0) with automated offline refresh tokens.
- **Token Endpoint:** `https://services.leadconnectorhq.com/oauth/token`
- **Token Expiry:** 86,400 seconds (24 hours). Middleware stores the encrypted refresh token inside Amazon DynamoDB and triggers renewal every 12 hours.
- **Location Context:** `locationId = lKj87YgTt192kLm99` (Apex Comfort Solutions - Central Texas Sub-Account).

## 3. System of Record (SoR) Governance Matrix

| Entity / Domain | System of Record (SoR) | Integration Flow | Conflict Resolution Policy |
|---|---|---|---|
| Inbound Lead / Prospect | GoHighLevel | GHL -> ServiceTitan (Booking) | GoHighLevel owns the record during acquisition. ServiceTitan assumes ownership upon Booking conversion to Job. |
| Customer Master Data | ServiceTitan | Bidirectional | ServiceTitan wins for legal name, billing address, and tax-exempt certificates. GoHighLevel wins for marketing opt-ins and DND flags. |
| Service Location | ServiceTitan | Bidirectional | ServiceTitan wins for physical street address validation, geocoding, and technician zone mapping. |
| Booking Request | GoHighLevel | GHL -> ServiceTitan | GoHighLevel generates external booking request. ServiceTitan creates immutable booking in dispatch tray. |
| Job / Work Order | ServiceTitan | ServiceTitan -> GHL | ServiceTitan is authoritative. State transitions (Scheduled, Dispatched, Arrived, Done) overwrite GHL Opportunity stages. |
| Appointment Window | ServiceTitan | ServiceTitan -> GHL | ServiceTitan dispatch board schedule overwrites GHL Calendar booking dates and start/end times. |
| Estimates & Proposals | ServiceTitan | ServiceTitan -> GHL | ServiceTitan pricing engine is authoritative. Unsold quotes trigger GHL automated re-engagement workflows. |
| Invoices & Payments | ServiceTitan | ServiceTitan -> GHL | ServiceTitan ledger controls balance and transaction settlement. Read-only projection written to GHL Custom Objects. |

## 4. End-to-End State Machine Workflow

```
   [ Inbound Event: LSA Lead / Web Form / Missed Call ]
                           │
                           ├── Missed Call (After-Hours) ──► GHL: Missed-Call Text-Back (< 30s)
                           │                                    │
                           └── Web Form / Emergency Lead ──────► GHL: Instant SMS Speed-to-Lead (< 60s)
                                                                │
                                                                ▼
                                                   [ Prospect Responds via SMS ]
                                                                │
                          ┌─────────────────────────────────────┴─────────────────────────────────────┐
                          ▼                                                                           ▼
              [ Needs Service Dispatch ]                                                   [ Inquiry / Out of Zone ]
                          │                                                                           │
              GHL Workflow: Add Tag `st-sync-pending`                                      GHL: Automated FAQ Sequence
                          │
              POST Webhook to Middleware Engine
                          │
              Middleware: Deduplication & Identity Resolution
                ├── Query ST Customer via Normalized Phone (+1XXXXXXXXXX)
                ├── Match Found? ──► Reuse `customerId` & `locationId`
                └── No Match?    ──► POST `/crm/v2/.../customers` & `/locations`
                          │
              POST `/jpm/v2/tenant/392817492/bookings`
                (Summary: Emergency HVAC Service | Source: GHL/LSA | Priority: Urgent)
                          │
              Dispatcher accepts Booking on ServiceTitan Dispatch Board -> Converts to Job
                          │
              Dispatcher Assigns Tech ──► Tech En Route ──► Tech On-Site ──► Diagnosis Completed
                                                                                   │
                                                                                   ▼
                                                                     Estimate Generated in ST
                                                                    (Status: Open / Presented)
                                                                                   │
                                                     ┌─────────────────────────────┴─────────────────────────────┐
                                                     ▼                                                           ▼
                                          [ Customer Signs On-Site ]                                  [ Customer Does Not Sign ]
                                                     │                                                           │
                                             ST: Estimate "Sold"                                         ST: Estimate "Open"
                                                     │                                                           │
                                             ST Webhook: Sold                                            ST Webhook: Open (> 24h)
                                                     │                                                           │
                                                     ▼                                                           ▼
                                         GHL: Pipeline -> "Won"                                      GHL: Quote Nurture Sequence
                                         Terminate Follow-Up Drips                                   SMS/Email (Day 1, 3, 5, 7)
                                                                                                                 │
                                                                                                  Customer Accepts via GHL SMS
                                                                                                                 │
                                                                                                                 ▼
                                                                                                     Alert Dispatcher in ST
```

---

# Master Data Dictionary & Field Mapping Table

The following master mapping dictionary defines the strict transformation rules across ServiceTitan V2 REST API endpoints and GoHighLevel V2 CRM entities. All ServiceTitan custom fields are located inside the `customFields` array and extracted via explicit key-value lookups.

| # | Entity Domain | ServiceTitan V2 Field Path | ST Data Type | GoHighLevel V2 Field Key | GHL Data Type | Direction | Transformation Rule & Business Logic |
|---|---|---|---|---|---|---|---|
| 1 | Customer | `customer.id` | Long | `contact.customFields.st_customer_id` | Text | ST -> GHL | Cast int64 to string. Immutable external indexing key. |
| 2 | Customer | `customer.name` | String | `contact.firstName`, `contact.lastName` | String | Bidirectional | ST to GHL: Split on first space. GHL to ST: Concatenate `{firstName} {lastName}`. |
| 3 | Customer | `customer.type` | Enum | `contact.customFields.customer_type` | Single Select | ST -> GHL | Map `Residential` -> `Residential`, `Commercial` -> `Commercial`. |
| 4 | Customer | `customer.active` | Boolean | `contact.customFields.st_is_active` | Boolean | ST -> GHL | Direct boolean pass-through. |
| 5 | Customer | `customer.balance` | Decimal | `contact.customFields.account_balance` | Numerical | ST -> GHL | 2-decimal precision floating-point currency representation. |
| 6 | Customer | `contacts[type='Phone'].value` | String | `contact.phone` | Phone | Bidirectional | Regex filter: `^\+1[2-9]\d{9}$`. Strip non-digits; prepend `+1`. |
| 7 | Customer | `contacts[type='Email'].value` | String | `contact.email` | Email | Bidirectional | Trim whitespaces; force lowercase. RFC 5322 regex validation. |
| 8 | Customer | `customer.doNotMail` | Boolean | `contact.dndSettings.Email.status` | Boolean | Bidirectional | If `true`, set GHL Email DND status to `enabled`. |
| 9 | Customer | `customer.doNotCall` | Boolean | `contact.dndSettings.Call.status` | Boolean | Bidirectional | If `true`, set GHL Phone Call DND status to `enabled`. |
| 10 | Customer | `customFields.[name='TaxExempt'].value` | Boolean | `contact.customFields.tax_exempt` | Boolean | ST -> GHL | Filter ST array where `name == 'TaxExempt'`; extract boolean. |
| 11 | Location | `location.id` | Long | `contact.customFields.st_location_id` | Text | ST -> GHL | Service property primary key. Cast to string. |
| 12 | Location | `location.address.street` | String | `contact.address1` | String | Bidirectional | Street address. Capitalize words; validate USPS standard abbreviations. |
| 13 | Location | `location.address.unit` | String | `contact.customFields.unit_number` | String | Bidirectional | Apartment, Suite, or Unit designator. Nullable. |
| 14 | Location | `location.address.city` | String | `contact.city` | String | Bidirectional | Verify against Central Texas coverage map (Austin, Round Rock, etc.). |
| 15 | Location | `location.address.state` | String | `contact.state` | String | Bidirectional | Force uppercase 2-letter ISO standard: `TX`. |
| 16 | Location | `location.address.zip` | String | `contact.postalCode` | String | Bidirectional | Enforce 5-digit US postal code string format. |
| 17 | Location | `location.address.latitude` | Decimal | `contact.customFields.latitude` | Numerical | ST -> GHL | Floating-point coordinates for dispatch verification. |
| 18 | Location | `location.address.longitude` | Decimal | `contact.customFields.longitude` | Numerical | ST -> GHL | Floating-point coordinates for dispatch verification. |
| 19 | Location | `customFields.[name='GateCode'].value` | String | `contact.customFields.gate_code` | Text | Bidirectional | Property access code. Filter ST array where `name == 'GateCode'`. |
| 20 | Booking | `booking.id` | Long | `opportunity.customFields.st_booking_id` | Text | ST -> GHL | External booking identifier generated upon intake. |
| 21 | Booking | `booking.source` | String | `opportunity.source` | String | GHL -> ST | Ingest GHL source tag (e.g., `Google LSA - Central Austin`). |
| 22 | Booking | `booking.summary` | String | `opportunity.name` | String | GHL -> ST | Format: `[Lead Source] - [Job Type] - [Contact Last Name]`. |
| 23 | Booking | `booking.priority` | Enum | `opportunity.customFields.priority_level`| Single Select | GHL -> ST | Map values: `Low`, `Medium`, `High`, `Urgent`. After-hours sets `Urgent`. |
| 24 | Booking | `booking.start` | DateTime | `opportunity.customFields.target_arrival` | ISO8601 | GHL -> ST | Target arrival slot. Converted from US/Central to UTC ISO8601. |
| 25 | Job | `job.id` | Long | `opportunity.customFields.st_job_id` | Text | ST -> GHL | Primary operational work order identifier. |
| 26 | Job | `job.jobNumber` | String | `opportunity.customFields.st_job_number` | Text | ST -> GHL | Human-readable job designator (e.g., `JOB-10492`). |
| 27 | Job | `job.jobStatus` | Enum | `opportunity.pipelineStageId` | Stage ID | ST -> GHL | Map: `Scheduled` -> Stage 1, `In Progress` -> Stage 2, `Completed` -> Stage 3. |
| 28 | Job | `job.businessUnitId` | Long | `opportunity.customFields.business_unit` | Single Select | ST -> GHL | Map ID `101` -> `HVAC Service`, `102` -> `HVAC Install`. |
| 29 | Job | `job.jobTypeId` | Long | `opportunity.customFields.job_type` | Single Select | ST -> GHL | Map ID `204` -> `AC Emergency`, `205` -> `System Replacement`. |
| 30 | Job | `job.total` | Decimal | `opportunity.monetaryValue` | Numerical | ST -> GHL | Invoice total written to GHL Opportunity value in USD. |
| 31 | Job | `job.assignedTechnicianIds` | Array | `opportunity.customFields.assigned_tech` | Text | ST -> GHL | Technician ID mapped to staff name for customer SMS notifications. |
| 32 | Appointment | `appointment.start` | DateTime | `appointment.startTime` | ISO8601 | ST -> GHL | Appointment start time in UTC format. |
| 33 | Appointment | `appointment.end` | DateTime | `appointment.endTime` | ISO8601 | ST -> GHL | Appointment end time in UTC format. |
| 34 | Appointment | `appointment.status` | Enum | `appointment.status` | Enum | ST -> GHL | Map: `Scheduled` -> `confirmed`, `Arrived` -> `showed`, `Canceled` -> `cancelled`. |
| 35 | Estimate | `estimate.id` | Long | `opportunity.customFields.st_estimate_id`| Text | ST -> GHL | Primary identifier for tracking unsold quotes. |
| 36 | Estimate | `estimate.status` | Enum | `opportunity.pipelineStageId` | Stage ID | ST -> GHL | Map: `Open` -> `Quote Delivered`, `Sold` -> `Won`, `Dismissed` -> `Lost`. |
| 37 | Estimate | `estimate.total` | Decimal | `opportunity.monetaryValue` | Numerical | ST -> GHL | Total estimate value in USD. Values > $5,000 flag high-ticket alert. |
| 38 | Estimate | `estimate.summary` | String | `opportunity.customFields.quote_summary` | Large Text | ST -> GHL | Scope of work (e.g., "16 SEER Heat Pump Full Replacement"). |
| 39 | Estimate | `estimate.createdOn` | DateTime | `opportunity.customFields.quote_created` | ISO8601 | ST -> GHL | Timestamp initiating the 24-hour countdown for nurture sequence. |
| 40 | Invoice | `invoice.id` | Long | `customObject.invoice.invoice_id` | Text | ST -> GHL | External key linking invoice custom object record in GHL. |
| 41 | Invoice | `invoice.total` | Decimal | `customObject.invoice.total_amount` | Numerical | ST -> GHL | Final settled invoice dollar amount. |
| 42 | Invoice | `invoice.balance` | Decimal | `customObject.invoice.balance_due` | Numerical | ST -> GHL | Outstanding balance. $0.00 signals complete payment. |

---

# Webhook Event Schema Specifications & API Payloads

## 1. Webhook Signature Validation Engine
All inbound ServiceTitan webhooks must be verified using HMAC-SHA256 signature calculation over the raw HTTP request body using the shared App Secret.

```yaml
algorithm: "HMAC-SHA256"
header_signature_key: "x-servicetitan-signature"
timestamp_tolerance_seconds: 300
verification_workflow:
  - "Extract the raw, unparsed HTTP request payload string."
  - "Retrieve the active ST App Secret from AWS Secrets Manager."
  - "Calculate HMAC-SHA256 digest on the payload string."
  - "Base64 encode the calculated digest."
  - "Perform constant-time comparison against header value to eliminate timing attack vectors."
```

## 2. Inbound GoHighLevel Intake Webhooks

### Event: `EmergencyLeadIntake` (GHL to Middleware)
Triggered when an emergency service form is submitted or an after-hours conversational SMS flow qualifies a dispatch request:

```json
{
  "event": "lead_intake_submitted",
  "timestamp": "2026-09-11T20:15:30.125Z",
  "locationId": "lKj87YgTt192kLm99",
  "workflow_id": "wf_speed_to_lead_after_hours",
  "contact": {
    "id": "ghl_cnt_9823471029",
    "firstName": "Marcus",
    "lastName": "Vance",
    "phone": "+15125550198",
    "email": "mvance77@gmail.com",
    "address1": "4102 Spicewood Springs Rd",
    "city": "Austin",
    "state": "TX",
    "postalCode": "78759",
    "customFields": [
      {
        "key": "system_type",
        "value": "Split AC - Heat Pump"
      },
      {
        "key": "issue_description",
        "value": "Complete cooling failure, burning electrical smell coming from outdoor condenser fan."
      },
      {
        "key": "lead_source",
        "value": "Google Local Services Ads"
      },
      {
        "key": "urgency_level",
        "value": "Emergency - Immediate Dispatch Required"
      }
    ]
  }
}
```

### Event: `MissedCallAttribution` (Twilio/LC-Phone to GHL Engine)
Captures missed after-hours calls to initiate the sub-30-second automated text-back:

```json
{
  "event": "call_status_update",
  "call_sid": "CA9823049182309182309182309",
  "direction": "inbound",
  "from": "+15125550198",
  "to": "+15127008899",
  "call_status": "no-answer",
  "call_duration": 0,
  "tracking_source": "Google LSA - Central Austin",
  "timestamp": "2026-09-11T20:14:15.000Z"
}
```

## 3. ServiceTitan V2 API Ingestion Payloads

### POST `/jpm/v2/tenant/392817492/bookings` (Middleware to ServiceTitan)
Dispatches the verified booking payload into the ServiceTitan Dispatch Tray:

```json
{
  "customerId": 49201934,
  "locationId": 58392011,
  "businessUnitId": 101,
  "jobTypeId": 204,
  "campaignId": 89,
  "priority": "Urgent",
  "isSendConfirmationEmail": false,
  "externalId": "ghl_cnt_9823471029_20260911",
  "summary": "AFTER-HOURS EMERGENCY: Complete cooling failure, burning electrical smell. Sourced via Google LSA.",
  "start": "2026-09-11T21:00:00.000Z",
  "customerContacts": [
    {
      "type": "Phone",
      "value": "+15125550198",
      "memo": "Primary Mobile - GHL SMS Engaged"
    }
  ],
  "customFields": [
    {
      "typeId": 12,
      "name": "Equipment_Type",
      "value": "Split AC - Heat Pump"
    },
    {
      "typeId": 15,
      "name": "Integration_Source",
      "value": "GHL-Auto-Booked"
    }
  ]
}
```

## 4. ServiceTitan Webhook Payloads to Middleware

### Event: `JobStatusChanged`
Published by ServiceTitan when a technician updates job status on mobile:

```json
{
  "eventId": "evt_77192a83-11ef-4209-a10c-992318029ab1",
  "eventType": "JobStatusChanged",
  "tenantId": 392817492,
  "timestamp": "2026-09-11T21:30:00.1245892Z",
  "payload": {
    "jobId": 3948210,
    "jobNumber": "JOB-10492",
    "customerId": 49201934,
    "locationId": 58392011,
    "previousStatus": "Dispatched",
    "currentStatus": "In Progress",
    "businessUnitId": 101,
    "modifiedOn": "2026-09-11T21:29:58.8921102Z"
  }
}
```

### Event: `EstimateCreated` / `EstimateUpdated` (Unsold Quote Recovery Engine)
Published when a field technician presents an estimate to the homeowner:

```json
{
  "eventId": "evt_3319082a-44fe-4821-b892-0019284711ac",
  "eventType": "EstimateUpdated",
  "tenantId": 392817492,
  "timestamp": "2026-09-11T22:15:00.000Z",
  "payload": {
    "estimateId": 8847102,
    "jobId": 3948210,
    "customerId": 49201934,
    "locationId": 58392011,
    "name": "16 SEER Complete Heat Pump Replacement",
    "status": "Open",
    "reviewStatus": "Reviewed",
    "summary": "Furnish and install 3.0-ton 16-SEER Trane Heat Pump system, new pad, digital thermostat, and secondary drain pan.",
    "subtotal": 8450.00,
    "total": 8450.00,
    "createdOn": "2026-09-11T22:00:00.000Z",
    "modifiedOn": "2026-09-11T22:14:50.000Z",
    "soldOn": null,
    "active": true
  }
}
```

---

# Edge Cases, Deduplication, and System Reliability Architecture

## 1. Waterfall Deduplication Engine
To ensure Apex Comfort Solutions never creates duplicate customer profiles or fractured job histories, inbound leads pass through a strict four-tiered waterfall resolution algorithm before any write operation is dispatched to ServiceTitan.

```
                  [ Incoming Lead Payload: Raw Contact Data ]
                                       │
                                       ▼
                       [ Normalize Phone String to E.164 ]
                           (Regex: ^\+1[2-9]\d{9}$)
                                       │
                                       ▼
                     [ Check Redis Idempotency Key TTL 24h ]
                     (Key: dedupe:lead:{sha256(phone+date)})
                                       │
                      ┌────────────────┴────────────────┐
                      ▼                                 ▼
               [ Key Exists ]                   [ Key Does Not Exist ]
                      │                                 │
              [ Return 200 OK ]                 [ Acquire Lock Key ]
             (Duplicate Discarded)                      │
                                                        ▼
                                           [ Query ServiceTitan API ]
                                            GET /crm/v2/.../customers
                                             ?phone={normalized_phone}
                                                        │
                                       ┌────────────────┴────────────────┐
                                       ▼                                 ▼
                                [ Match Found ]                   [ No Match ]
                                       │                                 │
                               Extract `customerId`             Query Location by Address
                                       │                        GET /crm/v2/.../locations
                                       │                         ?street={street}&zip={zip}
                                       │                                 │
                                       │                        ┌────────┴────────┐
                                       │                        ▼                 ▼
                                       │                  [ Match Found ]    [ No Match ]
                                       │                        │                 │
                                       │                  Link Existing     POST Customer &
                                       │                   Location ID      POST Location
                                       │                        │                 │
                                       └────────────────┬───────┴─────────────────┘
                                                        ▼
                                         [ POST /jpm/v2/.../bookings ]
                                          (Attach Validated IDs)
```

### Waterfall Resolution Steps
1. **Tier 1: ServiceTitan External Customer ID**  
   If `contact.customFields.st_customer_id` is populated in GoHighLevel, skip all query layers and execute direct writes against that Customer entity.
2. **Tier 2: E.164 Phone Normalization & Exact Match**  
   Strip formatting characters `( ) - .` and normalize to `+1XXXXXXXXXX`. Query `GET /crm/v2/tenant/392817492/customers?phone={phone}`. If exactly one customer record is returned, extract `customerId` and primary `locationId`.
3. **Tier 3: Address Normalization & CASS Standard Matching**  
   If the phone match returns multiple records (e.g., property management firms or rental portfolio owners) or zero records, parse the address against USPS CASS guidelines. Query `GET /crm/v2/tenant/392817492/locations?streetAddress={street}&zip={zip}`. If a matching location is found, link the booking to the parent `customerId` associated with that physical site.
4. **Tier 4: Net-New Customer & Location Creation**  
   If Tiers 1-3 fail to yield a match, execute sequential transactional writes:
   - `POST /crm/v2/tenant/392817492/customers` -> returns new `customerId`.
   - `POST /crm/v2/tenant/392817492/locations` (associated with `customerId`) -> returns new `locationId`.
   - Update GoHighLevel Contact with `st_customer_id` and `st_location_id`.

## 2. Concurrency Control & Infinite Loop Prevention
Bidirectional synchronization presents critical infinite loop vulnerabilities where an update in System A triggers a webhook in System B, bouncing endlessly between platforms.

```yaml
loop_prevention_architecture:
  signature_header: "X-Originating-System"
  middleware_identity: "RevOps-Sync-Engine"
  concurrency_engine: "Redis Distributed Mutex"
  lock_ttl_seconds: 60
workflow_steps:
  - "Inbound webhook arrives at middleware."
  - "Inspect header: If X-Originating-System equals RevOps-Sync-Engine, acknowledge HTTP 200 and discard immediately."
  - "Generate Redis lock key based on entity type and unique identifier."
  - "Evaluate lock state: If lock exists, reject execution and return HTTP 200. If lock does not exist, write key with 60-second TTL."
  - "Execute API mutation to downstream CRM."
  - "Retain lock until TTL expires to absorb asynchronous echo webhooks."
```

## 3. Rate Limiting, Throttling & Fault Tolerance
- **ServiceTitan Tenant Quota:** 300 requests per minute per tenant.
- **GoHighLevel Location Quota:** 100 requests per 10-second burst.
- **Middleware Rate-Limiting Policy:**
  - Token Bucket algorithm configured in Redis, restricting outbound ServiceTitan calls to a hard ceiling of 250 requests/minute (83% of tenant maximum).
  - Outbound GoHighLevel requests capped at 8 calls/second.
- **Retry Mechanics & Exponential Backoff:**
  - On HTTP `429 Too Many Requests` or HTTP `5xx Server Error`, middleware extracts the `Retry-After` header. If absent, workers apply truncated exponential backoff with randomized jitter:
    $$t_{\text{wait}} = \min\left(60, 2^{\text{attempt}} \times 0.5\right) \pm \text{jitter}$$
  - Retry intervals: Attempt 1 = 1s, Attempt 2 = 2s, Attempt 3 = 4s, Attempt 4 = 8s, Attempt 5 = 16s.
  - If an operation fails after 5 sequential attempts, the raw payload is written to the AWS SQS Dead Letter Queue (`st-ghl-dlq`).

## 4. ServiceTitan Pagination Traversal Strategy
ServiceTitan V2 API strictly requires cursor-based pagination utilizing `hasMore` and `continueFrom` for bulk datasets. Offsets and numeric page index queries are prohibited.

```yaml
pagination_traversal_specification:
  endpoint_pattern: "/dispatch/v2/tenant/392817492/jobs"
  query_parameters:
    pageSize: 50
  cursor_handling:
    - "Capture the continueFrom string token from the JSON response envelope."
    - "Validate response.hasMore boolean flag."
    - "If hasMore is true, issue subsequent GET request appending continueFrom parameter and store cursor checkpoint in DynamoDB table integration_sync_cursors."
    - "If hasMore is false, finalize sync cycle and update last_successful_sync_timestamp to UTC now."
```

---

# Operations Runbook & Standard Operating Procedures (SOP)

## SOP-001: Daily Integration Health Verification
**Frequency:** Daily at 06:00 AM Central Standard Time  
**Target SLA:** 15 Minutes  
**Assigned Owner:** RevOps Integration Engineer

1. **Verify Webhook Subscription Status:**
   - Log into the ServiceTitan Developer Portal (`developer.servicetitan.io`).
   - Navigate to **Tenant Settings** > **Integrations** > **Webhooks**.
   - Confirm subscription status is `Active` for Tenant `392817492`.
   - If status reads `Suspended` (triggered by 5 consecutive delivery timeouts), inspect the AWS API Gateway CloudWatch error logs, clear any endpoint blockers, and click **Reactivate**.
2. **Inspect SQS Dead Letter Queue (`st-ghl-dlq`):**
   - Open AWS CloudWatch Metrics Dashboard -> `SQS` -> `st-ghl-dlq`.
   - Verify `ApproximateNumberOfMessagesVisible == 0`.
   - If messages exist, initiate runbook procedure **SOP-004: DLQ Message Replay**.
3. **Verify Token Renewal Health:**
   - Query Redis memory store for active token keys:
     - `auth:servicetitan:bearer_token`
     - `auth:gohighlevel:access_token`
   - Confirm key TTL is strictly greater than 180 seconds.

## SOP-002: API Credential & App Secret Rotation
**Frequency:** Semi-Annual (Every 180 Days)  
**Assigned Owner:** Lead RevOps Architect

1. **Generate Secondary ServiceTitan App Secret:**
   - In ServiceTitan Developer Console, select App `Apex-RevOps-Engine` -> **Credentials**.
   - Click **Generate New Secret** (do not revoke the existing active secret).
2. **Update Secrets Manager in AWS:**
   - Navigate to AWS Secrets Manager -> `/production/apex/st_app_secret`.
   - Update the secret payload value to the newly generated key.
   - The AWS Lambda middleware automatically supports dual-secret verification during the 24-hour rollover window.
3. **Revoke Decommissioned Secret:**
   - After 24 hours of verified zero-error webhook ingestions, return to ServiceTitan Developer Console and click **Delete** on the old secret.

## SOP-003: Batch Historical Sync & Pipeline Reconciliation
**Frequency:** On-Demand (Following System Outage or Data Recovery)  
**Assigned Owner:** Tier 2 Technical Support

1. Calculate sync disruption window (e.g., `2026-09-11T12:00:00Z` to `2026-09-11T16:00:00Z`).
2. Run migration reconciliation script in dry-run mode:
   `docker run --rm apex-sync:latest npm run reconcile -- --from="2026-09-11T12:00:00Z" --entity="estimates" --dry-run=true`
3. Inspect output log: Confirm identified records match the discrepancy count between ST Reports and GHL Pipeline.
4. Execute live reconciliation batch write:
   `docker run --rm apex-sync:latest npm run reconcile -- --from="2026-09-11T12:00:00Z" --entity="estimates" --dry-run=false`
5. Monitor rate-limiter metrics in CloudWatch to ensure request throttling remains under 250 requests/minute.

## SOP-004: Dead Letter Queue (DLQ) Remediation & Message Replay
**Frequency:** Triggered on Alert (`st-ghl-dlq > 0`)  
**Assigned Owner:** RevOps Integration Engineer

1. In AWS Management Console, navigate to SQS -> `st-ghl-dlq` -> **Send and receive messages**.
2. Click **Poll for messages** and open the first message payload.
3. Read the injected metadata block: `x-error-code`, `x-error-message`, and `x-retry-count`.
4. Common failure patterns:
   - `Missing required field: businessUnitId`: Update GHL Opportunity mapping to inject fallback `101`.
   - `Invalid phone format`: Sanitize customer phone string via GHL Contact record directly.
5. Once data schema error is fixed, navigate to **Redrive messages** and redrive to source queue `st-ghl-booking-ingest`.

---

# Rollout Plan

```
[ PHASE 1: SANDBOX VALIDATION ] ─────────► [ PHASE 2: USER ACCEPTANCE TESTING ]
- Days 1 to 10                             - Days 11 to 18
- ST Integration Sandbox + GHL Sub-Demo    - 50 Complex Scenarios
- Mock API Calls & HMAC Validation         - Dispatch & CSR Sign-Off
              │                                          │
              ▼                                          ▼
[ PHASE 3: PRODUCTION PILOT ]   ─────────► [ PHASE 4: ENTERPRISE GO-LIVE ]
- Days 19 to 25                            - Day 26 Onward
- Single BU: HVAC Service (101)            - Full Rollout (BU 101 & 102)
- Live Shadowing in Dispatch               - 24/7 Hypercare & Alert Monitoring
```

## Phase 1: Sandbox & Mock Validation (Days 1 - 10)
- Provision ServiceTitan Developer Integration Sandbox connected to GoHighLevel Staging Sub-Account.
- Deploy middleware worker layer to AWS Lambda staging environment.
- Execute unit and synthetic load test suites (150 concurrent calls/minute) to confirm rate limiting, Redis caching, and error backoff mechanics.
- **Exit Gate:** 100% test pass rate across 40 distinct architectural unit tests; zero unhandled promise rejections.

## Phase 2: User Acceptance Testing (UAT) (Days 11 - 18)
- Configure live web form webhooks to route through staging middleware.
- Dispatch team and Chris Koehne run 50 end-to-end operational scenarios:
  - After-hours missed call generating SMS text-back and booking creation.
  - Emergency web lead generating booking and CSR dispatch notification.
  - Field technician presenting $8,500 estimate; confirming quote enters GHL nurture sequence after 24 hours.
  - Technician marking estimate "Sold"; confirming immediate cancellation of GHL nurture sequence.
- **Exit Gate:** Formal sign-off from General Manager and Dispatch Operations Lead.

## Phase 3: Single Business Unit Pilot (Days 19 - 25)
- Deploy middleware to AWS Production environment.
- Scope integration strictly to **Business Unit 101 (HVAC Service & Repair)**.
- Dispatchers maintain secondary monitoring of the unassigned booking tray to ensure zero dropped jobs.
- **Exit Gate:** 7 consecutive days of error-free booking ingestion; zero Dead Letter Queue messages; 100% of LSA leads acknowledged within 60 seconds.

## Phase 4: Full Enterprise Production Cutover (Day 26 Onward)
- Enable integration for **Business Unit 102 (HVAC Installation & Replacement)**.
- Schedule cutover window at 02:00 AM Central Standard Time to ensure zero customer impact.
- Activate automated CloudWatch SNS alerts to Chris Koehne and RevOps team for any HTTP 5xx or DLQ events.
- Initiate 14-day Hypercare support protocol with daily 15-minute operational standups.

---

# Escalation Policy

| Incident Code | Error Symptom / Trigger | Severity Level | Diagnostic Action & Resolution Workflow | Max Resolution SLA | Primary Owner | Secondary Escalation |
|---|---|---|---|---|---|---|
| ERR-AUTH-401 | HTTP 401 Unauthorized / Token Rejection | P1 | OAuth token renewal failed or App Secret revoked. Query AWS Secrets Manager; verify client credentials in ST Developer Portal; trigger manual Lambda token renewal script. | 30 Minutes | RevOps Systems Engineer | Lead Architect |
| ERR-WEBHOOK-HMAC | HMAC Signature Mismatch / Webhook Drop | P1 | ServiceTitan webhook failing signature validation or endpoint returning HTTP 500. Inspect TLS certificate expiration; verify App Secret hash calculation in Lambda; cycle secondary App Secret if compromised. | 45 Minutes | RevOps Systems Engineer | Security Ops Lead |
| ERR-RATE-429 | HTTP 429 Too Many Requests (> 5m continuous) | P2 | Outbound calls exceed 300 req/min tenant limit. Check Redis token bucket parameters; lower maximum throttle ceiling from 250 to 200 req/min; isolate runaway worker threads. | 90 Minutes | DevOps / Infrastructure Engineer | Lead Architect |
| ERR-SCHEMA-400 | HTTP 400 / 422 Bad Request Payload | P2 | Payload data validation failure (e.g., unmapped Job Type or Business Unit ID). Inspect message in `st-ghl-dlq`; update lookup dictionary in mapping engine; trigger DLQ redrive script. | 2 Hours | RevOps Integration Specialist | RevOps Systems Engineer |
| ERR-DEDUPE-DUP | Duplicate Customer Created in ST | P3 | Phone number normalization failed to catch formatted string. Inspect E.164 normalization logs; manually execute customer merge inside ServiceTitan; update regex sanitization rule. | 4 Hours | Tier 2 Support Specialist | RevOps Integration Specialist |
| ERR-STAGE-DESYNC | Opportunity Pipeline Stage Mismatch | P3 | GHL Opportunity stage does not reflect current ServiceTitan Job/Estimate status. Query ST API for entity status; trigger targeted reconciliation script for affected ST Job ID. | 8 Hours | Tier 2 Support Specialist | RevOps Systems Engineer |
| ERR-FIELD-DROP | Non-Critical Custom Field Missing | P4 | Minor custom field (e.g., Gate Code) failed to transfer. Check ST custom field array index; correct JSONPath mapping in dictionary; release patch during standard sprint cycle. | 3 Business Days | RevOps Integration Specialist | Product Owner |