# AI Quotas — Four CLI/Chat Providers Only

## Scope

Track **current consumer/CLI subscription quota only** for exactly four products:

1. **Codex**
2. **Cursor**
3. **Claude**
4. **Antigravity**

Do **not** include:

- OpenAI API
- Anthropic API
- Gemini API
- API billing
- API credits
- API RPM / TPM / RPD
- organization spend
- dummy inference probes

The dashboard should answer only:

> **How much quota is used, how much remains, and when does it reset?**

### This repository

| Product | Collector | `QUOTA_SCOPE` |
|---------|-----------|---------------|
| Codex | `app/providers/codex_adapter.py` | `cli` (default) |
| Cursor | `app/providers/cursor_adapter.py` | `cli` |
| Claude | `app/providers/claude_subscription_adapter.py` | `cli` |
| Antigravity | `app/providers/antigravity_adapter.py` | `cli` |

Set `QUOTA_SCOPE=cli` in `.env`. See also [ai-quotas-fetching-design.md](ai-quotas-fetching-design.md) for surface names (`chatgpt_codex`, `claude_subscription`, etc.) and API-scope adapters when explicitly enabled.

**Gaps vs this spec:** single global `PROBE_INTERVAL_SEC` (not per-provider 120s/300s); failed collector runs replace that card with `error` rather than marking prior metrics `stale` (planned improvement).

---

# 1. Codex

## Target

Track the current ChatGPT/Codex subscription quota used by Codex.

Typical fields:

```text
5-hour usage
weekly usage
remaining %
reset time
plan
```

## Preferred source

Use the local Codex app server:

```bash
codex app-server --stdio
```

Then query:

```text
account/rateLimits/read
```

Normalize returned windows by duration rather than assuming fixed names.

Example:

```ts
{
  provider: "codex",
  metric: "weekly",
  usedPercent: 91,
  remainingPercent: 9,
  resetsAt: "...",
  source: "codex_app_server"
}
```

## Fallback

If needed, use the authenticated ChatGPT/Codex subscription usage endpoint already used by the logged-in Codex client.

Treat that endpoint as private/internal and less stable than the app-server protocol.

---

# 2. Cursor

## Target

Track the current Cursor subscription usage for chat/Agent/Composer/model pools.

Typical useful fields:

```text
current usage %
remaining %
billing-cycle reset
pool name
```

Example:

```text
Cursor Models
9.6% used
90.4% remaining
resets Oct 24
```

## Source

Use the local Cursor dashboard / IDE state collector that is already working.

Treat the source as:

```text
private_dashboard
```

not as a documented public API.

Important:

```text
missing != 0
```

If a field disappears after a Cursor update, show it as unavailable instead of converting it to zero.

---

# 3. Claude

## Target

Track Claude / Claude Code subscription quota.

Typical fields:

```text
5-hour usage
weekly usage
model-specific weekly usage, if present
remaining %
reset time
```

## Preferred source

Use Claude Code's existing authenticated subscription state.

A local collector can consume the quota information exposed by Claude Code, for example via its status-line data or current subscription usage state.

Normalize:

```ts
{
  provider: "claude",
  metric: "5h",
  usedPercent: 31,
  remainingPercent: 69,
  resetsAt: "...",
  source: "claude_local"
}
```

Do not use Anthropic developer API usage/billing endpoints.

---

# 4. Antigravity

## Target

Track the current Google Antigravity subscription quota.

Typical fields:

```text
current model/group quota
remaining %
reset time
window
```

## Preferred source

Use Antigravity's machine-readable usage command:

```bash
agy -p /usage --output-format json
```

or equivalently:

```bash
agy --print /usage --output-format json
```

Normalize each returned bucket independently.

Example:

```ts
{
  provider: "antigravity",
  metric: "weekly",
  usedPercent: 40,
  remainingPercent: 60,
  resetsAt: "...",
  source: "antigravity_cli"
}
```

Do not query Gemini developer APIs.

**Note:** `gemini auth login` / Gemini CLI Code Assist for individuals is discontinued. Google directs users to [Antigravity](https://antigravity.google) instead; that is why this project uses `agy`, not Gemini CLI OAuth.

---

# Unified Model

```ts
type Provider =
  | "codex"
  | "cursor"
  | "claude"
  | "antigravity";

type QuotaSource =
  | "codex_app_server"
  | "private_dashboard"
  | "claude_local"
  | "antigravity_cli";

interface CurrentQuota {
  provider: Provider;

  metric: string;
  label?: string;

  usedPercent?: number;
  remainingPercent?: number;

  windowSeconds?: number;
  resetsAt?: string;

  model?: string;

  source: QuotaSource;

  fetchedAt: string;
}
```

---

# Collector Layout

```text
collectors/
├── codex.ts
├── cursor.ts
├── claude.ts
└── antigravity.ts
```

Each collector should return:

```ts
interface ProviderQuotaSnapshot {
  provider: Provider;

  status:
    | "ok"
    | "stale"
    | "unauthorized"
    | "unavailable"
    | "error";

  metrics: CurrentQuota[];

  fetchedAt: string;
  lastSuccessfulFetch?: string;
  error?: string;
}
```

In this repo the equivalent modules live under `app/providers/` (`codex_adapter.py`, `cursor_adapter.py`, `claude_subscription_adapter.py`, `antigravity_adapter.py`).

---

# Refresh Policy

Suggested defaults:

```text
Codex          120 s
Claude         120 s
Antigravity    120 s
Cursor         300 s
```

Also support:

```text
Refresh now
```

Never turn a failed refresh into:

```text
remaining = 0
```

Instead preserve the last successful snapshot and mark it stale.

---

# UI

Recommended dashboard:

```text
AI Quotas

Codex
5 hour     24% used · 76% left
Weekly     91% used ·  9% left
Reset      Oct 3 12:14

Claude
5 hour     31% used · 69% left
Weekly     18% used · 82% left
Reset      ...

Antigravity
Current    40% used · 60% left
Reset      ...

Cursor
Models      9.6% used · 90.4% left
Reset       Oct 24
```

Only show metrics actually returned by each provider.

---

# Implementation Priority

## P0

### Codex

```text
codex app-server
→ account/rateLimits/read
```

### Cursor

Keep the current working local dashboard/IDE collector.

### Claude

Read current Claude Code subscription quota state locally.

### Antigravity

```bash
agy -p /usage --output-format json
```

---

# Final Scope

```text
AI Quotas
├── Codex
├── Cursor
├── Claude
└── Antigravity
```

Nothing else.

No developer API quota collection is required.
