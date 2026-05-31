# aicosts

Track API spend across Anthropic, OpenAI, GCP, Twilio, and GitHub.
Pulls from each provider's admin/billing API into a local SQLite database; reports
by provider, project, key, or model.

## Install

```sh
git clone <this-repo> ~/workspace/aicosts
cd ~/workspace/aicosts
uv sync
uv tool install --editable .   # exposes `aicosts` on $PATH
```

## Setup

### Anthropic

Admin API key — Console → Settings → Admin Keys (`sk-ant-admin-...`).

```sh
aicosts keys set anthropic-admin-key
```

### OpenAI

Admin API key — platform.openai.com → Settings → Organization → Admin Keys.

```sh
aicosts keys set openai-admin-key
```

### GCP

1. Enable **billing export to BigQuery** in the GCP Console (Billing → Billing export → Standard usage cost). Use dataset name `gcp_costs` (or set `GCP_BILLING_DATASET` env var if different).
2. Grant the service account **BigQuery Data Viewer** on that dataset.
3. Store the service account key:

```sh
aicosts keys set gcp-service-account-key --file /path/to/sa-key.json
```

> First export can take up to 24h to appear after enabling.

> **GCP is pulled on demand only.** Querying the BigQuery billing export is itself
> billable, so `aicosts pull` skips GCP and prints a link to the billing console.
> Pull it explicitly when you want a refresh: `aicosts pull --provider gcp`.
> Override the console link with the `GCP_BILLING_CONSOLE_URL` env var.

### GitHub

1. Create a **fine-grained PAT** at [github.com/settings/tokens](https://github.com/settings/tokens?type=beta) with **Plan → Read** permission.
2. Note your GitHub organization name.

```sh
aicosts keys set github-token   # ghp_... or github_pat_...
aicosts keys set github-org     # your-org-name
```

### Twilio

1. Find your **Account SID** (`ACxxxxx`) at the top of [console.twilio.com](https://console.twilio.com).
2. Create an **API Key** (Console → Account → Keys & Credentials → API Keys) — note the Key SID (`SKxxxxx`) and Secret.

```sh
aicosts keys set twilio-account-sid   # ACxxxxx  — used in the API path
aicosts keys set twilio-api-key       # SKxxxxx  — used for Basic Auth
aicosts keys set twilio-api-secret    # secret shown once at key creation
```

> The Account SID and API Key SID are different. The URL path uses the Account SID (`AC...`); the API key pair (`SK...` + secret) is the auth credential.

### Subscriptions (manual)

Flat recurring charges the provider APIs don't report — an ElevenLabs, GitHub,
Codex, or Claude plan — are tracked by hand. The full charge lands on each billing
date (a $6/mo plan is one $6 event per month, not prorated) and shows up in reports
under the subscription's name.

```sh
aicosts subscriptions add elevenlabs --cost 6 --frequency monthly
aicosts subscriptions add github-pro --cost 4 --frequency monthly --start 2026-01-01
aicosts subscriptions add some-annual --cost 120 --frequency yearly --end 2026-06-30
aicosts subscriptions list
aicosts subscriptions remove elevenlabs
```

`--frequency` is one of `daily | weekly | monthly | yearly`. `--start` (default:
today) is the billing anchor — the day-of-month/year the charge recurs on. Billing
dates are materialized into reports on the next `aicosts pull`.

### Project mapping (optional)

`~/Library/Application Support/aicosts/projects.toml` maps provider IDs to human labels:

```toml
[[project]]
label = "openclaw-agent"
anthropic_workspace_ids = ["wrkspc_..."]
openai_project_ids = ["proj_..."]

[[project]]
label = "voice-calls"
openai_project_ids = ["proj_voice"]
gcp_project_ids = ["saints-podcast"]
twilio_subaccount_sids = ["ACxxxxx"]

[[project]]
label = "eng-platform"
github_orgs = ["my-company"]
```

## Use

```sh
# Pull last 30 days from all providers
aicosts pull

# Pull a specific provider / window
aicosts pull --provider anthropic --since 2026-04-01

# Reports
aicosts report --period month --by provider
aicosts report --period week --by project
aicosts report --period today --by model

# Daily-briefing one-liner
aicosts status
# -> today: $6.70 (openai $4.20, anthropic $2.50*)
#    (* = estimated from token counts; replaces when finalized cost data lands)
```

## Credentials

Credentials are stored in the macOS Keychain by default. For CI/GitHub Actions, set env vars instead — they take precedence over the keychain:

| Key name | Env var |
|---|---|
| `anthropic-admin-key` | `ANTHROPIC_ADMIN_KEY` |
| `openai-admin-key` | `OPENAI_ADMIN_KEY` |
| `gcp-service-account-key` | `GCP_SERVICE_ACCOUNT_KEY` |
| `twilio-account-sid` | `TWILIO_ACCOUNT_SID` |
| `twilio-api-key` | `TWILIO_API_KEY` |
| `twilio-api-secret` | `TWILIO_API_SECRET` |
| `github-token` | `GITHUB_TOKEN` |
| `github-org` | `GITHUB_ORG` |

## Data locations

```sh
aicosts paths
```

- `~/Library/Application Support/aicosts/db.sqlite` — main DB
- `~/Library/Application Support/aicosts/raw/{provider}/{date}.jsonl` — raw API archive
- `~/Library/Application Support/aicosts/projects.toml` — display mapping

## Design notes

- **SQLite + JSONL** — SQLite for queries, JSONL for raw API responses (replay/debug).
- **Provider-side IDs as primary key** — `workspace_id`, `project_id`, etc. survive key rotation. The local `projects.toml` is a display layer only.
- **Estimated vs finalized cost** — Anthropic's `usage_report` is real-time but returns token counts; cost is estimated from a static pricing table and rows are flagged `cost_estimated=1`. `cost_report` is authoritative but lags ~48h; when it catches up it overwrites the estimate.
- **GCP via BigQuery** — GCP pushes billing data to BigQuery automatically; `aicosts pull` queries it directly rather than polling an API.

## Develop

```sh
uv run pytest
```
