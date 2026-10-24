Implement, in Next.js (App Router), Supabase, and the Google GenAI SDK, a technical architecture for the following:

SoloSuite AI — the autonomous agency running the business behind your brand.

SoloSuite AI is a multi-agent system that acts as a virtual talent agency, business manager and administrative assistant for solo creators. It has three specialised LLM agents: a Negotiator Agent that reads incoming brand emails and drafts sponsorship proposals, a Trend Agent that analyses performance data to suggest future content topics, and an Operations Agent that manages scheduling and invoice generation.

Audience: full-time solo content creators, influencers and digital entrepreneurs who want to focus on creating instead of administration.

The sections below are REQUIREMENTS, not suggestions. Every subtask must use the names in the Shared Contract exactly — no subtask may invent its own table, column, field, route or event name. If a subtask needs something the contract lacks, it must say so explicitly instead of inventing it.

---

## 1. Stack (exact)

- Next.js 16, App Router, TypeScript strict, Turbopack. Relative imports are extensionless (`./lib/x`, never `./lib/x.js`).
- Supabase: Postgres + Auth (email + password) + Row Level Security on every table. Server-side clients via `@supabase/ssr`.
- Google GenAI SDK `@google/genai`, using the **Interactions API** (`client.interactions.create`), with `previous_interaction_id` for multi-turn and function calling. Not `generateContent`, not `chats.create`, not the deprecated `@google/generative-ai`.
- Tailwind CSS for styling. `globals.css` is imported by the root layout.
- Environment variables (only these): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, `CRON_SECRET`, `RESEND_API_KEY` (optional), `STRIPE_SECRET_KEY` (optional).

## 2. User flows (each must be fully specified end to end)

1. **Sign up** at `/login` (email + password). A database trigger on `auth.users` inserts the matching `creator_profiles` row. Nothing else creates profiles.
2. **Sign in** at `/login`, then redirect to `/`. Every page except `/login` redirects signed-out users to `/login`.
3. **Inbound brand email → deal → proposal → approval**:
   1. `POST /api/webhooks/inbound-email` receives the email; the creator is identified by the address `deals+<creator_id>@...`.
   2. It stores an `email_threads` row and **creates a `brand_deals` row** (status `INQUIRY_RECEIVED`) for the sender's brand.
   3. It runs the guardrail pre-checks, then the Negotiator Agent, whose output is a `NegotiatorProposal` (section 4).
   4. It inserts a `pending_agent_actions` row: `action_type = 'SEND_COUNTER_PROPOSAL'`, `payload` = that proposal plus the guardrail verdict. Nothing is sent yet.
   5. The creator reviews it on `/`, may edit the draft, then approves or rejects it via `POST /api/actions/[id]/approve` or `/reject`.
   6. Approve sends the email (only if `RESEND_API_KEY` is set) and moves the deal to `COUNTER_OFFER_SENT`. Reject marks the action `REJECTED`.
4. **Trend analysis**: the creator requests it from the dashboard; `POST /api/agents/trend` runs the Trend Agent for the **signed-in** creator (never a creator id taken from the request body) and returns a `TrendReport`. The dashboard renders the returned report — no hard-coded sample trends.
5. **Operations (cron)**: `GET /api/cron/operations` with `Authorization: Bearer <CRON_SECRET>` runs the Operations Agent for each deal in `CONTRACT_SIGNED` or `DELIVERABLES_IN_PROGRESS`. Anything with an external effect (invoice, calendar block) becomes a `pending_agent_actions` row for approval; invoices are created as `invoices` rows with status `DRAFT` and nothing reaches Stripe until approved.
6. **Settings**: `/settings` lets the creator edit their `creator_safety_policies` row (limits and blacklists in section 5).

## 3. Pages (these and only these)

| Route | Purpose |
|---|---|
| `/login` | Sign in / create account |
| `/` | Dashboard: KPI cards, Action Center (pending actions, live via Supabase Realtime), Deal Pipeline (from `brand_deals`), Trend panel (from `POST /api/agents/trend`), recent agent activity (from `agent_audit_logs`) |
| `/settings` | Safety policy editor |

No component may render hard-coded sample data. An empty table renders an empty state.

## 4. Shared Contract (every subtask uses these names verbatim)

### Tables (all have `id uuid primary key default gen_random_uuid()`, `created_at timestamptz default now()`, RLS: a creator reads/writes only rows where `creator_id = auth.uid()`)

- `creator_profiles` — `id uuid primary key references auth.users on delete cascade`, `display_name text not null`, `niche text[] default '{}'`, `minimum_cpm numeric(10,2) default 25`
- `creator_safety_policies` — `creator_id uuid primary key references creator_profiles on delete cascade`, `signature_threshold_cents bigint default 50000`, `minimum_rate_cents bigint default 15000`, `auto_invoice_limit_cents bigint default 100000`, `blacklisted_domains text[] default '{}'`, `blacklisted_keywords text[] default '{crypto,casino,gambling}'`, `max_agent_calls_per_hour int default 60`, `daily_budget_usd numeric(10,2) default 10`
- `email_threads` — `creator_id`, `sender_email text`, `subject text`, `body_text text`, `message_id text unique`, `status email_status`
- `brand_deals` — `creator_id`, `email_thread_id uuid references email_threads on delete set null`, `brand_name text`, `contact_email text`, `agreed_value_cents bigint`, `status deal_status`
- `operations_tasks` — `creator_id`, `deal_id uuid references brand_deals on delete cascade`, `title text`, `due_date timestamptz`, `status milestone_status`
- `invoices` — `creator_id`, `deal_id`, `amount_cents bigint`, `due_date timestamptz`, `status invoice_status default 'DRAFT'`, `stripe_invoice_id text`
- `pending_agent_actions` — `creator_id`, `deal_id uuid null`, `agent agent_type`, `action_type action_type`, `payload jsonb`, `status action_status default 'AWAITING_REVIEW'`
- `agent_audit_logs` — `creator_id`, `deal_id null`, `agent agent_type`, `interaction_id text`, `model text`, `duration_ms int`, `input_tokens int`, `output_tokens int`, `estimated_cost_usd numeric(12,6)`, `guardrail_status guardrail_status`, `guardrail_reasons text[]`, `tool_calls jsonb default '[]'`

Deleting an account must succeed: any audit trigger must not block the cascade.

### Enums

- `agent_type`: `NEGOTIATOR`, `TREND`, `OPERATIONS`
- `action_type`: `SEND_COUNTER_PROPOSAL`, `DISPATCH_INVOICE`, `SCHEDULE_EVENT`
- `action_status`: `AWAITING_REVIEW`, `APPROVED`, `REJECTED`, `EXECUTED`, `FAILED`
- `deal_status`: `INQUIRY_RECEIVED`, `COUNTER_OFFER_SENT`, `NEGOTIATING`, `CONTRACT_SIGNED`, `DELIVERABLES_IN_PROGRESS`, `INVOICED`, `PAID`, `DECLINED`
- `email_status`: `RECEIVED`, `PROCESSED`, `BLOCKED`
- `milestone_status`: `PENDING`, `SUBMITTED`, `VERIFIED`, `INVOICED`
- `invoice_status`: `DRAFT`, `ISSUED`, `PAID`, `VOID`
- `guardrail_status`: `PASSED`, `FLAGGED`, `BLOCKED`

### Payload types (TypeScript, in `src/lib/contracts.ts`; agents produce them, the UI reads them)

```ts
export interface ProposedDeliverable { platform: string; placementType: string; rateCents: number }
export interface NegotiatorProposal {
  brandName: string; dealFitScore: number; flaggedConcerns: string[];
  proposedDeliverables: ProposedDeliverable[]; totalCents: number; draftEmail: string;
}
export interface TrendReport { macroTrends: string[]; ideas: { title: string; premise: string; hook: string }[] }
export interface GuardrailVerdict { status: "PASSED" | "FLAGGED" | "BLOCKED"; requiresSignature: boolean; reasons: string[] }
export interface PendingActionPayload<T> { output: T; guardrail: GuardrailVerdict; toolCalls: { name: string; isError: boolean }[] }
```

Money is always integer cents. The UI's KPI "value awaiting approval" is the sum of `payload.output.totalCents` over `AWAITING_REVIEW` actions.

### API routes (request → response, all JSON unless stated)

| Route | Auth | Request | Response |
|---|---|---|---|
| `POST /api/webhooks/inbound-email` | shared secret header | `{ to, from, subject, text, messageId }` | `{ threadId }` |
| `POST /api/agents/trend` | signed-in session | `{ metrics: object }` | `TrendReport & { guardrail: GuardrailVerdict }` |
| `POST /api/actions/[id]/approve` | signed-in, owns action | `{ editedPayload?: object }` | `{ status }` |
| `POST /api/actions/[id]/reject` | signed-in, owns action | `{}` | `{ status }` |
| `GET /api/cron/operations` | `Bearer CRON_SECRET` | — | `{ processed: number }` |

No route streams. If one ever should, the contract must say so and the UI must read a stream.

## 5. Agents, tools and guardrails

- Every agent call goes through ONE wrapper that, in order: checks rate limit and daily spend (from `agent_audit_logs`), screens inbound content against the blacklists, runs the agent, checks its output and every tool call's arguments against the monetary limits, and writes one `agent_audit_logs` row.
- Every agent's `system_instruction` includes the same anti-hallucination constraints (never invent rates, past deals or availability; say when data is missing).
- **Tools are executed, not just declared**: specify the loop — run each `function_call` step, send `function_result` inputs back with `previous_interaction_id`, repeat until a final answer (cap the rounds), and sum token usage across rounds.
- Tool handlers take the creator (and deal) from the server context, never from the model's arguments.
- Negotiator tools (read-only): `getRateCard`, `checkAvailability(month)`. Operations tools: `createDraftInvoice`, `updateMilestone` (this deal only), `proposeCalendarBlock` (queued for approval; no calendar integration).
- Output formats are JSON schemas matching the Shared Contract types.

## 6. For the factory that builds this

- Every subtask declares `files`: the exact paths it creates, under `src/`, `tests/` or `supabase/`. Two subtasks never own the same file. Every page and route in sections 3–4 is owned by exactly one subtask.
- Subtasks never edit `package.json`, `tsconfig.json`, `next.config.*` or the root layout's stylesheet import; list any npm packages they need instead.
- Every module a subtask creates is imported by a page, route, or another module that is. No file exists only to be imported by a test.
- Database migrations live in `supabase/migrations/` and are numbered; the schema subtask owns all of them.

## 7. Out of scope

Payments beyond draft Stripe invoices, calendar integrations, social-platform APIs, vector/embedding memory, MCP servers, multi-user teams.
