# aicosts

Track API spend across Anthropic, OpenAI, and GCP.
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
