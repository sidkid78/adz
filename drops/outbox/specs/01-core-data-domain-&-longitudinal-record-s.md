# Core Data Domain & Longitudinal Record Schema

As the **Specialized Worker in Database Architecture and PostgreSQL**, I have designed a robust schema tailored for the HOMEase "Intelligence Layer." 

This architecture prioritizes the **Longitudinal Property Record**, ensuring that every assessment, modification, and clinical handoff contributes to a historical "Safety FICO" score. The schema is built for **Prisma ORM** and optimized for **PostgreSQL**, incorporating structures necessary for Row-Level Security (RLS) to ensure strict data isolation between homeowners, contractors, and clinicians.

---

### 1. Entity Relationship Diagram (ERD) Logic

The data domain is centered around the **Property** as the primary "living" entity.

*   **User & Profiles:** A unified `User` table handles authentication, while polymorphic-style relations (or specific profile tables) handle the distinct needs of **Homeowners** (family decision-makers), **Contractors** (CAPS certified), and **Clinicians** (OTs/Discharge planners).
*   **Property (The Moat):** The core of the longitudinal record. It stores the aggregate `safetyScore`.
*   **Assessment (The Versioned Event):** Represents a specific point-in-time scan. Assessments are versioned to show the progression from "Pre-Modification" (Risk) to "Post-Modification" (Certified Safe).
*   **RiskRecord (AI Insights):** Individual hazards identified by Gemini AI. These include confidence scores and a `verifiedAt` timestamp for Human-in-the-Loop (HITL) requirements.
*   **Project & Escrow:** Links an Assessment to a Contractor. It manages the financial state (Stripe Connect `escrowId`) and ensures contact info is hidden until the deposit is confirmed.
*   **Transaction:** A granular ledger for the $149 report fees and the 3% marketplace fees.

---

### 2. The Prisma Schema

```prisma
// datasource and generator setup
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

generator client {
  provider = "prisma-client-js"
}

// --- Enums ---

enum UserRole {
  HOMEOWNER
  CONTRACTOR
  CLINICIAN
  ADMIN
}

enum RiskSeverity {
  HIGH
  MEDIUM
  LOW
}

enum AssessmentStatus {
  IN_PROGRESS
  PENDING_HITL_VERIFICATION
  COMPLETED
  ARCHIVED
}

enum ProjectStatus {
  ESTIMATING
  ESCROW_PENDING
  IN_PROGRESS
  COMPLETED
  CERTIFIED_SAFE
  CANCELLED
}

// --- Models ---

model User {
  id            String    @id @default(uuid())
  email         String    @unique
  name          String?
  role          UserRole  @default(HOMEOWNER)
  createdAt     DateTime  @default(now())
  updatedAt     DateTime  @updatedAt

  // Profile Relations
  properties    Property[]    // Properties owned by Homeowner
  contractorProfile ContractorProfile?
  clinicianProfile  ClinicianProfile?
  
  // Activity
  assessmentsPerformed Assessment[] @relation("PerformedBy")
  transactions         Transaction[]
}

model ContractorProfile {
  id                String   @id @default(uuid())
  userId            String   @unique
  user              User     @relation(fields: [userId], references: [id])
  capsCertified     Boolean  @default(false)
  licenseNumber     String?
  stripeConnectId   String?  @unique
  companyName       String
  projects          Project[]
}

model ClinicianProfile {
  id                String   @id @default(uuid())
  userId            String   @unique
  user              User     @relation(fields: [userId], references: [id])
  organization      String   // Hospital or Rehab Clinic
  npiNumber         String?  // National Provider Identifier
  referrals         Assessment[] @relation("ClinicalReferral")
}

model Property {
  id                String   @id @default(uuid())
  ownerId           String
  owner             User     @relation(fields: [ownerId], references: [id])
  address           String
  city              String
  state             String
  zip               String
  
  // The "Safety FICO" Logic
  currentSafetyScore Int      @default(0) // 0-100 scale
  lastCertifiedAt   DateTime?
  
  assessments       Assessment[]
  createdAt         DateTime @default(now())
  updatedAt         DateTime @updatedAt

  @@index([ownerId])
}

model Assessment {
  id                String           @id @default(uuid())
  propertyId        String
  property          Property         @relation(fields: [propertyId], references: [id])
  
  // Versioning for Longitudinal Record
  version           Int              @default(1)
  status            AssessmentStatus @default(IN_PROGRESS)
  
  // Participants
  performedById     String
  performedBy       User             @relation("PerformedBy", fields: [performedById], references: [id])
  clinicianReferralId String?
  clinicianReferral ClinicianProfile? @relation("ClinicalReferral", fields: [clinicianReferralId], references: [id])

  // AI Data
  rawScanDataUrl    String?          // Link to S3/Cloud Storage for LiDAR/Video
  aiConfidenceScore Float?
  
  // Results
  riskRecords       RiskRecord[]
  project           Project?
  reportUrl         String?          // The Premium Assessment Report (PDF)
  
  createdAt         DateTime         @default(now())
  updatedAt         DateTime         @updatedAt

  @@index([propertyId])
}

model RiskRecord {
  id                String       @id @default(uuid())
  assessmentId      String
  assessment        Assessment   @relation(fields: [assessmentId], references: [id])
  
  title             String       // e.g., "Narrow Doorway"
  description       String
  severity          RiskSeverity
  
  // AI Attributes
  detectedDimension Float?       // e.g., 30.5 (inches)
  recommendedDim    Float?       // e.g., 36.0 (inches)
  aiConfidence      Float
  
  // HITL (Human-in-the-loop) Verification
  isVerified        Boolean      @default(false)
  verifiedById      String?
  verifiedAt        DateTime?

  // Financial Context
  estimatedCostMin  Decimal      @db.Decimal(10, 2)
  estimatedCostMax  Decimal      @db.Decimal(10, 2)
  
  createdAt         DateTime     @default(now())
}

model Project {
  id                String        @id @default(uuid())
  assessmentId      String        @unique
  assessment        Assessment    @relation(fields: [assessmentId], references: [id])
  
  contractorId      String?
  contractor        ContractorProfile? @relation(fields: [contractorId], references: [id])
  
  status            ProjectStatus @default(ESTIMATING)
  
  // Stripe Connect / Escrow Logic
  totalProjectValue Decimal?      @db.Decimal(10, 2)
  escrowId          String?       @unique // Stripe PI or Transfer ID
  isDepositPaid     Boolean       @default(false)
  
  // Disintermediation Control
  contactInfoReleased Boolean     @default(false)

  createdAt         DateTime      @default(now())
  updatedAt         DateTime      @updatedAt
}

model Transaction {
  id                String   @id @default(uuid())
  userId            String
  user              User     @relation(fields: [userId], references: [id])
  
  amount            Decimal  @db.Decimal(10, 2)
  currency          String   @default("USD")
  stripePaymentId   String   @unique
  
  type              TransactionType
  status            String   // e.g., "succeeded", "refunded"
  
  createdAt         DateTime @default(now())
}

enum TransactionType {
  PREMIUM_REPORT_FEE
  MARKETPLACE_COMMISSION
  PROJECT_DEPOSIT
}
```

---

### 3. Implementation of Longitudinal Record Logic

To support the **"Home Safety FICO"** concept, the schema utilizes the following logic:

1.  **Assessment Versioning:** Every time a modification is completed, a new `Assessment` record is created for the `Property`. By comparing `RiskRecord` counts and severities between `version n` and `version n-1`, the system calculates the **Safety Score Delta**.
2.  **Property-Level Aggregation:** The `Property` model holds the `currentSafetyScore`. This allows for rapid querying by insurers or clinicians without recalculating the entire history of scans.
3.  **Audit Trail:** Every `RiskRecord` is immutable once verified. If a contractor fixes a hazard, the record isn't deleted; it is marked as "resolved" in the subsequent assessment version, preserving the property's lifecycle data.

---

### 4. Data Isolation & Row-Level Security (RLS) Strategy

Given the sensitivity of home safety data and clinical referrals, I recommend the following RLS policies at the PostgreSQL level:

*   **Homeowner Isolation:** `SELECT` on `Property` and `Assessment` is restricted to the `ownerId` matching the `auth.uid()`.
*   **Contractor "Pay-to-Play":** Contractors cannot `SELECT` the `ownerId` or `address` (contact info) in the `Property` table until the `Project.isDepositPaid` flag is `TRUE`.
*   **Clinician Access:** Clinicians can only view `Assessments` where `clinicianReferralId` matches their profile ID, ensuring they only see patients they have actively transitioned.
*   **HITL Security:** Only users with `UserRole.ADMIN` can update the `isVerified` status on `RiskRecord` entities.

### 5. Technical Proficiency Note
This schema uses **Decimal** types for all currency and measurement fields to avoid the floating-point errors common in JavaScript/TypeScript, which is critical for the "Sub-inch Accuracy" requirement. The use of **UUIDs** ensures that project and assessment IDs cannot be guessed via sequential scanning, adding a layer of security against data scraping.
