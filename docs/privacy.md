# 개인정보: 무엇을 읽고, 어디로 보내나

**한국어** · [English](privacy.en.md)

AIMONITOR는 다른 도구의 로그와 로그인 파일을 읽는 앱입니다. 그래서 읽는 것, 보내는 것, 저장하는 것을 모두 적어 둡니다.
코드가 이 문서와 다르게 동작하면 버그입니다 — [이슈](https://github.com/supsuphansoup/AIMONITOR/issues)로 알려 주세요.

## 요약

- 로컬 로그에서는 **토큰 수·모델·시각만** 모읍니다. 프롬프트와 응답 본문은 저장하거나 보내지 않습니다.
- 밖으로 보내는 곳은 아래 표의 네 종류뿐입니다. **텔레메트리·분석·자동 업데이트는 없습니다.**
- 공급자 API 키는 Windows DPAPI로 암호화해 이 PC의 현재 사용자만 풀 수 있습니다.
- 로컬 API는 `127.0.0.1`에서만 열립니다.

## 이 PC에서 읽는 것

| 대상 | 쓰는 값 |
| :--- | :--- |
| Claude Code 세션 로그 (`~/.claude/projects/**/*.jsonl`, `~/.claude-*`, `CLAUDE_CONFIG_DIR`, 직접 추가한 폴더) | 응답의 토큰 수·모델·시각·응답/요청 ID. 본문은 쓰지 않습니다. |
| Claude `.credentials.json` | 로그인 토큰(`accessToken`) — 아래 Anthropic 사용량 조회에만 씁니다. 저장·기록하지 않습니다. `refreshToken`은 읽지 않습니다. 구독 종류(Pro/Max)도 읽습니다. |
| Claude `.claude.json` | 계정 UUID — 12자 해시로만 저장합니다. |
| Codex 세션 로그 (`~/.codex/sessions`, `~/.codex-*`, `CODEX_HOME`) | 토큰 수·모델·시각, 한도 스냅샷(`rate_limits`) |
| Codex `auth.json` | 계정 ID — 해시로만 저장합니다. 플랜 종류도 읽습니다. 액세스 토큰·API 키는 읽지 않습니다. |
| Copilot 세션 로그 (`~/.copilot/session-state`) | 세션 종료 이벤트의 모델별 토큰·요청 수 |
| Copilot `auth.db` | 로그인 토큰 **개수만** 셉니다. 값은 읽지 않습니다. |
| Antigravity 로그 (`~/.gemini/antigravity/brain/…/transcript.jsonl`) | 본문 **길이**로 토큰을 추정합니다. 본문은 메모리에서 길이만 재고 저장·전송하지 않습니다. |
| VS Code·Antigravity 설정 DB (`state.vscdb`) | 구독 등급·Copilot 활성 여부만, **읽기 전용**으로 엽니다. |
| Cursor·Windsurf·Ollama 폴더 | 설치 여부만 |
| 환경 변수 | `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, 공급자 키(`OPENAI_API_KEY` 등, [keys.md](spec/keys.md)), `AIMONITOR_API_KEY` |

## 밖으로 보내는 곳 (전부)

| 목적지 | 언제 | 보내는 것 |
| :--- | :--- | :--- |
| `api.anthropic.com/api/oauth/usage` | Claude 카드·위젯이 갱신될 때 (계정별 60초 캐시, 요청 제한(429)을 받으면 2분 대기) | 그 계정의 Claude 로그인 토큰. 본문·파라미터는 없고, User-Agent는 `AIMONITOR/<버전>`입니다. **비공식 엔드포인트**라 예고 없이 바뀔 수 있습니다. |
| 환율 API — `open.er-api.com`, 실패하면 `exchangerate-api.com`, 그다음 jsDelivr | 시작 시, 이후 30분마다 | 없음 (원화 표시용, 키 없음. 상대 서버에는 IP가 보입니다) |
| 등록한 키의 공급자 (OpenAI·Anthropic·OpenRouter·DeepSeek·Gemini·Groq) | 키를 등록했거나 해당 환경 변수가 있을 때 청구액·잔액 조회 (15분 캐시), 그리고 `검증` 버튼 | 그 공급자의 키 — 발급한 곳에만 보냅니다 ([keys.md](spec/keys.md)) |
| 알림 규칙에 넣은 Webhook URL (Slack·Discord 등) | 규칙이 발동할 때 | 규칙 이름·지표·연산자·기준값·실제값·시각 |

`127.0.0.1`의 로컬 API 서버와 Ollama(`127.0.0.1:11434`)에 보내는 요청은 이 PC 안에서만 오갑니다.

## 저장하는 것

| 위치 | 내용 |
| :--- | :--- |
| DB (`aimonitor.db` — exe는 `~/.aimonitor/`, 소스 실행은 저장소 폴더) | 기록마다 토큰 수·모델·시각·비용·계정 해시·태그. 알림 규칙(**Webhook URL은 평문**)과 발동 이력 |
| `~/.aimonitor/api_keys.json` | 공급자 키 — DPAPI 암호화 |
| `~/.aimonitor/desktop_config.json` | 화면 설정, 서버 주소, 서버 인증 키(DPAPI 암호화) |
| `~/.aimonitor/accounts.json`, `account_timeline.json` | 계정 해시·라벨·설정 폴더 경로·관측 시각 |
| `~/.aimonitor/quota/`, `claude_official_quota.json` | 한도 조회 결과(퍼센트·리셋 시각·구독 종류). 토큰은 없습니다. |
| `~/.aimonitor/*offsets*.json`, `ai_seen_claude.json` | 로그 파일별 읽은 위치와 중복 제거 키. 본문은 없습니다. |
| `~/.aimonitor/logs/` (exe만) | 실행 로그. 요청 본문은 남기지 않습니다. |
| 레지스트리 `HKCU\…\Run\AIMONITOR` | 자동 실행을 켰을 때만 |

## 한계

- **SDK로 직접 기록할 때**는 예외 메시지(`error_message`, 최대 2,000자)가 저장됩니다. 예외 문구에 프롬프트 일부가 들어 있다면 남을 수 있습니다. 메타데이터의 `prompt`·`content` 같은 키는 자동으로 지우지만, 키 이름 기반이라 다른 이름으로 넣은 원문은 막지 못합니다.
- 로컬 API는 기본적으로 API 키가 없어 **이 PC의 다른 프로그램은 조회할 수 있습니다.** 웹페이지가 브라우저를 통해 조작하는 시도(교차 출처 쓰기, DNS rebinding)는 차단합니다. 공유 PC라면 `API_KEY`와 `REQUIRE_AUTH_FOR_READS=true`를 설정하세요.

## 지우기

앱 설정에서 자동 실행을 끈 뒤 앱 폴더와 `%USERPROFILE%\.aimonitor`를 삭제하면 AIMONITOR가 만든 데이터가 모두 사라집니다.
소스 실행이었다면 저장소 폴더의 `aimonitor.db*`도 지웁니다. 다른 도구의 로그·로그인 파일은 건드리지 않습니다.
