# Automated Quote Follow-Up Engine & Pipeline Engineering
**Client:** Apex Comfort Solutions (Chris Koehne) | Central Texas HVAC  
**Target:** Plug $10,000/month ($120,000/year) quote leakage across unaccepted residential and light-commercial estimates  
**Technology Stack:** GoHighLevel (GHL v2 Workflow Engine) ⇄ ServiceTitan v2 REST API Middleware (AWS Lambda / Node.js runtime)

---

## Workflow Architecture & Trigger/Exit Logic

The follow-up engine coordinates real-time synchronization between ServiceTitan field estimates and GoHighLevel omni-channel engagement workflows.

```
       [ ServiceTitan: Technician Generates Estimate in Field ]
                               │
                               ▼
        [ ST Status: "Open" (Unaccepted after 24 hours) ]
                               │
   Specific Inbound Webhook Trigger from ServiceTitan:
   POST /accounting/v2/tenant/3948210/estimates -> servicetitan_estimate_synced
                               │
                               ▼
      ┌─────────────────────────────────────────────────────────┐
      │     GHL Workflow: [ST-SYNC] Quote Follow-Up Engine      │
      └─────────────────────────────────────────────────────────┘
                               │
                Split Evaluation by Ticket Value
              ┌────────────────┴────────────────┐
              ▼                                 ▼
   [ Minor Repair (< $1,500) ]      [ Major System (≥ $1,500) ]
      (Streamlined Cadence)            (High-Touch + Financing)
              │                                 │
              ├─ T+24h: Recap & Approval        ├─ T+24h: Recap, Scope & Digital Link
              ├─ T+48h: Diagnostic Urgency      ├─ T+48h: Financing ($/mo) + Warranty
              ├─ T+72h: Chris Soft Close        ├─ T+72h: Central TX Climate & Capacity
              └─ Day 5: Final File Closure      ├─ Day 5: Chris Koehne Owner Outreach
                                                └─ Day 7: Pre-Expiration Notice
                               │
                               ▼
        ┌───────────────────────────────────────────────────────┐
        │                  GLOBAL EXIT CRITERIA                 │
        │  • ST Status updates to "Sold" or "Dismissed"         │
        │  • Customer initiates inbound SMS, call, or email     │
        │  • Online acceptance logged (quote_signed_online)     │
        │  • Tag added: quote-manual-override                   │
        └───────────────────────────────────────────────────────┘
```

### Specific Inbound Webhook Trigger from ServiceTitan

The automation is initiated by the specific inbound webhook trigger from ServiceTitan configured within the ServiceTitan Developer Portal and integration middleware:
- **ServiceTitan Event Subscription:** `Estimate.Created` and `Estimate.Updated` events published by ServiceTitan.
- **ServiceTitan Webhook Endpoint:** `POST /accounting/v2/tenant/3948210/estimates` (API Route: `POST /accounting/v2/tenant/{tenantId}/estimates`).
- **GHL Custom Webhook Listener:** `servicetitan_estimate_synced` (Ingestion URL: `https://services.leadconnectorhq.com/hooks/apex-comfort/servicetitan_estimate_synced`).
- **Trigger Payload Structure:** The webhook payload contains the ServiceTitan tenant ID `3948210`, `estimate_id`, `job_id`, `customer_id`, `total_value`, `status`, `summary`, and technician metadata.

### Inbound Trigger Conditions & Filtering Logic
Enrollment into the automated follow-up engine requires the inbound webhook payload to fulfill all of the following validation checks:
1. `estimate_data.status` equals `Open`.
2. `estimate_data.total_value` is greater than `0.00`.
3. `contact.tags` does not contain `quote-follow-up-active`, `do-not-contact`, or `customer-vip-lost`.
4. `estimate_data.summary_type` equals `Replacement` or `Repair`.

### State Initialization Sequence
Upon passing trigger validation, the system executes four atomic updates:
- Applies tag: `quote-follow-up-active`.
- Inserts contact into pipeline: `HVAC Replacement & Repair Pipeline` at stage `Quote Delivered - Unsigned`.
- Sets custom field `custom_fields.last_estimate_total` to value of `{{estimate_data.total_value}}`.
- Sets custom field `custom_fields.estimate_approval_url` to `https://portal.apexcomforttx.com/quote/{{estimate_data.estimate_id}}?auth={{estimate_data.job_id}}`.

### Quiet Hours & Compliance Rules
- **Timezone Enforcement:** Evaluated exclusively under `America/Chicago` (Central Standard / Daylight Time).
- **SMS Window:** Monday through Saturday, 08:00 to 19:30 CST; Sunday, 10:00 to 18:00 CST.
- **Email Window:** Daily, 07:30 to 20:30 CST.
- **Off-Hours Delay Logic:** If a wait state elapses during restricted hours (e.g., 24-hour interval expires at 11:15 PM CST), the execution thread enters a sleep state and releases at exactly 08:05 AM CST the next morning.

### Integration Field-Mapping Specifications

The following table establishes the operational mapping between ServiceTitan v2 data models and GoHighLevel custom contact/opportunity fields.

| ServiceTitan Entity & Field | GoHighLevel Target Field | Target Type | Transformation / Business Logic | Data Flow Direction |
| :--- | :--- | :--- | :--- | :--- |
| `Estimate.id` | `custom_fields.st_estimate_id` | String | Integer cast to String; prefix stripped | ServiceTitan to GHL |
| `Estimate.total` | `Opportunity.monetary_value` | Currency | Two-decimal float representation (USD) | ServiceTitan to GHL |
| `Estimate.summary` | `custom_fields.estimate_summary` | String | Truncated to 255 chars; removes special symbols | ServiceTitan to GHL |
| `Estimate.modifiedOn` | `custom_fields.estimate_updated_ts` | DateTime | Convert UTC to ISO 8601 Central Time | ServiceTitan to GHL |
| `Job.technicianName` | `custom_fields.assigned_technician_name`| String | Direct String pass-through | ServiceTitan to GHL |
| `Job.address.street` | `Contact.address1` | String | Direct String pass-through | ServiceTitan to GHL |
| `Job.address.city` | `Contact.city` | String | Direct String pass-through | ServiceTitan to GHL |
| `Customer.mobilePhone` | `Contact.phone` | Phone | Format to standard E.164 (`+1XXXXXXXXXX`) | ServiceTitan to GHL |
| `GHL.Tag[quote-sold]` | `Estimate.status` | Enum | Map tag to ServiceTitan status: `Sold` | GHL to ServiceTitan |
| `GHL.Task[Created]` | `Task.taskTypeId` | Integer | Inject Task ID `44` (Quote Follow-Up Action) | GHL to ServiceTitan |

### Exit & Termination Triggers
The workflow listener evaluates incoming system webhooks and customer interactions. Execution terminates immediately when any of the following occur:
- **ServiceTitan Sold Status:** Webhook registers `estimate_data.status` = `Sold`. Contact transitions to pipeline stage `Won`, removes tag `quote-follow-up-active`, and applies tag `quote-sold`.
- **ServiceTitan Dismissed Status:** Webhook registers `estimate_data.status` = `Dismissed`. Contact transitions to pipeline stage `Lost` with cancellation reason logged.
- **Inbound Communication:** Contact sends SMS, initiates phone call, or replies to email. Contact is removed from sequence and routes to priority dispatch.
- **Digital Portal Execution:** Webhook event `quote_signed_online` triggers receipt and pauses cadences.

---

## Multi-Stage SMS & Email Sequence Copy

The messages below are deployed for estimates with total values equal to or exceeding $1,500.00 across the Central Texas coverage corridor (Austin, Round Rock, Buda, Kyle, Georgetown, San Marcos).

### Stage 1: The 24-Hour Scope & Digital Review (Day 1 — T+24 Hours Post-Visit)

#### Stage 1 SMS
- **Timing:** Exactly 24 hours post-estimate generation (delayed to 08:05 CST if landing in quiet hours)
- **Sender:** `(512) 555-0199` (Apex Comfort Solutions Dispatch)
- **Recipient:** `{{contact.phone}}`

Hi {{contact.first_name}}, this is Chris Koehne from Apex Comfort Solutions. Following up on the proposal technician {{custom_fields.assigned_technician_name}} prepared for your home at {{contact.address1}}. You can review the itemized breakdown and approve the scope directly here: {{custom_fields.estimate_approval_url}} - Let me know if you would like me to adjust any options before installation slots fill this week! Reply STOP to opt out.

#### Stage 1 Email
- **Sender:** Chris Koehne · Apex Comfort Solutions `<service@apexcomforttx.com>`
- **Subject:** Detailed estimate for your {{custom_fields.estimate_summary}}
- **Preheader:** Your itemized estimate, system warranty, and direct approval link.

Dear {{contact.first_name}},

Thank you for giving Apex Comfort Solutions the opportunity to evaluate the heating and cooling system at {{contact.address1}}. 

Our technician, {{custom_fields.assigned_technician_name}}, completed the diagnostic and engineering review and assembled an itemized proposal for your project:

**Project Summary:**
- **System / Service:** {{custom_fields.estimate_summary}}
- **Service Address:** {{contact.address1}}, {{contact.city}}
- **Itemized Total:** ${{opportunity.monetary_value}}
- **Price Guarantee Period:** 30 Days from issue

You can inspect the full line-item scope of work, technical specifications, and terms by accessing our secure digital portal below:
{{custom_fields.estimate_approval_url}}

If you would like to adjust the project options or discuss equipment alternatives, reply directly to this email or call our office at (512) 555-0199.

Warmly,

Chris Koehne  
General Manager | Apex Comfort Solutions  
License #TACLB000123C  
Office: (512) 555-0199

---

### Stage 2: The 48-Hour Financing & Warranty Pivot (Day 2 — T+48 Hours Post-Visit)

#### Stage 2 SMS
- **Timing:** 48 hours post-estimate generation
- **Sender:** `(512) 555-0199`
- **Recipient:** `{{contact.phone}}`

{{contact.first_name}}, a major HVAC repair or replacement shouldn't strain your emergency funds. Apex offers 0% interest for 18 months or low APR plans starting at ${{custom_fields.estimated_monthly_payment}}/month for your proposal. Run a soft credit pre-qualification with no score impact here: {{custom_fields.financing_application_url}} - Reply STOP to opt out.

#### Stage 2 Email
- **Sender:** Apex Comfort Solutions Financing Desk `<financing@apexcomforttx.com>`
- **Subject:** Monthly payment options and warranty terms: Estimate #{{custom_fields.st_estimate_id}}
- **Preheader:** Review monthly financing plans starting at ${{custom_fields.estimated_monthly_payment}}/mo with 0% APR available.

Dear {{contact.first_name}},

When evaluating unexpected HVAC repairs or complete system replacements in Central Texas, protecting your monthly cash flow is paramount. 

We partner with primary home improvement lenders to provide straightforward financing options for your estimate of ${{opportunity.monetary_value}}:

**Available Financing Programs:**
1. **Zero Interest Promotion:** 0% APR for up to 18 months on approved credit.
2. **Low-Monthly Fixed Rate:** Fixed terms up to 120 months with payments as low as ${{custom_fields.estimated_monthly_payment}}/month.
3. **No Prepayment Penalties:** Pay down the balance at your own pace at any point.

You can check your eligibility online with a soft credit check that does not impact your credit score:
{{custom_fields.financing_application_url}}

**The Apex Comfort Shield Guarantee:**
- **10-Year Equipment & Compressor Warranty** on complete system replacements.
- **1-Year Complete Labor Guarantee** on all mechanical repairs.
- **Manual J Load Calculation Assurance:** Guaranteed right-sized capacity for high-temperature Texas summers.

To proceed using cash, card, or direct check instead, view your standard proposal here:
{{custom_fields.estimate_approval_url}}

Sincerely,

Apex Comfort Solutions Financing Team  
Office: (512) 555-0199 | financing@apexcomforttx.com

---

### Stage 3: The 72-Hour Climate & Dispatch Capacity (Day 3 — T+72 Hours Post-Visit)

#### Stage 3 SMS
- **Timing:** 72 hours post-estimate generation
- **Sender:** `(512) 555-0199`
- **Recipient:** `{{contact.phone}}`

{{contact.first_name}}, Chris Koehne here with Apex. We are finalizing our installation crew routes for {{contact.city}} next week. We have a crew open for {{custom_fields.next_available_install_day}}. Do you want us to reserve that installation window for your system, or have you decided to hold off? Let me know so I can hold the team. Reply STOP to opt out.

#### Stage 3 Email
- **Sender:** Chris Koehne `<chris@apexcomforttx.com>`
- **Subject:** Installation schedule for {{contact.address1}} next week
- **Preheader:** Next week's dispatch schedule and crew reservations for your open estimate.

Dear {{contact.first_name}},

I am reaching out directly because estimate #{{custom_fields.st_estimate_id}} remains open on our service board for your property at {{contact.address1}}.

With Central Texas temperatures creating continuous heavy load on residential cooling equipment, our installation calendar books out 3 to 5 business days in advance. I want to ensure your household is not caught in extreme temperatures without dependable heating or air conditioning.

**Current Estimate on File:**
- **System Scope:** {{custom_fields.estimate_summary}}
- **Total Amount:** ${{opportunity.monetary_value}}
- **Tentative Target Window:** {{custom_fields.next_available_install_day}}

If you are gathering competing bids, I completely respect that approach—I want you to be confident in the contractor entering your home. If you want a second look at sizing data, duct calculations, or tier comparisons (Good / Better / Best), reply directly to this email or call my cell at (512) 555-0199.

If you are ready to secure a crew date, click below to confirm:
{{custom_fields.estimate_approval_url}}

Thank you,

Chris Koehne  
Owner | Apex Comfort Solutions  
Direct Line: (512) 555-0199

---

### Stage 4: Day 5 File Close-Out Notice (Day 5 — T+120 Hours Post-Visit)

#### Stage 4 SMS
- **Timing:** 120 hours post-estimate generation
- **Sender:** `(512) 555-0199`
- **Recipient:** `{{contact.phone}}`

Hi {{contact.first_name}}, we haven't heard back regarding estimate #{{custom_fields.st_estimate_id}}, so we assume you've solved the issue or decided to hold off. We will close out this ticket today so we don't bother you. Reach us anytime at (512) 555-0199 if anything changes. Stay comfortable! Reply STOP to opt out.

#### Stage 4 Email
- **Sender:** Apex Comfort Solutions `<service@apexcomforttx.com>`
- **Subject:** Permission to close your file? (Estimate #{{custom_fields.st_estimate_id}})
- **Preheader:** Final review before closing out your diagnostic record for {{contact.address1}}.

Dear {{contact.first_name}},

We have not received a reply regarding the proposal for your {{custom_fields.estimate_summary}} at {{contact.address1}}. We assume your heating and cooling requirements have been met, or that you have decided to postpone the project.

Our system is set to archive this file today so we do not send unnecessary follow-ups.

**Guarantee Notice:**
Your quoted pricing of ${{opportunity.monetary_value}} remains protected in our database through **{{custom_fields.estimate_expiration_date}}**. If your circumstances change or your existing equipment experiences further operational stress, you can reactivate your project instantly:

Reactivate and View Your Estimate:
{{custom_fields.estimate_approval_url}}

Thank you for considering Apex Comfort Solutions. We remain ready to serve your home whenever you need us.

Best regards,

Chris Koehne & The Apex Technical Team  
Apex Comfort Solutions  
Phone: (512) 555-0199 | service@apexcomforttx.com

---

## Objection-Handling Quick-Response Templates

These response templates are configured within GHL Snippets for instant deployment by dispatch personnel and Comfort Advisors.

### 1. Price Sensitivity & Competitor Lower Bid
- **Channel:** SMS or Email
- **Trigger Scenario:** Customer states: "Another company gave us a quote that is $800 cheaper."

{{contact.first_name}}, I completely understand keeping an eye on price. When homeowners see lower bids in Central TX, it almost always means the contractor is excluding city permit fees, omitting emergency primary float switches, using thin non-insulating duct flex, or running builder-grade coils that freeze in 100°+ weather. Apex includes full city permitting, digital static airflow testing, and our 10-year parts and labor backing. Could you text or email a photo of their line items? Chris will review it side-by-side to make sure you are getting a true apples-to-apples comparison.

---

### 2. Spouse or Co-Buyer Consultation Delay
- **Channel:** SMS
- **Trigger Scenario:** Customer states: "I need to talk this over with my husband/wife first."

Understood, {{contact.first_name}}! It is a major investment for your home. I can send a clean 1-page summary PDF directly to their phone or email right now showing the system options, utility rebate eligibility, and monthly payment options. What is their best email or mobile number?

---

### 3. Deferring the Repair or Replacement
- **Channel:** SMS
- **Trigger Scenario:** Customer states: "We are going to wait and try to get another season out of this unit."

Totally understand wanting to hold off, {{contact.first_name}}. Just an honest heads-up: when systems run with stressed components in Central Texas heat, they usually suffer full compressor burnout during peak 100° July/August afternoons when lead times for replacements are longest. Would it help if we explored a smaller repair scope to restore safe operation today without committing to the full overhaul?

---

### 4. Financing Process Inquiry
- **Channel:** SMS
- **Trigger Scenario:** Customer states: "How does the financing work? Does it take a long time to get approved?"

It takes under 90 seconds, {{contact.first_name}}! Our lenders do a soft credit check that does not affect your credit score. You can select 0% interest for 18 months or low monthly payments through 120 months. Complete the quick form here: {{custom_fields.financing_application_url}}. Let me know once you submit it and I will get our install manager to pencil your date in!

---

## Re-Engagement Routing & Automated Task-Creation Rules

Operational rules dictate how customer responses and behavioral triggers convert into human-assigned tasks within GoHighLevel and ServiceTitan.

```
       [ Customer Interacts with Follow-Up Asset ]
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
 [ Inbound SMS / Email ] [ Quote Viewed / Click ] [ Quote Signed ]
       │                   │                   │
       ▼                   ▼                   ▼
 Priority 1 Escalation   Priority 2 Routing   Priority 0 Won
 SLA: < 15 Minutes       SLA: < 2 Hours       Instant Dispatch
```

### SLA Escalation Engine

```
[Inbound Reply Event Logged]
          │
          ▼
   [Start 15-Min SLA Clock]
          │
          ├──> Human Dispatcher Claims & Calls Customer ──> [SLA Met: Cancel Escalation]
          │
          └──> 15 Minutes Elapses Without Outbound Call
                    │
                    ▼
          [Trigger Escalation]
                    │
                    ├─ In-App Push Notification to Chris Koehne Mobile
                    ├─ Direct SMS Alert to Operations Manager (+15125550199)
                    └─ Elevate Task Priority to 'CRITICAL' in GHL Board
```

### Automation Execution Rules

#### Rule 1: Customer Inbound Message (SMS, Email, or Web Chat)
- **Evaluation Criteria:**
  - `Contact Reply` event occurs across SMS, Email, or GMB channels.
  - Message body does not contain standard opt-out strings (`STOP`, `UNSUBSCRIBE`, `CANCEL`).
- **Automated Workflow Execution:**
  1. Set pipeline stage to `Quote Re-Engaged`.
  2. Strip tag: `quote-follow-up-active`.
  3. Apply tag: `quote-reply-pending`.
  4. Create high-priority internal task in GHL:
     - **Title:** `URGENT: Re-engagement from {{contact.first_name}} {{contact.last_name}}`
     - **Assignee:** Lead Dispatcher / Chris Koehne
     - **Due Date:** Current Timestamp + 15 Minutes
     - **Description:** Customer responded to automated sequence: "{{message.body}}". Review conversation history and call back immediately at {{contact.phone}}.
  5. Post payload to ServiceTitan Task Endpoint (`POST /jpm/v2/tenant/3948210/tasks`):
     ```json
     {
       "jobId": 3948210,
       "taskTypeId": 44,
       "summary": "GHL ALERT: Homeowner re-engaged on quote ($8,450.00)",
       "description": "Customer replied to SMS. Outbound call required within 15-minute SLA. Phone: +15125550198",
       "priority": "High",
       "status": "Open",
       "due": "2025-03-15T14:30:00.000Z"
     }
     ```

#### Rule 2: High-Intent Portal Behavior (Multiple Link Accesses)
- **Evaluation Criteria:**
  - Contact clicks `{{custom_fields.estimate_approval_url}}` 3 or more times within a 24-hour rolling window.
  - Current pipeline stage equals `Quote Delivered - Unsigned`.
- **Automated Workflow Execution:**
  1. Apply tag: `hot-quote-revisited`.
  2. Update contact custom field `custom_fields.engagement_intensity` to `High`.
  3. Generate internal GHL Task:
     - **Title:** `High Intent: {{contact.first_name}} {{contact.last_name}} reviewing estimate repeatedly`
     - **Assignee:** Assigned Technician ({{custom_fields.assigned_technician_name}}) / Dispatch
     - **Due Date:** Current Timestamp + 2 Hours
     - **Description:** Customer opened digital estimate portal 3+ times today. Call to answer lingering technical questions or assist with online checkout.

#### Rule 3: Digital Acceptance / Signature Execution
- **Evaluation Criteria:**
  - Inbound webhook `quote_signed_online` received from portal OR ServiceTitan status changes to `Sold`.
- **Automated Workflow Execution:**
  1. Transition pipeline stage to `Deal Won`.
  2. Remove tags: `quote-follow-up-active`, `hot-quote-revisited`, `quote-reply-pending`.
  3. Apply tag: `quote-sold`.
  4. Post internal notification to Slack `#dispatch-ops`:
     `🎉 DEAL WON: {{contact.first_name}} {{contact.last_name}} signed quote #{{custom_fields.st_estimate_id}} for ${{opportunity.monetary_value}}. Crew scheduling initiated.`
  5. Execute ServiceTitan REST API call to transition Job Status to `Ready to Schedule`.

#### Rule 4: Stalled Quote Archival (Sequence Expiration)
- **Evaluation Criteria:**
  - Quote age equals 30 calendar days without status update to `Sold`.
  - Sequence Stage 4 completed without customer response.
- **Automated Workflow Execution:**
  1. Transition pipeline stage to `Closed Stalled / Re-Nurture`.
  2. Remove tag: `quote-follow-up-active`.
  3. Apply tag: `quote-archived-30days`.
  4. Enroll contact into 6-month seasonal tune-up nurture workflow.

---

## Implementation Sign-Off Checklist

The following test criteria must be verified in the staging environment before deploying the integration to production.

| Test ID | System Component | Verification Steps & Acceptance Criteria | Owner | Execution Date | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `TC-01` | ServiceTitan Webhook Ingestion | Push mock estimate payload with status `Open` and value `$4,850.00`. Verify GHL creates contact, updates custom fields, and applies `quote-follow-up-active` within 5 seconds. | Integrations Architect | 2025-03-10 | Verified |
| `TC-02` | Value-Based Branching Logic | Inject two test payloads: `$850.00` (Minor Repair) and `$6,200.00` (Major System). Confirm GHL directs them to the respective repair vs. major system follow-up branches. | Systems Engineer | 2025-03-10 | Verified |
| `TC-03` | Central Time Quiet Hours Delay | Simulate trigger execution at 11:30 PM CST. Verify system pauses execution and schedules outbound SMS transmission for 08:05 AM CST the next morning. | QA Specialist | 2025-03-11 | Verified |
| `TC-04` | Dynamic Variable Rendering | Inspect generated SMS and Email Stage 1 messages. Confirm all tokens (`{{contact.first_name}}`, `{{opportunity.monetary_value}}`, approval URL) resolve to accurate values without raw merge syntax. | QA Specialist | 2025-03-11 | Verified |
| `TC-05` | Global Exit on Estimate Sold | Update ServiceTitan test estimate to `Sold`. Verify GHL immediately terminates all downstream wait states, strips active tags, and marks stage as `Deal Won` within 10 seconds. | Integrations Architect | 2025-03-12 | Verified |
| `TC-06` | Inbound Customer Reply Breakout | Send inbound SMS reply ("Can you do Wednesday?") from test mobile device. Verify sequence stops, alert SMS fires to dispatch pool (`+15127008899`), and GHL task creates with 15-minute SLA. | Systems Engineer | 2025-03-12 | Verified |
| `TC-07` | ServiceTitan Task Injection | Trigger customer reply breakout and confirm middleware successfully creates Task Type `44` in ServiceTitan linked to the specific Job ID. | Integrations Architect | 2025-03-13 | Verified |
| `TC-08` | TCPA Compliance & Opt-Out | Send inbound text `STOP`. Verify GHL contact records DND flag, applies tag `do-not-contact`, and blocks all subsequent SMS execution threads. | Compliance Officer | 2025-03-13 | Verified |
| `TC-09` | Financing Portal Redirection | Click financing links across Stage 2 email and SMS. Verify redirection to dedicated lender intake with pre-populated contractor dealer ID `APX-TX-884`. | Front-End Developer | 2025-03-14 | Verified |
| `TC-10` | Dispatcher Operational Runbook | Conduct 45-minute practical training with Chris Koehne and dispatch staff on pipeline stage movement, snippet deployment, and manual overrides. | Lead Consultant | 2025-03-14 | Verified |

### Production Deployment Approval
- **Apex Comfort Solutions Ownership:** Chris Koehne, General Manager — Approved 2025-03-15
- **Lead Systems Engineer:** Marcus Vance, RevOps Solutions LLC — Approved 2025-03-15
- **Go-Live Date:** 2025-03-16 07:00 CST