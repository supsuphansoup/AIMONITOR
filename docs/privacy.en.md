# Privacy: what it reads and where it sends

[한국어](privacy.md) · **English**

AIMONITOR reads other tools' logs and login files, so this page lists everything it reads, sends and stores.
If the code behaves differently from this page, that is a bug — please [open an issue](https://github.com/supsuphansoup/AIMONITOR/issues).

## Summary

- From local logs it collects **only token and request counts, model, time, session IDs, and account and de-duplication hashes**. Prompt and response text is never stored or sent.
- The desktop app sends data to the four kinds of destination below and nowhere else. **No telemetry, analytics or auto-update.**
- Provider API keys are encrypted with Windows DPAPI, readable only by the current user on this PC.
- The local API listens on `127.0.0.1` only.

## Read on this PC

| What | Values used |
| :--- | :--- |
| Claude Code session logs (`~/.claude/projects/**/*.jsonl`, `~/.claude-*`, `CLAUDE_CONFIG_DIR`, folders you add) | Token counts, model, time and response/request IDs of responses. Message text is not used. |
| Claude `.credentials.json` in each account's config folder | The login token (`accessToken`) — used only for the Anthropic usage lookup below; never stored or logged. `refreshToken` is not read. The subscription type (Pro/Max) and whether OAuth or `anthropicApiKey` is configured distinguish subscription usage from API billing. The API key is checked only for a nonempty value, then discarded; never stored, logged or sent. |
| Claude `.claude.json` | Account UUID — stored only as a 12-character hash |
| Codex session logs (`~/.codex/sessions`, `~/.codex-*`, `CODEX_HOME`) | Token counts, model, time, limit snapshots (`rate_limits`) |
| Codex `auth.json` in each account's config folder | Account ID — stored only as a hash. Account and plan claims in `id_token` are read locally. The JSON is loaded into memory to check whether ChatGPT login-token fields or `OPENAI_API_KEY` have nonempty values, to distinguish subscription usage from API billing. API-key and access/refresh-token values are discarded immediately after this presence check; never stored, logged or sent. |
| Copilot session logs (`~/.copilot/session-state`) | Per-model tokens and request counts, premium request usage (`requests.cost`) and session API time (`totalApiDurationMs`) from session-end events |
| Copilot `auth.db` | Only **counts** login tokens; their values are not read |
| Antigravity logs (`~/.gemini/antigravity/brain/…/transcript.jsonl`) | Tokens are estimated from text **length**: output from `content` and `thinking`, input from the first of `input_text`, `prompt` or `input` that is present (otherwise input is not estimated). Model, provider, time and session ID are used too. The text is only measured in memory, never stored or sent. |
| VS Code and Antigravity settings DBs (`state.vscdb`) | Subscription tier, whether Copilot is active, and model names reported by Antigravity, for the connected-AI display. Opened **read-only**. |
| Local Ollama API (`127.0.0.1:11434/api/tags`) | Installed model names and count, and whether it responds, for the connected-AI display |
| Cursor, Windsurf, Ollama folders | Whether they are installed |
| Environment variables | `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, provider keys (`OPENAI_API_KEY` etc., see [keys spec](spec/keys.md), Korean), `AIMONITOR_API_KEY` |
| Profile and AppData path environment variables | The home folder comes from `USERPROFILE` on Windows (falling back to `HOMEDRIVE` and `HOMEPATH`) or `HOME` on other operating systems. `APPDATA` and `LOCALAPPDATA` locate tool settings databases. The home folder is the base for tool logs and `~/.aimonitor/`. |
| Server-setting environment variables and `.env` in the current working folder (`~/.aimonitor/.env` for the exe, `.env` in the repository folder for the source desktop app) | `API_KEY`, `DATABASE_URL`, Host, CORS, retention and size limits, etc. ([settings list](../.env.example)). Values in `.env` are plain text, separate from the DPAPI-encrypted server auth key stored in `desktop_config.json`. |

## Sent off this PC (complete list)

| Destination | When | What is sent |
| :--- | :--- | :--- |
| `api.anthropic.com/api/oauth/usage` | When a Claude card or the widget refreshes (60 s cache per account; waits 2 min after a 429) | That account's Claude login token, with no body or parameters. The User-Agent is `AIMONITOR/<version>`. This is an **unofficial endpoint** and may change without notice. |
| Exchange-rate APIs — `open.er-api.com`, then `exchangerate-api.com`, then jsDelivr if those fail | At start, then every 30 min | Nothing (for the KRW display; no key; the host sees your IP) |
| Providers of keys you registered (OpenAI, Anthropic, OpenRouter, DeepSeek, Gemini, Groq) | Spend/balance lookups when a key is registered or its environment variable is set (15 min cache, refreshed when the UTC month changes), the Verify button, and forced refreshes (at most once per 60 s) | That provider's key (in an auth header) and the lookup period — only to the issuer's official API. Prompts and responses are never sent ([keys spec](spec/keys.md), Korean) |
| Webhook URLs in alert rules (Slack, Discord, …) | When a rule fires, including manual evaluations. When a rule is saved and right before sending, the host name is resolved through DNS to check the destination. | Rule name, metric, operator, threshold, actual value and window. Slack payloads also carry the rule ID and the UTC trigger time. |

Webhook destinations must be public addresses by default. Private, loopback, link-local, reserved and multicast addresses and URLs containing
`user:pass@` are rejected; redirects are not followed and proxies from environment variables are not used. Response bodies are never read or stored.
For an intranet webhook, allow it explicitly with the server setting `ALERT_ALLOW_PRIVATE_WEBHOOKS=true`.

Requests to the local API server on `127.0.0.1` and to Ollama (`127.0.0.1:11434`) never leave this PC.
Desktop instances use `127.0.0.1:47821` to prevent duplicate launches, exchanging only fixed control messages (`AIMONITOR:show`, `AIMONITOR:ping`, `AIMONITOR:ack:v1`). No usage records or auth keys are sent.
If you change the server address (`backend_url`) in the settings file (`desktop_config.json`) to another server, usage reads, ingestion requests and the server auth key go to that server, and no local server is started.

When you log your own app's calls with the **Python SDK**, records go to the ingestion server set in `api_url` (localhost on this PC by default).
Automatic extraction reads only the token counts of a response. The SDK sends provider, model, token counts, latency and status plus any identifiers,
tags, redacted metadata and `error_message` you pass; if you set `api_key`, it is sent in the `X-API-Key` header. The SDK never reads tool logs or login files.

## Stored

| Where | What |
| :--- | :--- |
| Database (default: `aimonitor.db` — in `~/.aimonitor/` for the exe, in the repository folder when run from source; actual storage is set by the server's `DATABASE_URL`) | Per record: provider, model, token counts, time, latency, cost, status, account hash, trace/session IDs, tags, redacted metadata, error message, and a collector's de-duplication key (`dedupe_key`, a hash). Alert rules (**webhook URLs in plain text**) and their trigger and delivery history |
| `~/.aimonitor/api_keys.json` | Only the key secret is encrypted with DPAPI (`sealed`). Provider, key ID, label, verification status, last verification time and balance description are plain text. |
| `~/.aimonitor/api_keys.json.lock` | A lock file that stops the desktop app and the server from editing the key file at the same time. No secrets. |
| `~/.aimonitor/ai_watcher.lock` | A lock file that stops multiple collectors from ingesting the same logs at the same time. No secrets. |
| `~/.aimonitor/desktop_config.json` | UI settings, server address, the server auth key (DPAPI-encrypted), and the USD/KRW rate with its source, reference date and last successful fetch time |
| `~/.aimonitor/accounts.json`, `account_timeline.json` | Account hashes, labels, config folder paths, times seen (the timeline keeps the latest 5,000). `accounts.json` also keeps each account's authentication mode (`subscription/api/unknown`), when the current mode was first observed and when it last changed. No key or token values. |
| `~/.aimonitor/quota/`, `claude_official_quota.json` | Limit lookup results (percent, reset time, subscription type). No tokens. |
| `~/.aimonitor/*offsets*.json`, `folder_offsets/`, `ai_cursors.json`, `ai_seen_claude.json` | Read positions per log file and de-duplication keys, plus file ID, size, times and a hash of the file's beginning to notice replaced files. No text. Entries for files missing for 30 days are removed. |
| `~/.aimonitor/logs/` (exe only) | Run logs per role (1 MiB per file, three backups). Corrupt log lines are counted, never copied. Request logs keep only the request ID, method, path, status and duration — no query strings or request bodies. Error logs keep only the exception type — no exception text or SQL parameters. |
| Registry `HKCU\…\Run\AIMONITOR` | Only when autostart is on |

The default database in `docker-compose.yml` is `/app/data/aimonitor.db` in the `aimonitor-data` data volume.

API responses mask webhook URLs as `scheme://host/…`, but the original stays in the database in plain text. Treat the database file and its backups as secrets.

Automatic retention cleanup is off by default (`RETENTION_DAYS=0`, keep everything). With a period set, records and alert history older than it
are deleted every hour using UTC; the manual purge API uses the same cutoff. Deleted records cannot be recovered.

## Limits

- **When you log through the SDK yourself**, automatic error records carry only the exception type (class name). If you pass `error_message` yourself, up to 2,000 characters of it are stored, so keep prompts, account details and secrets out of it. Metadata keys such as `prompt` or `content` are replaced with `[redacted]`, including inside nested dicts and lists, but redaction is by key name, so text stored under another name is not caught. These protections apply to newly ingested records; older database contents are not re-sanitized.
- By default the local API has no key, so **other programs on this PC can read it**. The key list shows a masked value with only the first and last four characters (keys of eight characters or fewer are fully masked), plus labels, verification status and spend. Attempts by web pages to drive it through your browser (cross-origin writes, DNS rebinding) are blocked whether or not an API key is set. On a shared PC, set `API_KEY` and `REQUIRE_AUTH_FOR_READS=true`; before exposing it to a network, also limit `ALLOWED_HOSTS` and `CORS_ORIGINS` to the values you actually use.
- A DNS rebinding race remains between checking a webhook destination and connecting to it. If you expose the server, also restrict its outbound network access.

## Removing everything

For a default local installation with no database-location override, data is in the folders below. Turn off autostart in the app's settings,
then stop the app and server and delete the app folder and `%USERPROFILE%\.aimonitor`. If you ran from source, also delete `aimonitor.db`
and its SQLite sidecar files (`aimonitor.db-wal`, `aimonitor.db-shm`, `aimonitor.db-journal`) in the repository folder.

If you changed the server's `DATABASE_URL`, separately delete the actual database it points to. For SQLite, delete that file and sidecar files
with the same name followed by `-wal`, `-shm` or `-journal`. Deleting the default folders does not delete a database elsewhere.

For Docker, separately delete the `aimonitor-data` data volume in `docker-compose.yml`. Deleting only the container or image leaves the database
behind. The actual volume name may have a Compose project-name prefix, so check the volume used by that deployment.

Data on a remote server selected by the server address (`backend_url`) and database backups must be deleted separately; deleting folders on this PC does not remove them.
Other tools' logs and login files are never modified.
