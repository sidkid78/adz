# Security, Compliance & Infrastructure Hardening

**HOMEase Solutions: Security, Compliance & Infrastructure Hardening Architecture**

As the **Specialized Worker in Cybersecurity, HIPAA Compliance, and Cloud Infrastructure**, I have developed the following security architecture. This framework is designed to transform HOMEase from a lead-generation tool into a high-trust "Intelligence Layer" capable of handling sensitive clinical referrals and high-value financial transactions.

---

### 1. Security Architecture Overview: The "Hardened Intelligence Layer"

The security posture of HOMEase follows the **Principle of Least Privilege (PoLP)** and **Defense in Depth**. Given the B2B2C pivot, the architecture must satisfy two distinct regulatory and trust domains: **HIPAA** (for clinical handoffs) and **PCI-DSS** (for Stripe Connect transactions).

#### Infrastructure Stack
*   **Cloud Provider:** AWS (utilizing HIPAA-eligible services) or Supabase (Hardened PostgreSQL).
*   **Identity Provider:** Supabase Auth (OpenID Connect / JWT).
*   **Secrets Management:** AWS Secrets Manager or HashiCorp Vault.
*   **WAF/CDN:** Cloudflare with Advanced Bot Protection (to prevent scraping of property safety scores).

---

### 2. HIPAA-Compliant Data Governance & Encryption

To mitigate the "Lethal Product Assumption" regarding data fidelity and privacy, we implement a **Data Decoupling Strategy**.

#### A. Encryption at Rest and In-Transit
*   **In-Transit:** All data moving between the Client (React/Vite) and Backend (Fastify) is encrypted using **TLS 1.3**. We enforce HSTS (HTTP Strict Transport Security) to prevent downgrade attacks.
*   **At-Rest:** 
    *   **Database:** PostgreSQL disks are encrypted using **AES-256** via AWS KMS (Key Management Service).
    *   **Media (LiDAR/Scans):** All raw smartphone captures are stored in S3 buckets with **Server-Side Encryption (SSE-KMS)**.
    *   **Field-Level Encryption:** Sensitive clinical identifiers (e.g., Patient Name, NPI) are encrypted at the application level before being written to the database, ensuring that even a database dump remains unreadable without the application-tier keys.

#### B. Clinical vs. Environmental Data Separation
To minimize HIPAA scope, we utilize a "Shadow Vault" architecture:
1.  **The Environment Vault (HOMEase Core):** Stores `PropertyID`, `SafetyScore`, and `RiskRecords`. This is "Environmental Intelligence" and is not inherently PHI.
2.  **The Clinical Vault (Isolated):** Stores the mapping of `PropertyID` to `PatientName` and `ClinicalDiagnosis`.
3.  **Tokenization:** The portal uses a non-reversible `HandoffToken` to link the two. Contractors never see the Clinical Vault.

---

### 3. Least-Privilege Access Control Logic (RBAC/ABAC)

Access is governed by a hybrid of **Role-Based Access Control (RBAC)** and **Attribute-Based Access Control (ABAC)** to manage the complex relationship between the "Anxious Daughter," the Clinician, and the Contractor.

| Role | Access Level | Logic / Condition |
| :--- | :--- | :--- |
| **Clinician** | Read/Write (Referral) | Can only view assessments they initiated via `clinicianReferralId`. No access to financial data. |
| **Homeowner** | Full Owner | Access to all data for their `PropertyID`. Can grant/revoke contractor access. |
| **Contractor** | Restricted Read | Can view `RiskRecords` and `ProjectScope`. **CANNOT** view `HomeownerAddress` or `ContactInfo` until `Project.isDepositPaid == TRUE`. |
| **Admin (HITL)** | Read/Verify | Access to `RiskRecords` for verification. No access to `PatientName`. |

**Implementation Detail (PostgreSQL RLS):**
```sql
-- Example RLS Policy for Contractor Access to Property Address
CREATE POLICY contractor_limited_access ON properties
FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM projects
    WHERE projects.property_id = properties.id
    AND projects.contractor_id = auth.uid()
    AND projects.is_deposit_paid = TRUE
  )
);
```

---

### 4. Immutable Audit Logging Strategy

To satisfy insurance underwriting requirements for the "Home Safety FICO," every change to an assessment must be auditable. We implement a **Write-Once-Read-Many (WORM)** audit log.

*   **Trigger:** Any `UPDATE` or `DELETE` on `Assessment`, `RiskRecord`, or `Project` tables.
*   **Storage:** Logs are streamed to an isolated, immutable S3 bucket with Object Lock enabled.
*   **Payload:**
    ```json
    {
      "timestamp": "2026-07-20T14:30:00Z",
      "actor_id": "user_uuid",
      "action": "RISK_RECORD_VERIFIED",
      "entity_id": "risk_uuid",
      "changes": { "isVerified": [false, true], "verifiedAt": [null, "2026-07-20T14:30:00Z"] },
      "ip_address": "192.168.1.1",
      "user_agent": "HOMEase-Native-iOS-v1.2"
    }
    ```

---

### 5. Material-Ordering Indemnity Insurance Trigger Logic

This is a critical "Technical Risk Mitigation" feature. If a contractor orders materials based on a HOMEase scan that is later found to be inaccurate, the platform triggers an indemnity claim process.

**The Trigger Logic Flow:**
1.  **Confidence Thresholding:** If `Gemini_AI_Confidence < 0.98`, the record is flagged for **HITL (Human-in-the-Loop)**.
2.  **Certification Gate:** A contractor can only order materials through the platform for records marked `isVerified: true`.
3.  **The Claim Trigger:**
    *   **Input:** Contractor reports a "Measurement Mismatch" (> 0.5-inch delta).
    *   **Validation:** The system checks the `AuditLog`. If the measurement was marked as "High Confidence" or "HITL Verified," the **Indemnity Trigger** is activated.
    *   **Output:** The system automatically notifies the insurance carrier (e.g., a digital MGA partner) and pauses the Stripe Escrow release to prevent further loss.

```typescript
// Indemnity Logic Hook
async function evaluateIndemnityTrigger(projectId: string, reportedError: number) {
  const project = await prisma.project.findUnique({ where: { id: projectId }, include: { assessment: true } });
  
  if (reportedError > TOLERANCE_THRESHOLD && project.assessment.aiConfidenceScore > 0.95) {
    await insuranceApi.triggerClaim({
      policyId: "HOMEASE_GEN_LIABILITY_001",
      evidence: project.assessment.rawScanDataUrl,
      auditTrail: await getAuditTrail(project.assessment.id)
    });
    return "CLAIM_INITIATED";
  }
}
```

---

### 6. Compliance Checklist

This checklist serves as the "Exit Criteria" for Phase 1 (Product Hardening).

#### **HIPAA Compliance (Administrative & Technical)**
- [ ] **BAA (Business Associate Agreement):** Signed with AWS/Supabase and all clinical partners.
- [ ] **Data Minimization:** Verified that no PHI is stored in the `RiskRecord` or `Property` tables.
- [ ] **Access Revocation:** Automated logic to revoke Clinician access 30 days post-discharge.
- [ ] **Encryption:** AES-256 at rest and TLS 1.3 in transit confirmed via audit.

#### **Financial & Marketplace Security (PCI-DSS)**
- [ ] **Stripe Connect Integration:** Confirmed no raw credit card data touches HOMEase servers (handled via Stripe Elements/Checkout).
- [ ] **Escrow Lockdown:** Verified that `ContactInfo` is null in API responses until `isDepositPaid` is true.
- [ ] **Anti-Money Laundering (AML):** Contractor KYC (Know Your Customer) handled via Stripe Connect onboarding.

#### **Infrastructure Resilience**
- [ ] **Vulnerability Scanning:** Weekly automated scans (e.g., Snyk, GitHub Dependabot).
- [ ] **DDoS Protection:** Cloudflare WAF configured with rate limiting on the `/api/v1/scan/upload` endpoint.
- [ ] **Backup & Recovery:** Point-in-time recovery (PITR) enabled for PostgreSQL with a 30-day retention window.

### 7. Technical Proficiency Note
By implementing **Row-Level Security (RLS)** at the database layer and **Field-Level Encryption** for clinical data, we ensure that even in the event of a high-level application breach, the "Longitudinal Property Record" remains secure and the "One-Inch Risk" is financially mitigated through automated indemnity triggers. This architecture provides the "Trust and Control" required by the Sandwich Generation and the actuarial rigor required by Medicare Advantage insurers.
