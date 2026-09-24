# Standard Operating Procedure & Technical Architecture: After-Hours Dispatch Guardrails

**Document Identifier:** SOP-ENG-TX-088  
**Organization:** Apex Comfort Solutions  
**Operational Scope:** Central Texas (Travis, Williamson, and Northern Hays Counties)  
**Integrated Systems:** GoHighLevel (GHL), ServiceTitan (ST), Twilio Voice/Messaging API, Make.com Middleware, SaneBox for Google Workspace  
**Document Revision:** Production Release 2.4  

---

## 1. Emergency Triage Decision Matrix

All inbound communications received outside regular business hours (17:00:00 to 07:00:00 CST Monday through Friday, and all weekends/company holidays) are processed by the automated conversational engine and dispatcher intake console. Incoming service requests must be triaged strictly against measurable environmental conditions, life-safety hazards, and pre-authorized financial commitments.

### 1.1 Triage Severity & Dynamic Response Matrix

| Urgency Level | Issue Classification | Environmental & Physical Criteria | Dispatch Fee & Billing Model | SLA: Automated First Response | SLA: Technician Mobilization | System Routing & GHL Tag Assignment |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **P1-Critical** | Total Cooling/Heating Loss with Health Hazard or Structural Threat | Outdoor ambient temp >90°F or <38°F; total HVAC failure with vulnerable occupants (infants, seniors >65, respiratory medical devices); active mechanical water pouring (>3 GPM) into living spaces; electrical arcing at compressor disconnect. | $189.00 Emergency Dispatch Fee pre-authorized via SMS/voice token before rollout; billed at $215.00/hr emergency labor rate. | $\le$ 15 Seconds (Missed Call Text Back / AI Triage) | $\le$ 45 Minutes from fee authorization | Tags: `status:qualified-emergency`, `priority:p1-critical`, `dispatch:pending-tech-ack`. Route to JobTypeId: 204 in ServiceTitan. |
| **P2-Urgent** | High-Risk System Failure with Containment | Outdoor ambient temp 80°F–89°F or 39°F–48°F; system short-cycling or locked out; single zone failure in multi-zone residential property; secondary drain pan actively filling but float switch operational. | Standard Diagnostic Fee ($99.00); priority scheduling for first morning dispatch window (07:00–09:00 CST). | $\le$ 60 Seconds | Next-Day AM Priority (07:00 CST Rollout) | Tags: `status:routine-service`, `priority:p2-urgent-nextday`. Auto-generate ServiceTitan booking under JobTypeId: 201. |
| **P3-Routine** | Maintenance & Non-Urgent Diagnostic Issues | Indoor temp stable (68°F–77°F); outdoor ambient temp 50°F–79°F; routine filter changes, seasonal tune-up inquiries, minor condensation line sweating, ductwork airflow balancing inquiries. | Standard Diagnostic Fee ($99.00) or Active Maintenance Agreement coverage ($0 diagnostic). | $\le$ 2 Minutes (Automated SMS) | Next Available Regular Business Hours Slot | Tags: `status:routine-service`, `priority:p3-routine`. Push to ServiceTitan morning review queue under JobTypeId: 202. |
| **P4-Reject** | Out-of-Area or Ineligible Scope | Physical property address outside Travis, Williamson, or Northern Hays counties; commercial ammonia refrigeration systems; oil-fired furnaces; non-contract industrial chillers. | No charge. Zero fee authorized. | $\le$ 2 Minutes | No dispatch | Tags: `routing:out-of-service-area`, `triage_urgency_level:P4-Reject`. Automated SMS referral to regional reciprocal mechanical contractors. |
| **P0-Hazard** | Catastrophic Gas Breach or Structure Fire | Natural gas odor detected indoors (>1% lower explosive limit); active carbon monoxide alarms sounding (>35 PPM); active electrical smoke or visible flames at air handler or condenser. | Zero charge. Evacuation priority. | Immediate Voice Script Drop | Municipal Emergency Services (911) | Direct voice script command to evacuate premises immediately. Dispatcher initiates municipal CAD bridge. Tag: `status:life-safety-evacuate`. |

### 1.2 Customer Diagnostic & Pre-Authorization Protocols

To prevent unnecessary field technician dispatch during off-hours, the GoHighLevel Conversational AI Engine and intake dispatchers must execute these deterministic qualification gates:

1. **Environmental Temperature Check:**
   - The system executes a REST call to OpenWeatherMap API using the contact's five-digit ZIP code.
   - The value is stored dynamically in `contact.ambient_temp_snapshot`.
   - If `contact.ambient_temp_snapshot` is between 49°F and 79°F, the system automatically disallows `P1-Critical` classification unless a life-safety override is manually applied by the Tier-3 Supervisor.

2. **Thermostat Reboot & High-Head Reset Verification:**
   - The client is instructed via conversational AI or human dispatcher:
     - Step 1: Switch thermostat system mode to `OFF`.
     - Step 2: Cycle the mechanical disconnect or dual-pole 240V breaker labeled `A/C` to `OFF` for 180 seconds, then toggle back to `ON`.
     - Step 3: Switch thermostat mode to `COOL` and adjust setpoint 3°F below room temperature.
   - If the condensing unit does not engage within 5 minutes, proceed immediately to financial fee authorization.

3. **Mandatory Fee Pre-Authorization:**
   - Dispatchers and automated bots must present the explicit $189.00 fee:  
     *"Apex Comfort Solutions dispatches master technicians after hours for a diagnostic authorization fee of $189.00, which covers full system troubleshooting and safety isolation. Do you authorize this charge to proceed?"*
   - Field `after_hours_fee_accepted` must evaluate to `True` (via SMS string match `AUTHORIZE_189` or digital intake link confirmation) before any technician notification is generated.

---

## 2. On-Call Escalation Protocol Documentation & Dispatcher Workflow Guardrails

### 2.1 Escalation Routing Architecture & Communication Channels

The escalation sequence is orchestrated through Make.com connecting GoHighLevel, Twilio Voice API, and ServiceTitan.

```
Incoming Emergency (P1-Critical + Fee Authorized)
                       │
                       ▼
          [Capacity Guardrail Engine]
          ├── Count >= 2 ───────────────► Standby Mode (Next-Day 07:00 AM)
          └── Count < 2
                       │
                       ▼
          [Tier 1: Primary On-Call Tech]
          ├── SMS Notification + Portal Ack
          └── 5-Minute Timer
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
      [Acknowledged]       [No Response]
             │                   │
   Assign ServiceTitan           ▼
   Job & Alert Client     [Tier 2: Secondary Lead Tech]
                          ├── Twilio Whisper Call Drop
                          ├── SMS Push Alert
                          └── 5-Minute Timer
                                 │
                       ┌─────────┴─────────┐
                       ▼                   ▼
                [Acknowledged]       [No Response]
                       │                   │
             Assign ServiceTitan           ▼
             Job & Alert Client     [Tier 3: Owner / Escalation Lead]
                                    ├── Continuous Phone Loop
                                    ├── SaneBox VIP Emergency Dispatch
                                    └── Manual Third-Party Overflow
```

#### Escalation Tier Contact Specifications

1. **Tier 1: Primary On-Call Field Specialist**
   - Routing: Direct SMS alert delivered to `{{custom_values.tech_on_call_tier1_phone}}` containing job location, customer complaint, system ambient metrics, and direct acknowledgment link.
   - Acknowledgment Window: 5 minutes (300 seconds).
   - Mechanism of Acknowledgment: Technician clicks unique webhook link (`https://hook.make.com/ack-tier1?jobId=...`) or replies with digit `1`.
   - Action on Acknowledgment: System assigns technician ID to ServiceTitan Job Booking, tags contact `dispatch:tech-assigned`, and sends customer arrival ETA via Twilio SMS.

2. **Tier 2: Regional Backup Field Specialist (Twilio Whisper Drop)**
   - Trigger: Tier-1 timer expires without dynamic acknowledgment.
   - Routing: Simultaneous SMS and automated outbound Twilio voice call to `{{custom_values.tech_on_call_tier2_phone}}`.
   - Voice Payload: Twilio executes automated voice drop utilizing Amazon Polly voice synthesis:
     ```xml
     <Response>
        <Say voice="Polly.Joanna">This is the Apex Comfort Automated Dispatch System. Primary technician has failed to acknowledge an emergency in Austin. Press 1 to accept, or 9 to reject.</Say>
        <Gather numDigits="1" action="https://hook.make.com/twilio-gather-tier2" method="POST" timeout="10"/>
     </Response>
     ```
   - Acknowledgment Window: 5 minutes (300 seconds).
   - Action on Failure: System records non-responsive incident in the technician reliability log and immediately escalates to Tier 3.

3. **Tier 3: Executive Override (Chris Koehne - General Manager / Owner)**
   - Target: `{{custom_values.owner_emergency_phone}}` (512-555-0199) and VIP email `dispatch@apexcomfort.com`.
   - Routing: Continuous outbound telephony ring loop bypassing mobile Do Not Disturb (DND) configurations via high-priority Twilio caller profile.
   - Failover Capability: Chris Koehne receives direct one-click authorization to reroute the call to contracted external mechanical overflow partner (Round Rock Emergency Mechanical Services) or personally take field command.

### 2.2 Dispatcher Workflow Guardrails & Operational Constraints

#### 2.2.1 Nightly Physical Truck Roll Capacity Ceiling
To preserve technician safety, adhere to Texas Department of Public Safety commercial driving standards, and prevent morning service disruption:
- **Maximum Limit:** Exactly two (2) physical emergency dispatches are permitted per night between the hours of 17:00:00 and 06:30:00 CST across the entire Central Texas service territory.
- **Enforcement Engine:** Make.com verifies `{{custom_values.nightly_dispatch_count}}` prior to triggering Tier 1 escalation.
- **Hard Cutoff Logic:**
  - If `{{custom_values.nightly_dispatch_count}}` $\ge$ 2 OR the current timestamp is between 00:00:00 and 06:30:00 CST:
    1. Suppress all field technician paging.
    2. Route contact to `STATUS: STANDBY`.
    3. Transmit automated customer SMS:  
       *"Apex Comfort Update: Our emergency field technicians are fully engaged on critical life-safety repairs tonight. Your service ticket is locked in as Priority #1 for our 07:00 AM emergency morning rollout. We will contact you at 06:45 AM to confirm technician arrival."*
    4. Auto-generate booking in ServiceTitan scheduled for 07:00:00 CST under JobTypeId: 204.

#### 2.2.2 Service Territory Boundary Geofencing
Field dispatches are strictly restricted to coordinates falling within the defined service polygon:
- **Southern Boundary:** FM 150 / Kyle Parkway (Hays County).
- **Northern Boundary:** State Highway 29 / Georgetown Loop (Williamson County).
- **Western Boundary:** Lake Travis / RM 620 corridor.
- **Eastern Boundary:** SH 95 / Elgin city limits.
- *Guardrail Execution:* Any ZIP code falling outside the verified directory (78613, 78641, 78660, 78664, 78681, 78701–78759) triggers tag `routing:out-of-service-area` and halts technician paging workflows.

#### 2.2.3 Dead-Man Switch (Human-In-The-Loop Safeguard)
To prevent automated conversational bots from sending conflicting or confusing instructions while a human specialist or dispatcher is communicating with a customer:
- **Listening Agent:** Make.com webhook captures all outbound call recordings, manual SMS sends, and call logs from both ServiceTitan and GHL conversation panels.
- **Execution Criteria:** If an Apex Comfort employee initiates an outbound SMS or telephone call to a lead or customer with an active conversation bot:
  1. Add tag `human-takeover`.
  2. Set custom field `bot_active` = `False`.
  3. Evict contact immediately from workflow `[OPS] 01 - After-Hours MCTB & AI Triage`.
  4. Terminate any pending AI follow-up timers.

### 2.3 Middleware API Integration Payloads

#### Make.com to ServiceTitan Booking Creation
When a P1 emergency is validated and claimed by a technician, Make.com issues an authenticated HTTP POST request to the ServiceTitan API (`POST /jpm/v2/tenant/394829104/bookings`):

```json
{
  "customerId": 1094821,
  "locationId": 1204855,
  "businessUnitId": 14,
  "jobTypeId": 204,
  "priority": "Urgent",
  "campaignId": 88,
  "start": "2024-07-19T03:30:00Z",
  "summary": "AFTER-HOURS P1 EMERGENCY - $189 FEE APPROVED. Ambient: 103F. Failure: Total system lockout.",
  "isSendConfirmation": false,
  "customFields": [
    {
      "name": "TriageUrgencyLevel",
      "value": "P1-Critical"
    },
    {
      "name": "DispatchFeeCollected",
      "value": "189.00"
    },
    {
      "name": "AssignedTechMobile",
      "value": "+15125550142"
    }
  ]
}
```

---

## 3. Dispatcher Email Inbox Sanitization Ruleset (SaneBox Deployment)

To ensure zero operational clutter and sub-60-second reaction times to incoming emergency digital communications, the dispatch mailbox (`dispatch@apexcomfort.com`) operates under an automated SaneBox AI sanitization and header-filtering ruleset.

### 3.1 Architectural Folder Topology

```
dispatch@apexcomfort.com (Primary Ingestion)
  │
  ├─► SaneBox Parsing Engine
  │     │
  │     ├─► [Inbox] ──────────────────────► Real-Time Webhook to Make.com / Pager Loop
  │     │     - Verified Emergency Signals
  │     │     - Priority Vendor Dispatches
  │     │     - DLQ System Alerts
  │     │
  │     ├─► [@SaneUrgentDispatch] ────────► High-Priority Secondary Sweep
  │     │     - Keyword hits from unverified client domains
  │     │
  │     ├─► [@SaneNextBusinessDay] ───────► Released at 06:30 AM CST to Day CSR Board
  │     │     - Routine Maintenance & Billing Inquiries
  │     │
  │     ├─► [@SaneLater] ─────────────────► Bulk Operational Correspondence
  │     │     - Supplier statements, non-urgent delivery updates
  │     │
  │     ├─► [@SaneNews] ──────────────────► Marketing & Newsletters (Suppressed)
  │     │
  │     └─► [@SaneBlackHole] ─────────────► Permanent Sender Block & Null Route
```

### 3.2 Deterministic SaneBox Filtering Rules

#### Rule Profile 1: Critical System & Emergency Inbox Retention
Emails meeting these criteria are delivered directly to the primary root `Inbox` and trigger an instant Make.com webhook to parse body content:
- **Condition 1 (System Core Senders):** Sender matches `alerts@servicetitan.com`, `dlq-monitor@apexcomfort.com`, or `notifications@twilio.com`.
- **Condition 2 (Subject String Verification):** Subject line matches regular expression:
  ```regex
  (?i)(emergency|loss\s*of\s*cooling|no\s*ac|water\s*leak|p1|urgent\s*service|heat\s*out)
  ```
- **Execution:** Keep in root `Inbox`; apply Google Workspace Label `TIER0_EMERGENCY`; dispatch webhook to `https://hook.make.com/email-intake-processor`.

#### Rule Profile 2: Supply Chain Noise Management (`@SaneLater` Exception Routing)
Communications from wholesale supply houses are automatically cleared from after-hours dispatcher views, with an exception for automated locker/night-drop pickup codes:
- **Condition (Supplier Sender Domains):** Sender domain matches `@insco.com`, `@johnstonesupply.com`, `@carrierenterprise.com`, or `@ferguson.com`.
- **Exclusion (Emergency Override):** Subject line contains `Emergency PO`, `Night Drop`, `Locker Code`, or `Expedited Delivery`.
- **Execution:** If Exclusion matches, deliver to root `Inbox` and label `SUPPLY_RUN_READY`. If Exclusion does not match, move immediately to `@SaneLater`.

#### Rule Profile 3: Commercial Spam & Unsolicited Lead Suppression (`@SaneBlackHole`)
- **Condition:** Message headers indicate bulk cold outreach or subject line matches:
  ```regex
  (?i)(seo\s*ranking|lead\s*generation|grow\s*your\s*business|virtual\s*assistant|offshore\s*dispatch)
  ```
- **Execution:** Move directly to `@SaneBlackHole`. Senders are permanently unsubscribed and blocked from SMTP transmission.

### 3.3 Daily Mailbox Audit & Maintenance Protocol
1. **06:30:00 CST Morning Reconciliation:**
   - The night dispatcher or incoming lead CSR opens Google Workspace, accesses folder `@SaneNextBusinessDay`, and verifies that all items have migrated smoothly to the morning triage board.
   - Run search query across `@SaneLater` for false negatives:
     ```
     label:@SaneLater newer_than:14h (urgent OR emergency OR leak OR broken OR AC OR heat)
     ```
2. **Model Training Operations:**
   - If any legitimate customer service request is detected in `@SaneLater`, the dispatcher drags the message to `Inbox` within the Google Workspace web client. This trains the Bayesian filtering model on Apex Comfort customer communication styles.
   - SaneBox cloud configurations are locked against non-admin edits. Only Chris Koehne and the designated RevOps Engineer possess administrative modification rights.

---

## 4. End-to-End Operational Execution Walkthrough (A Real-World Central Texas Scenario)

### 4.1 Incident Context & Environmental Baseline
- **Date & Timestamp:** July 18, 2024, 22:14:00 CST.
- **Ambient Weather Conditions:** Current temperature 103°F at Austin-Bergstrom (KAUS); heat index 112°F. National Weather Service Excessive Heat Warning in effect for Travis and Williamson Counties.
- **Customer:** Brenda Holloway (Contact ID: `GHL-88491`, ST Customer ID: `1094821`).
- **Service Address:** 1404 Settlers Valley Dr, Pflugerville, TX 78660 (Travis County).
- **Occupancy Profile:** Single-family residential home; 84-year-old mother residing in the home with supplemental oxygen concentrator.
- **Mechanical Equipment:** 4-Ton Lennox 16-SEER Split System (Installed 2019).
- **Active On-Call Rotation:**
  - Tier 1 Primary Specialist: Tyler Vance (Mobile: 512-555-0142).
  - Tier 2 Secondary Specialist: Marcus Reed (Mobile: 512-555-0188).
  - Tier 3 Escalation Override: Chris Koehne (Mobile: 512-555-0199).
- **Current Nightly Dispatch Counter:** `{{custom_values.nightly_dispatch_count}}` = `0` (Capacity intact).

---

### 4.2 Chronological Execution & Audit Log

#### 22:14:00 CST — Inbound Unanswered Call
Brenda Holloway calls the primary Apex Comfort Solutions business line (512-555-0100). Both office lines are automated for after-hours mode. The call rings for 18 seconds and disconnects to the after-hours automated voicemail greeting.

#### 22:14:22 CST — Workflow Initiation: MCTB & Environmental Ingestion
- Workflow `[OPS] 01 - After-Hours MCTB & AI Triage` fires in GoHighLevel.
- Webhook fires to Make.com: Retrieves current weather conditions for ZIP code 78660. Make.com returns `ambient_temp: 103` and writes this value to `contact.ambient_temp_snapshot`.
- Custom field `bot_active` is confirmed as `True`.

#### 22:14:37 CST — Automated Conversational Triage (GHL Conversational AI)
- GHL executes SMS dispatch to Brenda Holloway:  
  *"Hi Brenda, this is Apex Comfort Solutions. We saw we just missed your call! Central Texas heat doesn't wait—are you experiencing an AC emergency right now?"*
- **22:15:18 CST — Customer Response:**  
  *"Yes, my AC stopped blowing cold air two hours ago. My 84-year-old mother is here on oxygen and the house is already 86 degrees inside. Please help!"*
- **22:15:22 CST — AI Prompt Evaluation:**  
  The bot evaluates prompt directives:
  - Ambient temp (103°F) > 90°F: **TRUE**.
  - Vulnerable occupant identified: **TRUE**.
  - Triage classification: **P1-Critical**.
- **22:15:30 CST — AI Fee Authorization Presentation:**  
  *"Brenda, given the extreme heat and your mother's health needs, this is classified as an immediate P1 Life-Safety Emergency. We have an on-call master technician available. Our after-hours emergency diagnostic dispatch fee is $189.00. Please reply 'AUTHORIZE_189' so we can dispatch our technician immediately."*
- **22:16:05 CST — Customer Fee Authorization:**  
  Brenda replies: *"AUTHORIZE_189"*
- GHL sets `after_hours_fee_accepted` to `True`.
- GHL applies tags: `status:qualified-emergency`, `priority:p1-critical`, `dispatch:pending-tech-ack`.

#### 22:16:08 CST — Capacity Guardrail Engine Execution
- Webhook hits Make.com scenario `Check Nightly Capacity`.
- Make.com pulls global variable `{{custom_values.nightly_dispatch_count}}`, returning `0`.
- Constraint Check: `0 < 2` physical rolls.
- Action: Make.com updates `{{custom_values.nightly_dispatch_count}}` to `1`.
- Return Response: `STATUS: PROCEED`.
- Workflow moves immediately to `[OPS] 02 - On-Call Escalation Engine`.

#### 22:16:15 CST — Tier 1 Paging & Timeout Failure Simulation
- GHL dispatches internal SMS to Tier 1 Primary Tech Tyler Vance (512-555-0142):  
  *"EMERGENCY DISPATCH: P1-Critical at 1404 Settlers Valley Dr, Pflugerville. Total cooling loss, elderly occupant on O2. $189 Fee Authorized. Reply '1' or click https://hook.make.com/ack?tech=142 to claim."*
- System initiates 5-minute (300-second) wait condition monitoring field `Tech_Claimed`.
- *Operational Event:* Tyler Vance is currently deep in sleep following a heavy daytime shift and does not hear the mobile notification chime.
- At 22:21:15 CST, the 5-minute wait block expires. `Tech_Claimed` remains `False`.
- System registers Tier-1 timeout and pushes ticket to Tier 2.

#### 22:21:16 CST — Tier 2 Escalation & Twilio Whisper Drop
- Make.com triggers Twilio API to execute an automated high-priority voice call to Secondary Specialist Marcus Reed (512-555-0188), while simultaneously sending a backup SMS alert.
- Marcus Reed's phone rings; he answers at 22:21:28 CST.
- Twilio executes the TwiML script via Polly.Joanna:  
  *"This is the Apex Comfort Automated Dispatch System. Primary technician has failed to acknowledge an emergency in Pflugerville. Press 1 to accept, or 9 to reject."*
- Marcus presses DTMF tone `1` on his phone keypad at 22:21:38 CST.
- Twilio transmits the gather action webhook to `https://hook.make.com/twilio-gather-tier2`.
- Make.com updates GHL contact custom field `Tech_Claimed` to `True`, tags contact `dispatch:tech-assigned`, and appends note: `Claimed by Tier 2 Specialist Marcus Reed`.
- The escalation loop safely terminates, preventing Tier-3 escalation to Chris Koehne.

#### 22:22:00 CST — Automated Booking Injection into ServiceTitan
- Make.com issues API call to ServiceTitan:
  - Creates Booking ID: `8849201`.
  - Job Type: `204` (After-Hours Emergency).
  - Priority: `Urgent`.
  - Assigned Technician: `Marcus Reed (ID: 9482)`.
  - Customer Summary: *"P1-Critical: Loss of cooling. 103F ambient. Vulnerable occupant. $189 dispatch fee collected."*
- GHL sends reassurance SMS to Brenda Holloway:  
  *"Help is on the way, Brenda. Our senior field specialist Marcus Reed has accepted your dispatch and is rolling to your Pflugerville home now. Estimated arrival time is 23:05 CST. You can reach him directly through this line if needed."*

#### 22:28:00 CST — HITL Dead-Man Safeguard Activation
- Marcus Reed gets into his service van and dials Brenda Holloway's telephone number directly through his ServiceTitan Mobile application to confirm that the outdoor breaker is still switched on.
- ServiceTitan triggers an outbound call webhook to Make.com.
- Make.com processes the event:
  - Matches customer phone number to active GHL profile `GHL-88491`.
  - Executes immediate updates:
    1. Adds tag: `human-takeover`.
    2. Updates custom field: `bot_active` = `False`.
    3. Evicts contact from workflows `[OPS] 01` and `[OPS] 02`.
- The conversational AI engine is completely disconnected from the thread, guaranteeing zero automated message collisions while Marcus handles customer communication.

#### 23:01:45 CST — On-Site Arrival & Physical Triage
- Marcus arrives on-site at 1404 Settlers Valley Dr (39 minutes from his acceptance, well within the 45-minute mobilization SLA).
- Marcus checks in via ServiceTitan Mobile.
- Initial Physical Diagnostic:
  - Indoor temperature: 87.5°F.
  - Outdoor condenser: Fan motor operational; compressor humming but stalled on locked rotor amps (drawing 68.2 LRA on a 19.8 RLA nameplate).
  - Root Cause: Failed dual-round run capacitor (45/5 MFD @ 440V) measuring 0.0 MFD across hermetic terminal due to heat degradation; contactor points pitted.

#### 23:22:00 CST — Physical Remediation & System Recovery
- Marcus isolates high-voltage electrical disconnect.
- Installs on-truck replacement inventory:
  - (1) AmRad 45/5 MFD Turbo200 Universal Motor Run Capacitor.
  - (1) 30-Amp Packard 2-Pole Contactor.
- Cleans debris from condenser coil face using nitrogen purge.
- Restores 240V power to condenser. Compressor engages smoothly; initial amp draw settles at 14.1 Amps.
- Diagnostic readings at 23:35:00 CST:
  - Liquid Line Pressure: 295 PSIG.
  - Suction Line Pressure: 122 PSIG.
  - Subcooling: 10.2°F (Manufacturer Target: 10.0°F $\pm$ 1°F).
  - Delta T across indoor evaporator coil: 19.4°F (Supply: 58.6°F, Return: 78.0°F).

#### 23:42:00 CST — Financial Finalization, Digital Sign-off & System Close
- Brenda Holloway reviews the digital work order on Marcus's ServiceTitan tablet:
  - After-Hours Diagnostic Dispatch Fee: $189.00 (Authorized & Collected).
  - Universal Capacitor & Contactor Replacement: $298.00.
  - Total Investment: $487.00.
- Brenda provides digital signature; payment is processed via integrated mobile card reader.
- Marcus completes the ticket in ServiceTitan Mobile at 23:48:00 CST.
- ServiceTitan webhook fires to GHL, applying tag `job:completed`.
- SaneBox and GHL log the digital transaction, archiving all conversational threads, Twilio voice logs, and ServiceTitan work orders into the master CRM record.
- Marcus returns to standby status. Fatigue management verifies his total active on-site duration was 46 minutes, keeping him eligible for backup standby should a second extreme emergency arise prior to 06:30:00 CST.