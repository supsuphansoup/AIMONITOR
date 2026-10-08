<h1 align="center">AIMONITOR</h1>

<p align="center">
  <b>Remaining limits and usage for Claude Code, Codex and Copilot — at a glance, in the Windows tray.</b><br>
  Split per account. Prompts are never stored.
</p>

<p align="center">
  <a href="https://github.com/supsuphansoup/AIMONITOR/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/supsuphansoup/AIMONITOR/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/supsuphansoup/AIMONITOR/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/supsuphansoup/AIMONITOR"></a>
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center"><a href="README.md">한국어</a> · <b>English</b></p>

<!-- Screenshots: docs/images/widget.png (always-on-top widget), docs/images/main.png (main window) — add before going public -->

> The app UI is in Korean for now. Numbers, model names and limits read the same in any language; English UI contributions are welcome.

## Who it's for

- You keep typing `/usage` to see how much of Claude Code's **5-hour and weekly limits** is left.
- You juggle **personal and work Claude accounts**, or Codex and Copilot too, and lose track of what you spent where.
- You want to see your API spend **without sending prompts to a third-party SaaS**.

## Features

- **Always-on-top widget** — remaining limit (%) and reset time per tool, plus today's usage and cost, in a corner of the screen. Hide or show it from the tray.
- **Per-account split** — reads the default config folders, `CLAUDE_CONFIG_DIR` / `CODEX_HOME`, `~/.claude-*` / `~/.codex-*` and folders you add, and shows each account separately.
- **Honest numbers** — when there is no official value it shows "unavailable" or "cannot calculate" instead of inventing one, and estimates are labelled as estimates.
- **Main window** — logs and charts (by account and model), provider API keys with spend and balance, alert rules (Slack/Discord), CSV export.
- **Your own app's LLM calls too** — three lines of Python SDK or the HTTP API put the latency, tokens and cost of OpenAI, Anthropic, Gemini and Ollama calls on the same screen.
- **Start with Windows** — a toggle in settings.

### Supported tools

| Tool | Usage (tokens, cost) | Remaining limit | Source |
| :--- | :--- | :--- | :--- |
| Claude Code | ✅ | ✅ 5-hour and weekly | Local session logs + Anthropic usage lookup |
| OpenAI Codex | ✅ | ✅ limit windows from session logs | Local session logs |
| GitHub Copilot | ✅ | ⚠️ premium requests this month ÷ the limit you enter | Local session logs (GitHub does not expose the remainder) |
| Google Antigravity | ⚠️ estimated (from text length) | — | Local logs |
| Cursor, Windsurf, Ollama | detected only | — | Install folders |

## Install

### exe (recommended, no Python needed)

1. Download `AIMONITOR-<version>-windows-x64.zip` from [Releases](https://github.com/supsuphansoup/AIMONITOR/releases/latest) and unzip it anywhere.
2. Run `AIMONITOR\AIMONITOR.exe`.

- The exe is not code-signed, so **SmartScreen may warn** on first launch: choose `More info → Run anyway`. Check the download against the attached `.sha256` (`Get-FileHash <zip> -Algorithm SHA256`).
- Settings, the database and logs live in `%USERPROFILE%\.aimonitor`.
- To uninstall, turn off autostart in settings, then delete the app folder and `%USERPROFILE%\.aimonitor`.

### From source

Requires Python 3.10+ with Tkinter.

```powershell
git clone https://github.com/supsuphansoup/AIMONITOR.git
cd AIMONITOR
pip install -r backend/requirements.txt -r desktop/requirements.txt
python run_desktop.py          # without a console: pythonw run_desktop.pyw
```

The app starts a local API server on `127.0.0.1:8000`. When run from source the database is `aimonitor.db` in the repository folder. To build the exe yourself: `pip install -r build-windows/requirements.txt`, then `pyinstaller build-windows/aimonitor.spec --noconfirm`.

## 🔒 Privacy: what it reads and where it sends

AIMONITOR reads other tools' logs and login files, so here is everything it reads and sends. Details are in the [desktop spec](docs/spec/desktop.md) (Korean).

**Read on this PC**

| What | Values used |
| :--- | :--- |
| Claude Code session logs (`~/.claude/projects/**/*.jsonl` etc.) | Token counts, model, time and request ID of responses. **Message text is not used.** |
| Claude `.credentials.json` | The login token — used only for the Anthropic usage lookup below. Never stored or logged. |
| Claude `.claude.json`, Codex `auth.json` | Account ID — stored only as a 12-character hash. Codex tokens and API keys are not read. |
| Codex session logs (`~/.codex/sessions`) | Token counts, model, time, limit snapshots |
| Copilot session logs (`~/.copilot/session-state`) | Tokens and request counts from session-end events. Login tokens are only **counted**. |
| Antigravity logs | Tokens are estimated from text **length**. The text stays in memory and is never stored or sent. |
| Other apps' databases (VS Code, Copilot, Antigravity) | Plan and connection status only, opened **read-only** |

**Sent off this PC** — this is the complete list. No telemetry, analytics or auto-update.

| Destination | When | What is sent |
| :--- | :--- | :--- |
| `api.anthropic.com/api/oauth/usage` | When a Claude card or the widget refreshes (60 s cache per account) | That account's Claude login token. This is an **unofficial endpoint** and may change without notice. |
| Exchange-rate APIs (`open.er-api.com` → `exchangerate-api.com` → jsDelivr) | At start, then every 30 min | Nothing (for the KRW display; no key) |
| Providers of keys you registered (OpenAI, Anthropic, OpenRouter, DeepSeek, …) | When a key is registered or `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `*_ADMIN_KEY` env vars exist (15 min cache), and on Verify | That provider's own key (only to its issuer) |
| Webhook URLs in alert rules | When a rule fires | Rule name, metric, value |

**Stored** — token counts, model, time, cost, account hashes and folder paths in `~/.aimonitor` and the database. Provider API keys are **encrypted with Windows DPAPI**, readable only by the current Windows user. Webhook URLs are stored in plain text in the database.

- Records collected from local logs contain no prompt or response text. When you log through the SDK yourself, the exception message (`error_message`, up to 2,000 characters) is stored, so a prompt fragment inside an exception message would be kept.
- The local API listens on `127.0.0.1` only. By default there is no API key, so **other programs on this PC can read it**. Attempts by web pages to drive the API through your browser (cross-origin writes, DNS rebinding) are blocked.

## 🐍 Python SDK

```bash
pip install -e sdk/python
```

```python
from aimonitor import AIMonitor

monitor = AIMonitor(api_url="http://localhost:8000/api/v1", api_key="...")

# 1) Decorator — records latency and token counts automatically
@monitor.observe(provider="openai", model_name="gpt-4o", tags=["production"])
def call_gpt(prompt: str):
    return openai_client.chat.completions.create(...)

# 2) Explicit record — cache tokens passed separately
with AIMonitor() as mon:
    mon.track(
        provider="anthropic",
        model_name="claude-sonnet-4-5",
        prompt_tokens=1200,
        completion_tokens=600,
        cache_read_tokens=8000,
        cache_write_tokens=1500,
        latency_ms=450.0,
    )
```

Sync and async are both supported, and token counts are extracted from provider responses automatically. To keep network waits off the request path, use `AIMonitor(background=True)` — a background worker sends in batches. See [sdk/python/README.md](sdk/python/README.md).

## 🖥️ API server only (Docker)

To collect on a server without the desktop app:

```bash
cp .env.example .env
python -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))" >> .env
docker compose up --build -d
```

- API base `http://localhost:8000/api/v1`, Swagger `http://localhost:8000/api/v1/docs`, health check `/health`
- The default port binding is `127.0.0.1:8000`. To expose it, **set `API_KEY` first**, then change the port in `docker-compose.yml`. There is no web UI.

## 🔐 Security settings

| Item | Setting | Default | Notes |
| :--- | :--- | :--- | :--- |
| API key | `API_KEY` | *(none)* | Unset: every endpoint is open, and only requests addressed to `localhost` are answered |
| Protect reads | `REQUIRE_AUTH_FOR_READS` | `false` | `true` requires the server key for desktop and SDK reads too |
| Allowed origins | `CORS_ORIGINS` | localhost | Writes from other browser origins are refused. `*` disables credentials |
| Allowed hosts | `ALLOWED_HOSTS` | *(none)* | Only for serving non-`localhost` names without an API key (trusted networks) |
| Retention | `RETENTION_DAYS` | `0` (forever) | When set, old records are deleted periodically |

Clients authenticate with `X-API-Key: <key>` or `Authorization: Bearer <key>`. All settings are in [.env.example](.env.example).

## 🔌 API summary

| Method | Path | Description | Auth |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/traces` | Ingest one trace | write |
| `POST` | `/api/v1/traces/batch` | Ingest a batch (up to `MAX_BATCH_SIZE`) | write |
| `GET` | `/api/v1/traces` | List (filters, period, pagination) | read |
| `GET` | `/api/v1/traces/{trace_id}` | Get one | read |
| `GET` | `/api/v1/traces/export.csv` | Streaming CSV export | read |
| `GET` | `/api/v1/metrics/overview` | KPI summary (`hours_back`) | read |
| `GET` | `/api/v1/metrics/timeseries` | Time buckets | read |
| `GET` | `/api/v1/metrics/breakdown` | By model and provider | read |
| `GET` | `/api/v1/metrics/active_contexts` | Desktop per-tool usage and latest context | read |
| `GET` | `/api/v1/metrics/connected_ais` | Connected AIs, official limits, local usage | read |
| `GET` | `/api/v1/keys` | Provider key status, masked values | read |
| `POST` | `/api/v1/keys` | Register a provider key (several per provider, by label) | write |
| `PATCH` | `/api/v1/keys/{provider}/{key_id}` | Rename a key | write |
| `DELETE` | `/api/v1/keys/{provider}/{key_id}` | Delete one key | write |
| `DELETE` | `/api/v1/keys/{provider}` | Delete all of a provider's keys | write |
| `POST` | `/api/v1/keys/{provider}/verify` | Verify a provider key | write |
| `GET` | `/api/v1/keys/spend` | Official spend and balance (15 min cache) | read |
| `GET` | `/api/v1/alerts/metrics` | Available metrics and operators | read |
| `GET` | `/api/v1/alerts/rules` | List rules | read |
| `POST` | `/api/v1/alerts/rules` | Create a rule | write |
| `PATCH` | `/api/v1/alerts/rules/{id}` | Partly update a rule (pause etc.) | write |
| `DELETE` | `/api/v1/alerts/rules/{id}` | Delete a rule and its history | write |
| `POST` | `/api/v1/alerts/evaluate` | Evaluate now (`ignore_cooldown`) | write |
| `GET` | `/api/v1/alerts/history` | Firing history | read |
| `GET` | `/api/v1/system/info` | Runtime settings and scheduler status | read |
| `GET` | `/api/v1/system/pricing` | Rate card in use | read |
| `POST` | `/api/v1/system/purge?days=N` | Delete data older than N days | write |
| `GET` | `/health` | Liveness (no DB access) | public |
| `GET` | `/api/v1/system/ready` | Readiness (checks the DB) | public |

All timestamps are returned as **ISO-8601 with a UTC offset**. `GET /traces` also returns the total count in the `X-Total-Count` header.

## 💰 Cost rules

The `cost_source` field states where a cost came from.

| Value | Meaning | Cost |
| :--- | :--- | :--- |
| `pricing_table` | Calculated from the built-in rate card | The calculated amount |
| `custom` | The caller passed `cost_usd` | The passed amount |
| `local_free` | Local inference (Ollama etc.), no token billing | `$0.00000` |
| `unknown` | **No rate known** | **"cannot calculate"** |

Prompt-cache writes and reads have different rates, and the discount differs by model (for example Claude Opus 5.5 reads at 0.05×, gpt-4o at 0.5×, gpt-5 at 0.1×). AIMONITOR never adds them together; each is priced with the model's official rate. The rates in use, their check date (`as_of`) and sources are at `GET /api/v1/system/pricing`.

Costs are **estimates at standard rates**. Batch discounts, long-prompt tiers, the 1-hour cache and cloud-partner pricing are not applied ([docs/spec/cost.md](docs/spec/cost.md)).

## 🏗️ Layout

```
AIMONITOR/
├── desktop/           # Windows desktop app: main window, tray, widget, log watchers, accounts, limits
├── backend/app/       # FastAPI: core (settings, auth, sanitizing, UTC), db, models, routers, services (cost, aggregation, alerts)
├── backend/tests/     # Backend and desktop tests
├── sdk/python/        # Python client (aimonitor-sdk)
├── build-windows/     # PyInstaller exe build (spec, entry point)
├── docs/              # intent.md (why) → spec/ (per-feature rules) → plan.md (current work), in Korean
├── run_desktop.py     # Source launcher (without a console: run_desktop.pyw)
├── AGENTS.md          # Shared instructions for AI coding agents (CLAUDE.md, GEMINI.md point here)
└── CHANGELOG.md
```

## 🧪 Development

```bash
pip install -r backend/requirements.txt -r desktop/requirements.txt
python -m pytest -q
ruff check .
python -m uvicorn backend.app.main:app --port 8000 --reload   # API dev server
python scripts/simulate.py --batches 20 --spread-hours 6      # demo traffic (use a separate DB)
```

Changes are in [CHANGELOG.md](CHANGELOG.md) (Korean).

## 📄 License

[MIT](LICENSE)
