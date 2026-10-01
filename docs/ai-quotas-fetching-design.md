# AI Quotas — Proper Usage and Quota Fetching Design

## Goal

Build a local **AI Quotas** dashboard that reports real quota and usage information for:

- OpenAI / ChatGPT / Codex
- OpenAI API
- Anthropic / Claude
- Anthropic API
- Google Antigravity
- Gemini API
- Cursor

The key design rule is to **separate consumer subscription quotas from API billing/rate limits**.

A subscription meter such as:

```text
Weekly
9% remaining
Resets Oct 3 at 12:14
```

is not the same thing as an API billing error such as:

```text
credit_balance_exhausted
```

These must be represented as different quota surfaces.

### Implementation map (this repo)

| Design surface | Status in `ai-quotas` |
|----------------|----------------------|
| `chatgpt_codex` | `CodexSubscriptionAdapter` — CLI scope |
| `claude_subscription` | `ClaudeSubscriptionAdapter` — CLI scope |
| `antigravity` | `AntigravityAdapter` — CLI scope |
| `cursor` | `CursorAdapter` — CLI scope |
| `openai_api` | `OpenAIAdapter` — `QUOTA_SCOPE=api` or `all` |
| `anthropic_api` | `AnthropicAdapter` — API scope |
| `gemini_api` | `GeminiApiAdapter` — API scope |
| Gemini CLI (legacy) | `GeminiCliAdapter` — optional `INCLUDE_GEMINI_CLI_COLLECTOR` |

Default `QUOTA_SCOPE=cli` polls only the four CLI surfaces above. See [ai-quotas-four-providers.md](ai-quotas-four-providers.md) for the product-level scope (Codex, Cursor, Claude, Antigravity only).

---

# 1. Recommended Provider/SURFACE Split

Use separate collectors and separate dashboard cards for each product surface.

| Provider | Surface | What it measures |
|---|---|---|
| OpenAI | `chatgpt_codex` | ChatGPT / Codex plan allowance |
| OpenAI | `openai_api` | API usage, spend, RPM, TPM |
| Anthropic | `claude_subscription` | Claude / Claude Code subscription quotas |
| Anthropic | `anthropic_api` | API rate limits and usage |
| Google | `antigravity` | Antigravity subscription quota |
| Google | `gemini_api` | Gemini API project quotas |
| Cursor | `cursor` | Cursor monthly included usage |

Do **not** merge these into one provider-level number.

---

# 2. OpenAI — ChatGPT / Codex Subscription Quotas

## Recommended source

Use the local Codex app-server:

```bash
codex app-server --stdio
```

It exposes account-level rate-limit information through:

```text
account/rateLimits/read
```

This is the correct source for the ChatGPT/Codex allowance shown in the UI.

## Initialize

Send JSON-RPC over stdin/stdout.

```json
{
  "method": "initialize",
  "id": 1,
  "params": {
    "clientInfo": {
      "name": "ai-quotas",
      "title": "AI Quotas",
      "version": "0.1.0"
    },
    "capabilities": {
      "experimentalApi": true
    }
  }
}
```

Then request rate limits:

```json
{
  "method": "account/rateLimits/read",
  "id": 2,
  "params": {
    "supportsLunaReserve": true
  }
}
```

## Example response shape

A response can contain data similar to:

```json
{
  "rateLimits": {
    "limitId": "codex",
    "primary": {
      "usedPercent": 25,
      "windowDurationMins": 300,
      "resetsAt": 1779459394
    },
    "secondary": {
      "usedPercent": 91,
      "windowDurationMins": 10080,
      "resetsAt": 1779826837
    },
    "credits": {
      "hasCredits": false,
      "unlimited": false,
      "balance": "0"
    },
    "planType": "plus"
  }
}
```

Interpretation:

```text
primary
  300 minutes
  = 5-hour window

secondary
  10080 minutes
  = 7-day / weekly window
```

Example:

```text
usedPercent = 91
remainingPercent = 9
```

becomes:

```text
Weekly
Used:       91%
Remaining:   9%
Reset:       ...
```

## Normalization

```ts
function normalizeCodexWindow(
  provider: "openai",
  metric: string,
  value: {
    usedPercent?: number;
    windowDurationMins?: number;
    resetsAt?: number;
  }
): QuotaMetric {
  const usedPercent = value.usedPercent;

  return {
    provider,
    surface: "chatgpt_codex",
    metric,
    windowSeconds: value.windowDurationMins
      ? value.windowDurationMins * 60
      : undefined,
    usedPercent,
    remainingPercent:
      usedPercent == null ? undefined : Math.max(0, 100 - usedPercent),
    unit: "percent",
    resetsAt: value.resetsAt
      ? new Date(value.resetsAt * 1000).toISOString()
      : undefined,
    source: "official_local_cli",
    fetchedAt: new Date().toISOString()
  };
}
```

## Important

Do **not** use an OpenAI API inference request to discover ChatGPT subscription quota.

This:

```text
credit_balance_exhausted
```

means the API billing account has no usable API credit.

It does **not** mean the ChatGPT weekly quota is exhausted.

---

# 3. OpenAI API

Treat the OpenAI API as a completely separate surface.

## Historical usage

Use the organization usage APIs.

Typical endpoint:

```http
GET /v1/organization/usage/completions
```

Use this for historical token/request usage.

## Cost

Use:

```http
GET /v1/organization/costs
```

Use this for monetary API spend.

## Spend limit

If available for the account:

```http
GET /v1/organization/spend_limit
```

## Short-term rate limits

Read rate-limit headers from normal API responses.

Relevant headers include:

```text
x-ratelimit-limit-requests
x-ratelimit-remaining-requests
x-ratelimit-reset-requests

x-ratelimit-limit-tokens
x-ratelimit-remaining-tokens
x-ratelimit-reset-tokens

x-ratelimit-limit-project-tokens
x-ratelimit-remaining-project-tokens
x-ratelimit-reset-project-tokens
```

These provide values such as:

```text
RPM
TPM
project TPM
reset time
```

## Billing errors

If inference fails with:

```text
credit_balance_exhausted
```

store this as a **billing state**, not a quota metric.

Recommended representation:

```json
{
  "provider": "openai",
  "surface": "openai_api",
  "status": "blocked",
  "reason": "credit_balance_exhausted"
}
```

Do not convert it into:

```text
billing_credits = 0
remaining = 0
```

unless you actually retrieved a billing balance from an authoritative billing endpoint.

---

# 4. Anthropic — Claude Subscription

Claude subscription quotas and Anthropic API quotas should also be separated.

Use a distinct surface:

```text
claude_subscription
```

Possible windows include:

```text
5 hour
7 day
model-specific weekly quota
```

A normalized internal form could look like:

```json
{
  "provider": "anthropic",
  "surface": "claude_subscription",
  "metric": "weekly",
  "usedPercent": 18,
  "remainingPercent": 82,
  "unit": "percent",
  "resetsAt": "..."
}
```

## Recommended collection strategy

For a local-only application, prefer consuming quota information already exposed by Claude Code or its local status integration.

Avoid issuing artificial model prompts every 5 minutes only to discover quota status.

A good local design is:

```text
Claude Code
    ↓
local status/quota JSON
    ↓
AI Quotas collector
    ↓
normalized quota model
```

---

# 5. Anthropic API

Use a separate surface:

```text
anthropic_api
```

## Configured rate limits

Typical Admin API endpoint:

```http
GET /v1/organizations/rate_limits
```

## Usage

Typical endpoint:

```http
GET /v1/organizations/usage_report/messages
```

## Costs

Typical endpoint:

```http
GET /v1/organizations/cost_report
```

## Runtime response headers

Anthropic responses can expose headers such as:

```text
anthropic-ratelimit-requests-limit
anthropic-ratelimit-requests-remaining
anthropic-ratelimit-requests-reset

anthropic-ratelimit-input-tokens-limit
anthropic-ratelimit-input-tokens-remaining
anthropic-ratelimit-input-tokens-reset

anthropic-ratelimit-output-tokens-limit
anthropic-ratelimit-output-tokens-remaining
anthropic-ratelimit-output-tokens-reset
```

Normalize them into separate metrics:

```text
requests_per_minute
input_tokens_per_minute
output_tokens_per_minute
```

---

# 6. Google Antigravity

For individual Google AI subscription usage, prefer **Antigravity** rather than the older Gemini CLI flow.

Use:

```bash
agy -p /usage --output-format json
```

The result is machine-readable and suitable for a local quota dashboard.

Typical useful fields are conceptually similar to:

```text
groups[].buckets[]
```

with values such as:

```text
window
remaining_fraction
reset_time
```

Normalize:

```ts
const remainingPercent = bucket.remaining_fraction * 100;
const usedPercent = 100 - remainingPercent;
```

Recommended surface:

```text
antigravity
```

Recommended source:

```text
official_local_cli
```

Do not generate dummy Gemini prompts solely to measure subscription quota.

---

# 7. Gemini API

The Gemini API is a separate project/API quota surface.

Recommended surface:

```text
gemini_api
```

Track API-specific dimensions such as:

```text
RPM = requests per minute
TPM = tokens per minute
RPD = requests per day
```

Do not bind quota collection to a specific inference model such as:

```text
gemini-2.0-flash
```

A retired model should not break quota collection.

Bad design:

```text
call model
if call succeeds -> infer quota
```

Better design:

```text
Cloud Quotas
    ↓
configured limits

Cloud Monitoring
    ↓
actual usage
```

Conceptual model:

```text
limit      = quota configuration
usage      = monitoring metric
remaining  = limit - usage
```

Keep model dimensions when the provider exposes different quotas per model.

---

# 8. Cursor

Your current Cursor approach is reasonable for a local-only utility.

Example values:

```text
composer_auto
api_pool
total
plan_included
billing_month
reset timestamp
```

Normalize these separately.

Example:

```json
{
  "provider": "cursor",
  "surface": "cursor",
  "metric": "composer_auto",
  "usedPercent": 9.6067,
  "remainingPercent": 90.3933,
  "unit": "percent",
  "resetsAt": "2026-10-24T01:18:31.000Z",
  "source": "private_dashboard"
}
```

## Important

If this data is read from Cursor's internal dashboard endpoint or IDE state, mark it as unofficial/private.

Recommended:

```text
source = private_dashboard
stability = unofficial
```

Avoid labeling it:

```text
source = dashboard_api
```

because that implies a documented public API.

Also:

```text
missing field != zero
```

If the source changes schema, render the field as unavailable instead of silently converting it to `0`.

---

# 9. Unified Data Model

Recommended TypeScript model:

```ts
type Provider =
  | "openai"
  | "anthropic"
  | "google"
  | "cursor";

type Surface =
  | "chatgpt_codex"
  | "openai_api"
  | "claude_subscription"
  | "anthropic_api"
  | "antigravity"
  | "gemini_api"
  | "cursor";

type QuotaSource =
  | "official_api"
  | "official_local_cli"
  | "response_headers"
  | "private_dashboard"
  | "error_only";

type QuotaUnit =
  | "percent"
  | "requests"
  | "tokens"
  | "usd"
  | "credits"
  | "included_units";

interface QuotaMetric {
  provider: Provider;
  surface: Surface;

  metric: string;

  windowSeconds?: number;
  windowLabel?: string;

  used?: number;
  limit?: number;
  remaining?: number;

  usedPercent?: number;
  remainingPercent?: number;

  unit: QuotaUnit;

  resetsAt?: string;

  source: QuotaSource;

  fetchedAt: string;
}
```

Recommended provider-level state:

```ts
interface ProviderSurfaceStatus {
  provider: Provider;
  surface: Surface;

  status:
    | "ok"
    | "partial"
    | "unauthorized"
    | "blocked"
    | "unavailable"
    | "error";

  message?: string;

  metrics: QuotaMetric[];

  fetchedAt: string;
}
```

---

# 10. Source Metadata

Add source metadata to each collector.

```ts
interface CollectorMetadata {
  source:
    | "official_api"
    | "official_local_cli"
    | "response_headers"
    | "private_dashboard"
    | "error_only";

  stability:
    | "official"
    | "semi_official"
    | "unofficial";

  authType?:
    | "local_session"
    | "oauth"
    | "api_key"
    | "admin_api_key"
    | "browser_session";

  refreshRecommendedSeconds?: number;
}
```

Example:

```json
{
  "source": "private_dashboard",
  "stability": "unofficial",
  "authType": "local_session",
  "refreshRecommendedSeconds": 300
}
```

---

# 11. Collector Architecture

Recommended structure:

```text
collectors/
├── openai/
│   ├── codex_subscription.ts
│   ├── api_usage.ts
│   ├── api_cost.ts
│   └── api_rate_limits.ts
│
├── anthropic/
│   ├── claude_subscription.ts
│   ├── api_usage.ts
│   ├── api_cost.ts
│   └── api_rate_limits.ts
│
├── google/
│   ├── antigravity.ts
│   ├── gemini_quota.ts
│   └── gemini_monitoring.ts
│
├── cursor/
│   └── cursor_dashboard.ts
│
└── normalize/
    ├── quota.ts
    └── reset_time.ts
```

Each collector should return:

```ts
Promise<ProviderSurfaceStatus>
```

---

# 12. Refresh Policy

Do not query every source at the same frequency.

Recommended defaults:

```text
ChatGPT / Codex
    60–300 seconds

Claude subscription
    60–300 seconds

Antigravity
    60–300 seconds

Cursor
    300 seconds

API usage reports
    5–15 minutes

API costs
    15–60 minutes

Response headers
    capture opportunistically from real requests
```

Avoid creating billable requests solely to refresh quota state.

---

# 13. Error Handling

Do not convert every failure into a fake quota metric.

Use explicit status values.

Examples:

```json
{
  "status": "unauthorized",
  "message": "Re-authenticate Antigravity"
}
```

```json
{
  "status": "blocked",
  "message": "OpenAI API billing credits exhausted"
}
```

```json
{
  "status": "unavailable",
  "message": "Provider does not expose this quota through a supported API"
}
```

```json
{
  "status": "partial",
  "message": "Usage available, configured limit unavailable"
}
```

---

# 14. Do Not Infer Zero From Missing Data

Never do:

```ts
const remaining = response.remaining ?? 0;
```

Prefer:

```ts
const remaining =
  response.remaining === undefined
    ? undefined
    : response.remaining;
```

Display:

```text
—
```

instead of:

```text
0
```

when the provider did not supply the field.

This distinction is particularly important for:

```text
billing
model-specific limits
subscription quotas
monthly included pools
```

---

# 15. Dashboard UI

Recommended layout:

```text
OpenAI · ChatGPT / Codex                         OK

5 hour
Used             12%
Remaining        88%
Reset            22:14

Weekly
Used             91%
Remaining         9%
Reset            Oct 3 12:14

Source           Codex app-server
```

Separate API card:

```text
OpenAI · API                                  BLOCKED

Billing
credit_balance_exhausted

Monthly cost
—

RPM
—

TPM
—

Source
OpenAI API
```

Claude:

```text
Claude · Subscription                           OK

5 hour          31% used
Weekly          18% used
```

Anthropic API:

```text
Anthropic · API                                 OK

RPM
Input TPM
Output TPM
Monthly cost
```

Google:

```text
Google · Antigravity                            OK

5 hour
Weekly
Model pool
```

Gemini API:

```text
Google · Gemini API                             OK

RPM
TPM
RPD
```

Cursor:

```text
Cursor                                           OK

Billing month
Composer Auto     9.61% used
API Pool          0.00% used
Total             9.37% used
Reset             Oct 24

Source
Cursor private dashboard
```

---

# 16. Suggested Normalized Example

```json
[
  {
    "provider": "openai",
    "surface": "chatgpt_codex",
    "metric": "five_hour",
    "windowSeconds": 18000,
    "usedPercent": 12,
    "remainingPercent": 88,
    "unit": "percent",
    "resetsAt": "2026-10-01T03:14:00Z",
    "source": "official_local_cli",
    "fetchedAt": "2026-10-01T01:17:37Z"
  },
  {
    "provider": "openai",
    "surface": "chatgpt_codex",
    "metric": "weekly",
    "windowSeconds": 604800,
    "usedPercent": 91,
    "remainingPercent": 9,
    "unit": "percent",
    "resetsAt": "2026-10-03T17:14:00Z",
    "source": "official_local_cli",
    "fetchedAt": "2026-10-01T01:17:37Z"
  },
  {
    "provider": "cursor",
    "surface": "cursor",
    "metric": "composer_auto",
    "usedPercent": 9.6067,
    "remainingPercent": 90.3933,
    "unit": "percent",
    "resetsAt": "2026-10-24T01:18:31Z",
    "source": "private_dashboard",
    "fetchedAt": "2026-10-01T01:17:37Z"
  }
]
```

---

# 17. Highest-Priority Implementation Changes

1. Replace the current OpenAI inference probe with:

   ```text
   codex app-server
       → account/rateLimits/read
   ```

2. Split:

   ```text
   openai
   ```

   into:

   ```text
   chatgpt_codex
   openai_api
   ```

3. Store:

   ```text
   credit_balance_exhausted
   ```

   as an API billing status, not a quota metric.

4. Replace obsolete Gemini CLI subscription collection with:

   ```bash
   agy -p /usage --output-format json
   ```

5. Split Claude into:

   ```text
   claude_subscription
   anthropic_api
   ```

6. Keep Cursor's current collector, but label its source:

   ```text
   private_dashboard
   unofficial
   ```

7. Never generate dummy inference traffic solely to refresh quota status.

8. Never convert missing values to zero.

---

# 18. Recommended Final Provider Matrix

| Surface | Best Source | Auth | Stability |
|---|---|---|---|
| ChatGPT / Codex | `codex app-server` | ChatGPT local session | Official local |
| OpenAI API usage | Organization usage API | Admin API key | Official |
| OpenAI API cost | Organization cost API | Admin API key | Official |
| OpenAI API RPM/TPM | Response headers | API key | Official |
| Claude subscription | Local Claude Code quota state | Local session | Semi-official/local |
| Anthropic API | Admin APIs + headers | Admin/API key | Official |
| Antigravity | `agy -p /usage --output-format json` | Google local auth | Official local |
| Gemini API | Cloud Quotas + Monitoring | Google Cloud auth | Official |
| Cursor | Cursor dashboard/internal state | Local session | Unofficial/private |

---

# 19. Summary

The dashboard should model **quota surfaces**, not just providers.

The same company may expose several independent limit systems:

```text
OpenAI
├── ChatGPT / Codex subscription
├── API billing
├── API usage
└── API short-term rate limits
```

and similarly:

```text
Anthropic
├── Claude subscription
└── Anthropic API
```

```text
Google
├── Antigravity subscription
└── Gemini API
```

This avoids false states such as:

```text
OpenAI
0 credits
```

when the actual user-facing ChatGPT quota is:

```text
Weekly
9% remaining
```

The most important implementation improvement is therefore:

```text
codex app-server
    ↓
account/rateLimits/read
    ↓
normalize 5-hour + weekly windows
    ↓
AI Quotas dashboard
```
