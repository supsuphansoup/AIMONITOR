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
- **Per-account split** — reads every Claude and Codex config folder and shows each account separately.
- **Honest numbers** — no official value means "unavailable" or "cannot calculate", never a made-up number. Estimates are labelled as estimates.
- **Main window** — logs and charts (by account and model), provider API keys with spend and balance, Slack/Discord alerts, CSV export.
- **Your own app's LLM calls too** — log OpenAI, Anthropic, Gemini and Ollama calls through the Python SDK or the HTTP API onto the same screen.
- **Start with Windows**.

| Tool | Usage (tokens, cost) | Remaining limit |
| :--- | :--- | :--- |
| Claude Code | ✅ | ✅ 5-hour and weekly |
| OpenAI Codex | ✅ | ✅ limit windows from session logs |
| GitHub Copilot | ✅ | ⚠️ premium requests this month ÷ the limit you enter (GitHub does not expose the remainder) |
| Google Antigravity | ⚠️ estimated | — |
| Cursor, Windsurf, Ollama | detected only | — |

## Install

**exe (recommended, no Python needed)**

1. Download `AIMONITOR-<version>-windows-x64.zip` from [Releases](https://github.com/supsuphansoup/AIMONITOR/releases/latest) and unzip it anywhere.
2. Run `AIMONITOR\AIMONITOR.exe`.

The exe is not code-signed, so SmartScreen may warn on first launch: choose `More info → Run anyway`.
Check the download against the attached `.sha256` (`Get-FileHash <zip> -Algorithm SHA256`). Data lives in `%USERPROFILE%\.aimonitor`.

**From source** (Python 3.10+ with Tkinter)

```powershell
git clone https://github.com/supsuphansoup/AIMONITOR.git
cd AIMONITOR
pip install -r backend/requirements.txt -r desktop/requirements.txt
pythonw run_desktop.pyw
```

## 🔒 Privacy

AIMONITOR reads other tools' logs and login files, so everything it reads and sends is listed → **[full list](docs/privacy.en.md)**

- From local logs it collects token counts, model and time only. Prompt and response text is never stored or sent.
- It sends to: the Claude usage lookup (`api.anthropic.com`, an unofficial endpoint), exchange-rate APIs, providers of keys you registered, and webhooks you configure. No telemetry.
- Provider API keys are encrypted with Windows DPAPI. The local API listens on `127.0.0.1` only.

## Log your own app's LLM calls

```python
from aimonitor import AIMonitor  # pip install -e sdk/python

monitor = AIMonitor(api_url="http://localhost:8000/api/v1")

@monitor.observe(provider="openai", model_name="gpt-4o")
def call_gpt(prompt: str):
    return openai_client.chat.completions.create(...)
```

Latency, tokens and cost show up in the app. Async, batching and cache tokens are in the [SDK docs](sdk/python/README.md); running the API server alone with Docker is in the [operations spec](docs/spec/security-ops.md) (Korean); the full API is at `/api/v1/docs` on a running server.

## Docs

| Doc | What |
| :--- | :--- |
| [docs/privacy.en.md](docs/privacy.en.md) | Every file read, every host contacted, where data is stored |
| [docs/spec/](docs/spec/README.md) | Per-feature specs (Korean) — desktop, account collection, API keys, ingestion and metrics API, cost, alerts, security and operations |
| [sdk/python/README.md](sdk/python/README.md) | Python SDK |
| [CHANGELOG.md](CHANGELOG.md) | Changes per version (Korean) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, tests, contribution rules |
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability |

## License

[MIT](LICENSE)
