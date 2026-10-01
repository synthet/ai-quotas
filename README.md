# AI Quotas Dashboard

Local FastAPI dashboard that shows current rate limits and usage pools across AI providers.

Docs:

- [Four providers only (default scope)](docs/ai-quotas-four-providers.md)
- [Full fetching design (surfaces + API)](docs/ai-quotas-fetching-design.md)

## Scope (`QUOTA_SCOPE` in `.env`)

| Value | Providers polled |
|-------|------------------|
| **`cli`** (default) | **Codex**, **Cursor**, **Claude Code** (`/usage`), **Antigravity** (`agy /usage`) — no API probes |
| `api` | OpenAI, Anthropic, Gemini API (inference probes + headers) |
| `all` | Every adapter |

## CLI collectors (default scope)

| Card | Method |
|------|--------|
| **Codex** (ChatGPT subscription) | `codex app-server` → `account/rateLimits/read` |
| **Cursor** | Local auth → `GET cursor.com/api/usage-summary` |
| **Claude** (subscription) | `claude -p /usage` or `~/.claude/.cc-statusbar-quota.json` |
| **Antigravity** | `agy -p /usage --output-format json` |

**Google consumer quota:** use **Antigravity** (`agy`), not Gemini CLI. Google has ended Code Assist sign-in for individuals on `gemini auth login` — migrate per [antigravity.google](https://antigravity.google). The optional `INCLUDE_GEMINI_CLI_COLLECTOR=true` legacy card is only for old OAuth sessions and is not recommended.

API scope (`api` / `all`) adds OpenAI, Anthropic, and Gemini API probe adapters.

## Setup

```powershell
cd D:\Projects\ai-quotas
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env with your API keys
uvicorn app.main:app --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787

## Configuration

See `.env.example`. Probe models (override in `.env`):

| Variable | Default |
|----------|---------|
| `OPENAI_PROBE_MODEL` | `gpt-4o-mini` |
| `ANTHROPIC_PROBE_MODEL` | `claude-haiku-4-5` |
| `GEMINI_PROBE_MODEL` | `gemini-3.8-flash` |

Each API probe uses a small completion (~16 tokens) to read **response headers** (RPM/TPM remaining). That is the only supported way to get live limits without an **Admin API key**.

## How to fetch properly (per provider)

| Provider | What this app does | What you must fix externally |
|----------|-------------------|------------------------------|
| **OpenAI** | Probe → `x-ratelimit-*` on success; on `credit_balance_exhausted` shows **degraded** + billing row | Add prepaid credits at [billing settings](https://platform.openai.com/settings/organization/billing/). No probe can succeed with $0 balance. Optional later: `OPENAI_ADMIN_KEY` for usage/cost APIs (no RPM remaining). |
| **Anthropic** | Probe with `claude-haiku-4-5` (fallback list in code) → `anthropic-ratelimit-*` headers | Standard key has **no** “quota remaining” REST API; headers need a successful probe. Optional: Admin key + `GET /v1/organizations/rate_limits` for configured caps. |
| **Gemini API** | `generateContent` on `gemini-3.8-flash` (fallbacks if retired) | On success Google does **not** return remaining RPM/RPD in headers; 429 gives retry hints. Set `GOOGLE_API_KEY` from AI Studio. |
| **Antigravity** (CLI scope) | `agy -p /usage --output-format json` | Install/sign in to Antigravity CLI; same subscription Google now points consumers to instead of Gemini CLI Code Assist. |
| **Gemini CLI** (legacy, off by default) | Old OAuth → Cloud Code quota RPC | **Do not use** `gemini auth login` for individuals — Google reports the client is unsupported; use Antigravity instead. |
| **Cursor** | Reads IDE token from `state.vscdb` → `GET /api/usage-summary` | Stay logged into Cursor; no API key. Shows plan % and included units. |

After changing `.env` or code, restart uvicorn and click **Refresh now**. If “Last refresh” is old, the page may show stale errors (e.g. old model names).

```powershell
# Quick check without the browser
curl http://127.0.0.1:8787/api/snapshot
```

## Security

- Binds to **127.0.0.1** only. Do not expose this app on a network.
- Never commit `.env` (contains API keys and may touch Cursor session material).

## Risks

- Probe requests cost tokens and count toward RPM.
- Cursor and Gemini CLI quota endpoints are undocumented and may change.
- Reading Cursor `state.vscdb` while the IDE is open can occasionally fail; retry refresh.

## Tests

```powershell
python -m pytest tests/ -q
```
