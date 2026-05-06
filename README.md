# aicosts

Track API spend across Anthropic, OpenAI, Twilio, and other paid services.
Pulls from each provider's admin/billing API into a local SQLite database; reports
by provider, project, key, or model.

Tracking issue: [dot-openclaw#13](https://github.com/davidrossdegroot/dot-openclaw/issues/13).

## Status — Phase 1 (MVP)

- [x] CLI scaffolding (`pull`, `report`, `status`, `paths`, `keys`)
- [x] SQLite storage + JSONL raw archive
- [x] macOS Keychain credential storage
- [x] Anthropic provider (cost_report + usage_report)
- [x] OpenAI provider (organization/costs)
- [ ] Phase 2: Telegram budget alerts
- [ ] Phase 3: Twilio + ngrok + static fixed-costs YAML
- [ ] Phase 4 (optional): GCP via BigQuery billing export

## Install

```sh
git clone <this-repo> ~/workspace/aicosts
cd ~/workspace/aicosts
uv sync
uv tool install --editable .   # exposes `aicosts` on $PATH
```

## Setup

Both Anthropic and OpenAI cost endpoints require **admin** API keys, which are
distinct from your regular `sk-ant-api03-...` / `sk-...` keys.

1. **Anthropic admin key** — Console → Settings → Admin Keys.
   Stored in macOS Keychain under service `aicosts`, key `anthropic-admin-key`.

   ```sh
   aicosts keys set anthropic-admin-key
   ```

2. **OpenAI admin key** — platform.openai.com → Settings → Organization → Admin
   keys.

   ```sh
   aicosts keys set openai-admin-key
   ```

3. **Project mapping (optional)** — `~/Library/Application Support/aicosts/projects.toml`:

   ```toml
   [[project]]
   label = "openclaw-agent"
   anthropic_workspace_ids = ["wrkspc_..."]
   openai_project_ids = ["proj_..."]

   [[project]]
   label = "voice-calls"
   openai_project_ids = ["proj_voice"]
   ```

## Use

```sh
# Pull last 30 days from all providers
aicosts pull

# Pull a specific provider for a specific window
aicosts pull --provider anthropic --since 2026-04-01

# Reports
aicosts report --period month --by provider
aicosts report --period week --by project
aicosts report --period today --by model

# Daily-briefing one-liner
aicosts status
# -> today: $6.70 (openai $4.20, anthropic $2.50*)
#    (* = estimated from token counts; replaces when finalized)
```

## Data locations

```sh
aicosts paths
```

- `~/Library/Application Support/aicosts/db.sqlite` — main DB
- `~/Library/Application Support/aicosts/raw/{provider}/{date}.jsonl` — replay archive
- `~/Library/Application Support/aicosts/projects.toml` — display mapping

## Design notes

See [the plan in dot-openclaw#13](https://github.com/davidrossdegroot/dot-openclaw/issues/13)
for context. Key decisions:

- **SQLite + JSONL** — SQLite for queries, JSONL for raw API responses (replay/debug).
- **Admin keys in Keychain, never `.env`** — admin keys can manage workspaces and
  rotate keys; treat them as more sensitive than regular API keys.
- **Provider-side IDs as primary key** — `workspace_id`, `project_id`, etc. survive
  key rotation. The local `projects.toml` is a display layer only.
- **Estimated vs finalized cost** — Anthropic's `usage_report` is real-time but
  returns token counts; we estimate cost from a static pricing table and mark
  rows `cost_estimated=1`. `cost_report` is authoritative but lags ~24h; when it
  catches up, it overwrites the estimate.

## Develop

```sh
uv run pytest
```
