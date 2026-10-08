# Security Policy

AIMONITOR reads login files of other tools (for example Claude Code's `.credentials.json`) and stores provider API keys, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security tab → Report a vulnerability** ([link](https://github.com/supsuphansoup/AIMONITOR/security/advisories/new)).

Include the version (`GET /api/v1/system/info` or the release name), how to reproduce, and the impact.
Never paste real tokens or API keys; redact them. You will get a first reply within 7 days.

## Scope

In scope, for example:
- a token or API key leaving the PC to anyone other than its issuer, or being written in plain text;
- a web page or another user driving the local API (`127.0.0.1:8000`) in ways the docs say are blocked;
- prompt or response text reaching the database, logs, error responses or CSV export.

What the app reads and sends by design is listed in [docs/privacy.en.md](docs/privacy.en.md).

## Supported versions

Only the latest release gets fixes.

---

보안 취약점은 공개 이슈 대신 **Security 탭 → Report a vulnerability**로 비공개 제보해 주세요. 실제 토큰·키는 가리고 보내 주세요.
