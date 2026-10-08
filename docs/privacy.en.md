# Privacy: what it reads and where it sends

[한국어](privacy.md) · **English**

AIMONITOR reads other tools' logs and login files, so this page lists everything it reads, sends and stores.
If the code behaves differently from this page, that is a bug — please [open an issue](https://github.com/supsuphansoup/AIMONITOR/issues).

## Summary

- From local logs it collects **token counts, model and time only**. Prompt and response text is never stored or sent.
- It sends data to the four kinds of destination below and nowhere else. **No telemetry, analytics or auto-update.**
- Provider API keys are encrypted with Windows DPAPI, readable only by the current user on this PC.
- The local API listens on `127.0.0.1` only.

## Read on this PC

| What | Values used |
| :--- | :--- |
| Claude Code session logs (`~/.claude/projects/**/*.jsonl`, `~/.claude-*`, `CLAUDE_CONFIG_DIR`, folders you add) | Token counts, model, time and response/request IDs of responses. Message text is not used. |
| Claude `.credentials.json` | The login token (`accessToken`) — used only for the Anthropic usage lookup below; never stored or logged. `refreshToken` is not read. The subscription type (Pro/Max) is read too. |
| Claude `.claude.json` | Account UUID — stored only as a 12-character hash |
| Codex session logs (`~/.codex/sessions`, `~/.codex-*`, `CODEX_HOME`) | Token counts, model, time, limit snapshots (`rate_limits`) |
| Codex `auth.json` | Account ID — stored only as a hash. The plan type is read too. Access tokens and API keys are not read. |
| Copilot session logs (`~/.copilot/session-state`) | Per-model tokens and request counts from session-end events |
| Copilot `auth.db` | Only **counts** login tokens; their values are not read |
| Antigravity logs (`~/.gemini/antigravity/brain/…/transcript.jsonl`) | Tokens are estimated from text **length**. The text is only measured in memory, never stored or sent. |
| VS Code and Antigravity settings DBs (`state.vscdb`) | Subscription tier and whether Copilot is active, opened **read-only** |
| Cursor, Windsurf, Ollama folders | Whether they are installed |
| Environment variables | `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, provider keys (`OPENAI_API_KEY` etc., see [keys spec](spec/keys.md), Korean), `AIMONITOR_API_KEY` |

## Sent off this PC (complete list)

| Destination | When | What is sent |
| :--- | :--- | :--- |
| `api.anthropic.com/api/oauth/usage` | When a Claude card or the widget refreshes (60 s cache per account; waits 2 min after a 429) | That account's Claude login token, with no body or parameters. This is an **unofficial endpoint** and may change without notice. |
| Exchange-rate APIs — `open.er-api.com`, then `exchangerate-api.com`, then jsDelivr if those fail | At start, then every 30 min | Nothing (for the KRW display; no key; the host sees your IP) |
| Providers of keys you registered (OpenAI, Anthropic, OpenRouter, DeepSeek, Gemini, Groq) | Spend/balance lookups when a key is registered or its environment variable is set (15 min cache), and the Verify button | That provider's own key — only to its issuer |
| Webhook URLs in alert rules (Slack, Discord, …) | When a rule fires | Rule name, metric, operator, threshold, actual value, time |

Requests to the local API server on `127.0.0.1` and to Ollama (`127.0.0.1:11434`) never leave this PC.

## Stored

| Where | What |
| :--- | :--- |
| Database (`aimonitor.db` — in `~/.aimonitor/` for the exe, in the repository folder when run from source) | Per record: token counts, model, time, cost, account hash, tags. Alert rules (**webhook URLs in plain text**) and their history |
| `~/.aimonitor/api_keys.json` | Provider keys, DPAPI-encrypted |
| `~/.aimonitor/desktop_config.json` | UI settings, server address, the server auth key (DPAPI-encrypted) |
| `~/.aimonitor/accounts.json`, `account_timeline.json` | Account hashes, labels, config folder paths, times seen |
| `~/.aimonitor/quota/`, `claude_official_quota.json` | Limit lookup results (percent, reset time, subscription type). No tokens. |
| `~/.aimonitor/*offsets*.json`, `ai_seen_claude.json` | Read positions per log file and de-duplication keys. No text. |
| `~/.aimonitor/logs/` (exe only) | Run logs. Request bodies are never logged. |
| Registry `HKCU\…\Run\AIMONITOR` | Only when autostart is on |

## Limits

- **When you log through the SDK yourself**, the exception message (`error_message`, up to 2,000 characters) is stored, so a prompt fragment inside an exception message would be kept. Metadata keys such as `prompt` or `content` are redacted, but redaction is by key name, so text stored under another name is not caught.
- By default the local API has no key, so **other programs on this PC can read it**. Attempts by web pages to drive it through your browser (cross-origin writes, DNS rebinding) are blocked. On a shared PC, set `API_KEY` and `REQUIRE_AUTH_FOR_READS=true`.

## Removing everything

Turn off autostart in the app's settings, then delete the app folder and `%USERPROFILE%\.aimonitor`; that removes all data AIMONITOR created.
If you ran from source, also delete `aimonitor.db*` in the repository folder. Other tools' logs and login files are never modified.
