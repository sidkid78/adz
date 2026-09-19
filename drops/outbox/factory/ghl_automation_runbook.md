# Apex Comfort Solutions: GoHighLevel & ServiceTitan Automation Engine Runbook

This document defines the production integration architecture, workflow logic, field mappings, standard operating procedures (SOP), error-handling runbooks, deployment verification protocols, and incident escalation policies for Apex Comfort Solutions (Central Texas HVAC).

---

## 1. System Architecture & Data Flow Topology

The automation layer connects inbound voice/digital touchpoints (Google Local Services Ads, LC-Phone/Twilio, and Web Intake Forms) directly into GoHighLevel (Sub-Account `loc_apex_ctx_9281a`) and synchronizes qualified emergency service jobs into ServiceTitan (Tenant ID `392019481`) via an asynchronous AWS Lambda integration middleware.

```
                              [ INBOUND TOUCHPOINTS ]
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
     [ Voice Call (Unanswered) ]                      [ Digital Form / LSA ]
     Numbers: +15125550110, +15125550111              Webhook / Native Ingest
                 │                                               │
                 ▼                                               ▼
  ┌───────────────────────────────┐               ┌───────────────────────────────┐
  │ Workflow 1: MCTB Engine       │               │ Workflow 2: Speed-to-Lead     │
  │ Trigger: Missed Call Status   │               │ Trigger: Inbound Web/LSA      │
  └──────────────┬────────────────┘               └──────────────┬────────────────┘
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         │
                                         ▼
                         ┌───────────────────────────────┐
                         │ Workflow 3: Conversational    │
                         │ SMS Triage & Geo-Validation   │
                         └──────────────┬────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
      [ Qualified Emergency ]                         [ Routine Service / Quote ]
                 │                                               │
                 ▼                                               ▼
  ┌───────────────────────────────┐               ┌───────────────────────────────┐
  │ Workflow 4: On-Call Tech      │               │ Workflow 5: Standard Dispatch │
  │ Tiered SMS/Voice Escalation   │               │ Non-Emergency Booking Queue   │
  └──────────────┬────────────────┘               └──────────────┬────────────────┘
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         │
                                         ▼
                         ┌───────────────────────────────┐
                         │ Outbound Middleware Webhook   │
                         │ POST https://api.apex-ops.net │
                         └──────────────┬────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                 [ AWS SQS / DLQ ]             [ ServiceTitan API v2 ]
                 "apex-ghl-st-dlq"             /jpm/v2/tenant/392019481/bookings
```

### 1.1 Core Identifiers and Authentication Specifications
* **GHL Sub-Account Location ID:** `loc_apex_ctx_9281a`
* **ServiceTitan Tenant ID:** `392019481`
* **ServiceTitan API Base URL:** `https://api.servicetitan.io`
* **Middleware Ingestion Endpoint:** `https://api.apex-ops.net/v1/ghl/sync-booking`
* **Middleware Authentication:** Shared Secret Header `X-Apex-Signature` verified via HMAC-SHA256 hash using key `whsec_908fbb23091ac84729104812a`
* **A2P 10DLC Brand Campaign:** Campaign ID `CMP-APX-829104` (Registered under Apex Comfort Solutions LLC, Standard Low-Volume Mixed Campaign)

---

## 2. Field-Mapping Specification

The following mapping governs synchronization between GoHighLevel contacts/custom values and the ServiceTitan v2 Booking Ingestion API (`POST /jpm/v2/tenant/392019481/bookings`).

| Source Entity & Field | Target ServiceTitan Field | Data Type | Transformation & Validation Rules | Null Handling Strategy | Sample Output Value |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `contact.phone` | `customer.mobilePhone` | String | Validate regex `^\+1[2-9]\d{9}$`; normalize to E.164. | Reject booking if null or invalid. | `"+15125550143"` |
| `contact.first_name` | `customer.firstName` | String | Trim whitespace; title case. | Fallback: `"Valued"` | `"Marcus"` |
| `contact.last_name` | `customer.lastName` | String | Trim whitespace; title case. | Fallback: `"Homeowner"` | `"Vance"` |
| `contact.address1` | `location.address.street` | String | Trim whitespace; parse unit/apt into `location.address.unit`. | Reject booking if empty. | `"4102 Spicewood Springs Rd"` |
| `contact.city` | `location.address.city` | String | Match against approved service list: Austin, Round Rock, Cedar Park, Pflugerville, Georgetown, Leander, Buda, Kyle. | Reject with tag `routing:out-of-service-area`. | `"Austin"` |
| `contact.postal_code` | `location.address.zip` | String | Validate 5-digit US ZIP format `^\d{5}$`. | Reject if missing or non-matching. | `"78759"` |
| `contact.hvac_system_type` | `summary` (Prefix) | String | Prepend to internal summary: `[System: {Val}]`. | Default: `"[System: Unknown]"` | `"[System: AC / Cooling]"` |
| `contact.urgency_level` | `priority` | String | Map: `Critical Emergency` -> `High`; `Same-Day Priority` -> `Medium`; `Standard Routine` -> `Low`. | Default: `"Medium"` | `"High"` |
| `contact.id` | `externalId` | String | Prepend prefix: `GHL-CONT-` + GHL Contact ID string. | None (System generated). | `"GHL-CONT-98a72b01c"` |
| `custom_values.business_unit_id` | `businessUnitId` | Long | Emergency calls route to `89201948101` (After-Hours BU); Daytime calls route to `89201948102` (Install/Service BU). | Default: `89201948102` | `89201948101` |
| `contact.issue_description` | `bookingComments` | String | Concatenate triage questions and responses into newline-separated text. | Fallback: `"Customer requested dispatch via automated SMS."` | `"AC completely blowing warm air. Newborn infant in residence."` |

---

## 3. Workflow Operational Specifications

### 3.1 Workflow 1: Missed-Call Text-Back (MCTB) Engine

* **Trigger:** Call Status equals `No-Answer`, `Busy`, or `Voicemail` across Tracking Numbers `+15125550110` (Main Pool) and `+15125550111` (Emergency LSA Voice).
* **Execution Parameters:**
  * Auto-Mark as Read: Disabled.
  * Allow Re-entry: Enabled with an enforced 15-minute hysteresis delay.
  * Stop on Response: Enabled.
* **Execution Path:**
  1. Add Contact Tag: `status:mctb-active`.
  2. Evaluate Condition: Time of call evaluation against US/Central time.
     * **Branch A (Daytime Operational Window):** Monday–Friday 07:30 to 18:00 CT, Saturday 08:00 to 14:00 CT.
       * Action 1: Enforce delay of exactly 15 seconds.
       * Action 2: Send Outbound SMS:
         > "Hi {{contact.first_name | default: 'there'}}, this is Sarah with Apex Comfort Solutions. We missed your call while assisting another Central Texas homeowner! Are you experiencing an urgent heating/cooling issue, or did you need to schedule routine service? Reply directly to this text—I'm standing by to help."
       * Action 3: Push in-app alert to Central Dispatch desk: `"Missed call from {{contact.phone}}. Daytime MCTB executed."`
       * Action 4: Set Pipeline `HVAC Inbound Triage` stage to `Missed Call - Pending Reply`.
     * **Branch B (After-Hours Emergency Window):** All times outside Branch A, including all day Sunday.
       * Action 1: Enforce delay of exactly 10 seconds.
       * Action 2: Send Outbound SMS:
         > "Hi {{contact.first_name | default: 'there'}}, you reached Apex Comfort Solutions after hours. Our dispatch phone is resting, but our emergency tech team is on call. If your AC or heat is down tonight, reply 'EMERGENCY' with your address, and I will alert our on-call technician immediately."
       * Action 3: Set Pipeline `HVAC Inbound Triage` stage to `After-Hours Lead - Contact Attempted`.
       * Action 4: Wait 180 seconds (3 minutes) for incoming contact reply.
       * Action 5: If no reply received after 180 seconds, send follow-up nudge:
         > "Apex Comfort check-in: We know HVAC problems can't wait in this Texas weather. Reply here anytime tonight if you want us to reserve the earliest emergency dispatch slot for you."

### 3.2 Workflow 2: Digital Speed-to-Lead Auto-Responder

* **Trigger 1:** Web Form Submitted (`form_id`: `apex_emergency_form` or `apex_contact_general`).
* **Trigger 2:** Google LSA Message Lead Ingested via native integration.
* **Execution Parameters:**
  * Availability: 24/7/365 continuous execution.
  * Execution SLA: Outbound delivery completed under 30 seconds from receipt.
* **Execution Path:**
  1. Normalize phone to E.164 via middleware parsing.
  2. Apply tags: `lead-source:web-form` or `lead-source:lsa-msg` and `bot-active`.
  3. Evaluate Form Selection `urgency_level`:
     * **Branch A (Urgent / Complete Outage):**
       * Action 1: Enforce delay of 20 seconds.
       * Action 2: Send SMS:
         > "Apex Comfort Solutions Priority Dispatch: Hi {{contact.first_name}}, we received your urgent service request from Google. Is the system completely offline right now, and are there any elderly residents or infants in the home? Reply here so we can prioritize your technician routing."
       * Action 3: Trigger automated internal call whisper to On-Call Dispatcher pool: `"Urgent web intake from {{contact.first_name}} in {{contact.city}}. Immediate response needed."`
       * Action 4: Apply tag `st-sync-pending`.
     * **Branch B (Routine / Replacement Quote):**
       * Action 1: Enforce delay of 45 seconds.
       * Action 2: Send SMS:
         > "Hi {{contact.first_name}}, thank you for contacting Apex Comfort Solutions! We received your request regarding HVAC service. What primary issue are you experiencing with your system, and what day works best for our technician to inspect it?"
       * Action 3: Advance to Workflow 3 triage queue.

### 3.3 Workflow 3: Conversational SMS Triage & Qualification

* **Trigger:** Contact Replied via SMS channel.
* **Pre-condition Filter:** Contact must possess tag `bot-active` AND must NOT possess tag `human-takeover`.
* **State Machine Processing Logic:**
  1. **Opt-Out Check:** If payload matches `STOP`, `UNSUBSCRIBE`, `CANCEL`, or `QUIT`, immediately trigger GHL native DND, strip tag `bot-active`, apply tag `status:opted-out`, and abort execution.
  2. **Intent Parsing Stage:**
     * **Emergency Matching:** String contains `not cooling`, `no ac`, `no heat`, `freezing`, `burning`, `smoke`, `leaking water`, `100 degrees`, `baby`, `infant`, `elderly`, `emergency`, `stopped working`, `blowing warm`, `sweating`, `tonight`, or `now`.
       * Set `urgency_level` = `Critical Emergency (No Cooling/Heating)`.
       * Apply tag `status:qualified-emergency`.
       * Remove tag `status:mctb-active`.
       * Send Outbound SMS:
         > "Understood. No cooling or heating in our climate is serious. Our on-call trucks carry full diagnostic parts. What is the exact street address and ZIP code for the home, and are you the homeowner?"
       * Apply tag `triage:step-2-pending`.
     * **Routine Matching:** String contains `tune-up`, `maintenance`, `estimate`, `quote`, `system replacement`, `new unit`, `price`, `checkup`, `service call`, `next week`, or `filter`.
       * Set `urgency_level` = `Standard Routine Repair`.
       * Apply tag `status:qualified-routine`.
       * Remove tag `status:mctb-active`.
       * Send Outbound SMS:
         > "Got it! We can take care of that for you without emergency dispatch rates. What is your property address, and do you prefer a morning (8 AM–12 PM) or afternoon (12 PM–5 PM) arrival window?"
       * Apply tag `triage:step-2-pending`.
     * **Ambiguous Intent Fallback:**
       * Send Outbound SMS:
         > "Thanks for getting back to us. To get you to the right dispatcher, could you let us know: Is your system completely stopped, or are you looking to book a routine repair or system replacement estimate?"
       * Increment counter variable `triage_attempts`. If `triage_attempts` > 1, immediately apply tag `human-takeover`, strip `bot-active`, send Slack alert to `#dispatch-exceptions`, and exit workflow.
  3. **Territory Verification Stage (Step 2):**
     * Evaluate extracted address against approved ZIP codes: `78701`, `78702`, `78703`, `78704`, `78705`, `78750`, `78759`, `78613`, `78641`, `78660`, `78664`, `78626`, `78628`, `78610`, `78640`.
     * **If Valid Service Area:** Apply tag `service-address-validated`, set pipeline stage to `Triage Complete - Ready for Dispatch`, and transition to Workflow 4 (if emergency) or Workflow 5 (if routine).
     * **If Out-of-Service Area:**
       * Apply tag `routing:out-of-service-area`.
       * Send Outbound SMS:
         > "Thank you for reaching out to Apex Comfort Solutions. It appears your property is located outside our licensed Central Texas service territory. To get you taken care of as fast as possible, we recommend contacting a local HVAC specialist in your county. We apologize for the inconvenience!"
       * Move Pipeline to `Closed - Non Serviceable Area`.

### 3.4 Workflow 4: Technician On-Call Escalation Engine

* **Trigger:** Contact Tag Added `status:qualified-emergency` outside business hours.
* **Escalation Loop Logic:**
  1. Apply tag `dispatch:pending-tech-ack`.
  2. Send High-Priority Dispatch SMS to Tier 1 Technician Marcus Vance (`+15125550181`):
     > "🚨 APEX AFTER-HOURS DISPATCH:
     > Client: {{contact.first_name}} {{contact.last_name}}
     > Phone: {{contact.phone}}
     > Address: {{contact.address1}}, {{contact.city}}
     > Issue: {{contact.urgency_level}} - System: {{contact.hvac_system_type}}
     > Reply 'ACCEPT {{contact.id}}' within 300 seconds."
  3. Wait 300 seconds (5 minutes) for incoming SMS response containing `"ACCEPT {{contact.id}}"`.
  4. **Condition A: Tier 1 Tech Acknowledges:**
     * Remove tag `dispatch:pending-tech-ack`.
     * Apply tag `dispatch:tech-assigned`.
     * Set Custom Field `assigned_tech` = `"Marcus Vance"`.
     * Send Outbound SMS to Customer:
       > "Good news, {{contact.first_name}}: Your emergency ticket has been accepted by our on-call HVAC technician. They are reviewing your details and will call you from this number within 15 minutes to confirm arrival time. For urgent updates, reply here."
     * Dispatch webhook payload to ServiceTitan Booking Ingestion endpoint.
  5. **Condition B: Tier 1 Tech Times Out (No response after 300s):**
     * Log escalation event: `"Tier 1 (+15125550181) unacknowledged after 300s. Escalating to Tier 2."`
     * Send High-Priority Dispatch SMS to Tier 2 Technician Elena Rodriguez (`+15125550182`):
       > "🚨 APEX AFTER-HOURS DISPATCH (ESCALATION TIER 2):
       > Client: {{contact.first_name}} {{contact.last_name}}
       > Phone: {{contact.phone}}
       > Address: {{contact.address1}}, {{contact.city}}
       > Issue: {{contact.urgency_level}} - System: {{contact.hvac_system_type}}
       > Reply 'ACCEPT {{contact.id}}' within 300 seconds."
     * Initiate automated outbound phone dial to Elena Rodriguez (`+15125550182`) with voice whisper: `"Emergency HVAC lead unacknowledged. Check text immediately to claim dispatch."`
     * Wait 300 seconds (5 minutes) for response.
  6. **Condition C: Tier 2 Tech Times Out (No response after additional 300s):**
     * Remove tag `dispatch:pending-tech-ack`.
     * Apply tag `dispatch:escalated-to-owner`.
     * Initiate direct emergency call to Business Owner Chris Koehne (`+15125550199`).
     * Whisper message upon answer: `"CRITICAL FAILURE: Two on-call technicians failed to claim emergency HVAC lead {{contact.first_name}} {{contact.last_name}} at {{contact.address1}}. Immediate manual intervention required."`
     * Send Priority Alert SMS to `+15125550199`.
     * Dispatch emergency webhook to Slack channel `#owner-critical-alerts`.

---

## 4. Standard Operating Procedures (SOP)

### SOP-APX-01: Dispatcher Human Takeover Protocol

* **Purpose:** Ensures seamless suspension of automated SMS flows whenever human dispatchers or technicians engage directly with a homeowner.
* **Scope:** Applies to all GHL web dashboard users, mobile app dispatchers, and ServiceTitan office users.
* **Procedure:**
  1. When viewing an active conversation in the GoHighLevel Conversations Inbox, check the upper header for the tag `bot-active`.
  2. Before typing a manual text or clicking to initiate a phone call, click the contact tags area and apply the tag `human-takeover`.
  3. The automation engine automatically executes the following actions within 2 seconds:
     * Strips the tag `bot-active`.
     * Changes custom field `bot_active` to `False`.
     * Drops the contact from all active execution branches in Workflows 1, 2, and 3.
  4. Communicate with the customer directly. All incoming messages will remain visible in the inbox without triggering bot evaluation.
  5. **Re-enabling the Bot:** If the interaction is complete and the customer requires automated follow-up scheduling, manually remove `human-takeover` and apply `bot-active`.

### SOP-APX-02: Technician Emergency Claiming Procedure

* **Purpose:** Standard operating sequence for field technicians responding to after-hours emergency calls.
* **Scope:** All licensed HVAC technicians on the after-hours rotation schedule.
* **Procedure:**
  1. Upon receiving the high-priority dispatch text containing `"🚨 APEX AFTER-HOURS DISPATCH"`, read the client address, system type, and issue description.
  2. Verify that you can arrive at the property within 90 minutes.
  3. Reply directly to the incoming SMS with the exact phrase: `ACCEPT [Contact_ID]` (Example: `ACCEPT 98a72b01c`).
  4. Confirm that you receive the confirmation SMS back from the system within 15 seconds: `"Dispatch assigned. Customer notified. Contact homeowner immediately."`
  5. Open the ServiceTitan Mobile application on your device. Refresh the job tray.
  6. Locate the automatically created Booking under the `Unassigned After-Hours` queue.
  7. Place an outbound voice call to the customer via the ServiceTitan masked phone dialer within 15 minutes of accepting the lead to provide an exact estimated time of arrival (ETA).
  8. If your vehicle is delayed or you encounter a safety hazard en route, text or call the Dispatch Supervisor line (`+15125550100`) immediately to initiate manual re-routing.

---

## 5. Technical Error Remediation & Dead-Letter Queue (DLQ) Handling

### 5.1 Telephony Error Codes (Twilio / LC-Phone)

The integration monitors outbound delivery receipts via webhook callback. The following operational procedures address transmission failures:

```
                      [ Twilio Status Callback ]
                                   │
             ┌─────────────────────┴─────────────────────┐
             ▼                                           ▼
      [ Error 30003 / 30005 ]                     [ Error 30007 / 30008 ]
   (Unreachable Destination)                   (Carrier Filtering / 10DLC)
             │                                           │
             ▼                                           ▼
  [ Apply: flag:invalid-phone ]              [ Apply: flag:carrier-violation ]
  [ Strip: bot-active ]                      [ PAUSE Outbound Automations ]
  [ Switch to Email Rescue ]                 [ Alert RevOps Lead in Slack ]
             │                                           │
             ▼                                           ▼
  [ Alert Dispatch Desk ]                    [ Review Message Template Copy ]
```

* **Error 30003 (Unreachable Destination Handset) & 30005 (Unknown / Inactive Number):**
  1. Automated handler captures webhook failure status.
  2. Contact is tagged with `flag:invalid-phone` and `bot-active` is stripped.
  3. If an email address exists on file, the system dispatches Email Template `EMAIL-RESCUE-URGENT`:
     * Subject: *"Urgent: Apex Comfort Solutions trying to reach you regarding your HVAC request"*
     * Body prompts the customer to call the dispatch desk directly at `+15125550100`.
  4. An alert task is generated in ServiceTitan for Central Dispatch to verify customer phone details via white pages lookup.

* **Error 30007 (Carrier Violation) & 30008 (Unknown Carrier Filtering):**
  1. Middleware catches the rejection and fires an immediate alert to Slack channel `#revops-telephony`.
  2. Outbound campaign throttling drops to 1 message every 10 seconds per sending number.
  3. The RevOps Administrator must immediately audit recent message templates for blacklisted keywords (e.g., promotional terms, excessive punctuation, missing opt-out disclosure).
  4. Ensure outbound signature complies with the approved CTIA template: Brand identification and `Reply STOP to cancel` must be present in every introductory message.

### 5.2 ServiceTitan API Middleware Failure & SQS DLQ Runbook

When the integration worker encounters errors transmitting bookings to ServiceTitan API endpoint `/jpm/v2/tenant/392019481/bookings`, messages are routed through an automated retry pipeline before entering the Dead-Letter Queue (`arn:aws:sqs:us-east-1:482910481029:apex-ghl-st-dlq`).

#### Failure Modes and Handling Procedures

* **HTTP 401 Unauthorized:**
  * Root Cause: ServiceTitan Integration Client Secret or Application Token has expired.
  * Auto-Action: Lambda middleware halts queue consumption and attempts to refresh bearer token using credentials stored in AWS Secrets Manager (`prod/servicetitan/app_credentials`).
  * Resolution: If token refresh fails, execute manual token regeneration via ServiceTitan Developer Portal (`https://developer.servicetitan.io`), update secret key `prod/servicetitan/app_credentials`, and restart worker.

* **HTTP 400 Bad Request / Validation Failure:**
  * Root Cause: Payload violates ServiceTitan schema (e.g., invalid ZIP code format, missing required customer last name, or unmapped business unit ID).
  * Auto-Action: Message immediately routed to DLQ with message attribute `ErrorReason` set to HTTP response body.
  * Tag applied to GHL Contact: `st-sync-failed`.

* **HTTP 429 Rate Limit Exceeded:**
  * ServiceTitan enforces a threshold of 60 requests per minute per application client.
  * Auto-Action: Worker executes exponential backoff with full jitter:
    $$T_{\text{sleep}} = 2^{\text{attempt}} \times 1000\text{ms} + \text{Uniform}(0, 500\text{ms})$$
  * Max retries: 5 attempts before pushing to DLQ.

#### Step-by-Step DLQ Inspection & Redrive Procedure

1. Open administrative terminal with access to AWS CLI credentials for account `482910481029`.
2. Inspect the current backlog depth of the Dead-Letter Queue:
   ```bash
   aws sqs get-queue-attributes \
     --queue-url "https://sqs.us-east-1.amazonaws.com/482910481029/apex-ghl-st-dlq" \
     --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
   ```
3. Read the oldest failed payload to evaluate the failure reason:
   ```bash
   aws sqs receive-message \
     --queue-url "https://sqs.us-east-1.amazonaws.com/482910481029/apex-ghl-st-dlq" \
     --attribute-names All \
     --message-attribute-names All \
     --max-number-of-messages 1 \
     --visibility-timeout 60
   ```
4. Parse the `ErrorReason` attribute in the returned JSON. If the error was due to an unmapped ZIP code or malformed address:
   * Open the GHL contact record indicated in `externalId`.
   * Correct the address fields in the contact record.
5. Once data or code corrections are applied, initiate an automated DLQ redrive to replay messages back into the active processing queue:
   ```bash
   aws sqs start-message-move-task \
     --source-arn "arn:aws:sqs:us-east-1:482910481029:apex-ghl-st-dlq" \
     --destination-arn "arn:aws:sqs:us-east-1:482910481029:apex-ghl-st-main-queue"
   ```
6. Monitor the redrive progress until queue depth reaches zero:
   ```bash
   aws sqs list-message-move-tasks \
     --queue-url "https://sqs.us-east-1.amazonaws.com/482910481029/apex-ghl-st-dlq"
   ```

---

## 6. Phased Rollout, Verification & Cutover Plan

The cutover schedule migrates Apex Comfort Solutions from legacy manual answering services to the automated speed-to-lead engine.

```
[ Day 1: Staging Validation ] ──► [ Day 2: Internal Pilot ] ──► [ Day 3: Controlled Cutover ] ──► [ Day 4: Full Go-Live ]
  - Verify A2P 10DLC               - Run 10 Test Calls            - Route 100% Daytime            - Route 100% All Traffic
  - Validate Webhook HMAC          - Force Tier 1/2 Escalation    - Monitor First 20 MCTB         - Continuous DLQ Monitoring
  - Dry-run ServiceTitan           - Confirm ST Booking Sync      - Verify Human Takeover         - Daily KPI Tracking
```

### Day 1 (Wednesday): Pre-Cutover Staging & Infrastructure Validation
* Confirm A2P 10DLC registration status shows `VERIFIED` and `ACTIVE` under Twilio Sub-Account for Apex Comfort Solutions.
* Verify webhook secret tokens and IAM permissions for Lambda worker `ghl-servicetitan-bridge`.
* Execute synthetic dry-run sync using mock payload to ServiceTitan sandbox environment. Confirm booking displays in dispatch tray with correct business unit ID (`89201948102`).
* Audit all GHL Custom Values: Confirm primary technician mobile numbers (`+15125550181`, `+15125550182`) and owner mobile (`+15125550199`) are accurate.

### Day 2 (Thursday): Internal End-to-End Pilot & Failure Simulation
* **Test 1 (Daytime MCTB):** Dial tracking number `+15125550110` from unassigned mobile phone at 14:00 CT. Disconnect after 2 rings. Confirm `SMS-MCTB-01-BIZ` delivers within 15 seconds.
* **Test 2 (After-Hours MCTB):** Dial tracking number `+15125550111` at 18:30 CT. Confirm `SMS-MCTB-02-AFTERHOURS` delivers within 10 seconds.
* **Test 3 (NLP Intent Classification):** Reply to the after-hours text: *"My AC is blowing hot air and I have an infant at home."*
  * Confirm `urgency_level` updates to `Critical Emergency`.
  * Confirm `status:qualified-emergency` tag is applied.
  * Confirm Tier 1 Tech Marcus Vance receives dispatch alert text within 30 seconds.
* **Test 4 (Escalation Timeout Simulation):** Allow Marcus Vance's 300-second timer to expire without replying.
  * Confirm system triggers Tier 2 dispatch SMS and outbound call whisper to Elena Rodriguez at $T+300$ seconds.
* **Test 5 (Technician Claiming & Booking Push):** Elena Rodriguez replies `ACCEPT [Contact_ID]`.
  * Confirm customer receives confirmation text: `"Your emergency ticket has been accepted by our on-call HVAC technician."`
  * Confirm ServiceTitan live booking is created in the unassigned queue within 45 seconds.

### Day 3 (Friday): Controlled Production Cutover
* **07:30 CT:** Switch inbound Google Local Services Ads call forwarding number from legacy call center to GHL Tracking Pool `+15125550110`.
* **08:00–12:00 CT:** Live monitor the first 20 inbound missed calls. Dispatch Lead oversees GHL inbox to verify that MCTB executes reliably and human dispatchers can override bots using the `human-takeover` tag without friction.
* **18:00 CT:** Enable After-Hours Escalation Engine (Workflow 4) for weekend coverage.

### Day 4 (Saturday & Sunday): Full Operational Go-Live
* System operates autonomously under after-hours protocols.
* Operations supervisor checks SQS DLQ depth every 4 hours via AWS CloudWatch Dashboard `Apex-Automation-Ops`.

### Rollback Criteria & Abort Procedure
If any of the following critical triggers occur during the cutover window, the RevOps Lead must abort the cutover immediately:
1. Outbound SMS delivery failure rate exceeds 5% across any consecutive 30-minute block (indicates carrier filtering or 10DLC suspension).
2. ServiceTitan API returns repeated 5xx responses or authentication failures persisting for $>15$ minutes without automated recovery.
3. Automated voice whispers fail to dial on-call technician phones during an emergency escalation event.

**Immediate Abort Execution Sequence:**
1. Log into Google LSA Dashboard -> Direct Connect Settings -> Revert primary call forwarding number back to Answering Service Call Center (`+15125550190`).
2. Log into GoHighLevel -> Settings -> Workflows -> Toggle Workflows 1, 2, 3, and 4 to **Draft** status.
3. Send notification to `#all-dispatch` on Slack: `"Automation cutover aborted. All incoming calls revert to manual phone answering."`

---

## 7. Incident Escalation Policy & Service Level Agreements

### 7.1 Severity Classification & Operational SLAs

| Severity Level | Operational Impact Criteria | Target Response SLA | Status Update Cadence | Target Resolution SLA | Responsible Lead |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SEV-1 (Critical)** | Core telephony failure; MCTB completely silent; after-hours emergency calls failing to alert technicians; booking bridge down. | $\le 15$ Minutes | Every 30 Minutes | $\le 2$ Hours | Systems Integration Architect |
| **SEV-2 (Major)** | ServiceTitan booking sync failure (DLQ expanding); NLP misclassifying emergency leads as routine; high SMS latency (>60s). | $\le 30$ Minutes | Every 60 Minutes | $\le 4$ Hours | RevOps Automation Specialist |
| **SEV-3 (Minor)** | Single carrier delivery degradation; individual customer data mapping mismatch; ambiguous NLP loop fallback firing excessively. | $\le 2$ Hours | Every 4 Hours | $\le 24$ Hours | Field Dispatch Supervisor |
| **SEV-4 (Low)** | Non-critical tag naming update; minor dashboard reporting discrepancy; routine copy adjustment request. | $\le 8$ Hours | Daily | 3 Business Days | GHL Sub-Account Administrator |

### 7.2 Incident Escalation Roster & Contacts

* **First Response: Cloud Operations & RevOps Engineering**
  * Contact: RevOps On-Call Desk
  * Phone: `+15125550170`
  * Email / Pager: `ops-alert@apexcomfortsolutions.com`
  * Responsibilities: Initial triage of CloudWatch alarms, Twilio delivery dashboards, and GHL workflow execution logs.
* **Secondary Escalation: Systems Integration Architect**
  * Contact: Lead Systems Architect (External Middleware Consultant)
  * Phone: `+15125550175`
  * Email: `architect@integration-specialists.io`
  * Responsibilities: Code patches on AWS Lambda middleware, SQS DLQ redrive execution, ServiceTitan OAuth credential refreshes, and API schema reconciliations.
* **Executive Escalation & Business Authority: General Manager / Owner**
  * Contact: Chris Koehne (Owner, Apex Comfort Solutions)
  * Direct Mobile: `+15125550199`
  * Responsibilities: Business risk sign-off, emergency vendor escalation, and operational authority to trigger full system rollback.