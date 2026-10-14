# 개인정보: 무엇을 읽고, 어디로 보내나

**한국어** · [English](privacy.en.md)

AIMONITOR는 다른 도구의 로그와 로그인 파일을 읽는 앱입니다. 그래서 읽는 것, 보내는 것, 저장하는 것을 모두 적어 둡니다.
코드가 이 문서와 다르게 동작하면 버그입니다 — [이슈](https://github.com/supsuphansoup/AIMONITOR/issues)로 알려 주세요.

## 요약

- 로컬 로그에서는 **토큰·요청 수, 모델, 시각, 세션 ID와 계정·중복 제거용 해시만** 모읍니다. 프롬프트와 응답 본문은 저장하거나 보내지 않습니다.
- 데스크톱 앱이 밖으로 보내는 곳은 아래 표의 네 종류뿐입니다. **텔레메트리·분석·자동 업데이트는 없습니다.**
- 공급자 API 키는 Windows DPAPI로 암호화해 이 PC의 현재 사용자만 풀 수 있습니다.
- 로컬 API는 `127.0.0.1`에서만 열립니다.

## 이 PC에서 읽는 것

| 대상 | 쓰는 값 |
| :--- | :--- |
| Claude Code 세션 로그 (`~/.claude/projects/**/*.jsonl`, `~/.claude-*`, `CLAUDE_CONFIG_DIR`, 직접 추가한 폴더) | 응답의 토큰 수·모델·시각·응답/요청 ID. 본문은 쓰지 않습니다. |
| Claude `.credentials.json` (계정별 설정 폴더) | 로그인 토큰(`accessToken`) — 아래 Anthropic 사용량 조회에만 씁니다. 저장·기록하지 않습니다. `refreshToken`은 읽지 않습니다. 구독 종류(Pro/Max)와 OAuth·`anthropicApiKey`의 설정 여부로 청구 방식을 구분합니다. API 키는 비어 있지 않은지만 확인하고 즉시 버리며 저장·로그·전송하지 않습니다. |
| Claude `.claude.json` | 계정 UUID — 12자 해시로만 저장합니다. |
| Codex 세션 로그 (`~/.codex/sessions`, `~/.codex-*`, `CODEX_HOME`) | 토큰 수·모델·시각, 한도 스냅샷(`rate_limits`) |
| Codex `auth.json` (계정별 설정 폴더) | 계정 ID — 해시로만 저장합니다. `id_token`의 계정·플랜 claim을 로컬에서 읽습니다. JSON을 메모리에 읽어 ChatGPT 로그인 토큰 필드와 `OPENAI_API_KEY`가 비어 있지 않은지 확인해 청구 방식을 구분합니다. API 키·액세스/갱신 토큰 값은 이 설정 여부 판단 직후 버리며 저장·로그·전송하지 않습니다. |
| Copilot 세션 로그 (`~/.copilot/session-state`) | 세션 종료 이벤트의 모델별 토큰·요청 수, 프리미엄 요청 사용량(`requests.cost`), 세션 API 시간(`totalApiDurationMs`) |
| Copilot `auth.db` | 로그인 토큰 **개수만** 셉니다. 값은 읽지 않습니다. |
| Antigravity 로그 (`~/.gemini/antigravity/brain/…/transcript.jsonl`) | 본문 **길이**로 토큰을 추정합니다. 출력은 `content`·`thinking`, 입력은 `input_text`·`prompt`·`input` 중 처음 확인되는 문자열의 길이이며 없으면 입력은 추정하지 않습니다. 모델·공급자·시각·세션 ID도 씁니다. 본문은 메모리에서 길이만 재고 저장·전송하지 않습니다. |
| VS Code·Antigravity 설정 DB (`state.vscdb`) | 구독 등급·Copilot 활성 여부·Antigravity가 보고한 모델명. 연결 AI 표시에 쓰며 **읽기 전용**으로 엽니다. |
| 로컬 Ollama API (`127.0.0.1:11434/api/tags`) | 설치된 모델 이름·개수와 연결 상태. 연결 AI 표시에 씁니다. |
| Cursor·Windsurf·Ollama 폴더 | 설치 여부만 |
| 환경 변수 | `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, 공급자 키(`OPENAI_API_KEY` 등, [keys.md](spec/keys.md)), `AIMONITOR_API_KEY` |
| 프로필·AppData 경로 환경 변수 | 홈 폴더는 Windows의 `USERPROFILE`(없으면 `HOMEDRIVE`·`HOMEPATH`), 다른 OS의 `HOME`으로 찾습니다. `APPDATA`·`LOCALAPPDATA`는 도구 설정 DB의 위치를 찾는 데 씁니다. 홈 폴더는 도구 로그와 `~/.aimonitor/` 위치의 기준입니다. |
| 서버 설정 환경 변수와 현재 작업 폴더의 `.env` (exe는 `~/.aimonitor/.env`, 소스 데스크톱은 저장소 폴더의 `.env`) | `API_KEY`·`DATABASE_URL`·Host·CORS·보관·크기 제한 등 ([설정 목록](../.env.example)). `.env`의 값은 평문이며, `desktop_config.json`에 DPAPI로 암호화해 저장하는 서버 인증 키와 별도 설정입니다. |

## 밖으로 보내는 곳 (전부)

| 목적지 | 언제 | 보내는 것 |
| :--- | :--- | :--- |
| `api.anthropic.com/api/oauth/usage` | Claude 카드·위젯이 갱신될 때 (계정별 60초 캐시, 요청 제한(429)을 받으면 2분 대기) | 그 계정의 Claude 로그인 토큰. 본문·파라미터는 없고, User-Agent는 `AIMONITOR/<버전>`입니다. **비공식 엔드포인트**라 예고 없이 바뀔 수 있습니다. |
| 환율 API — `open.er-api.com`, 실패하면 `exchangerate-api.com`, 그다음 jsDelivr | 시작 시, 이후 30분마다 | 없음 (원화 표시용, 키 없음. 상대 서버에는 IP가 보입니다) |
| 등록한 키의 공급자 (OpenAI·Anthropic·OpenRouter·DeepSeek·Gemini·Groq) | 키를 등록했거나 해당 환경 변수가 있을 때 청구액·잔액 조회 (15분 캐시, UTC 기준 달이 바뀌면 다시 조회), `검증` 버튼, 강제 갱신(최소 60초 간격) | 그 공급자의 키(인증 헤더)와 조회 기간 — 발급한 곳의 공식 API에만 보냅니다. 프롬프트·응답은 보내지 않습니다 ([keys.md](spec/keys.md)) |
| 알림 규칙에 넣은 Webhook URL (Slack·Discord 등) | 규칙이 발동할 때 (수동 평가 포함). 규칙을 저장할 때와 보내기 직전에는 대상 확인을 위해 호스트 이름을 DNS로 조회합니다. | 규칙 이름·지표·연산자·기준값·실제값·시간 창. Slack 형식에는 규칙 ID와 UTC 발동 시각도 들어갑니다. |

Webhook 대상은 기본적으로 공개 주소만 허용합니다. 사설·루프백·링크 로컬·예약·멀티캐스트 주소와 `user:pass@`가 든 URL은 거부하고,
리디렉션을 따르지 않으며, 환경 변수의 프록시를 쓰지 않습니다. 응답 본문은 읽거나 저장하지 않습니다.
사내망 Webhook이 필요하면 서버 설정 `ALERT_ALLOW_PRIVATE_WEBHOOKS=true`로 허용합니다.

`127.0.0.1`의 로컬 API 서버와 Ollama(`127.0.0.1:11434`)에 보내는 요청은 이 PC 안에서만 오갑니다.
중복 실행을 막는 데스크톱 인스턴스끼리는 `127.0.0.1:47821`로 고정 제어 메시지(`AIMONITOR:show`, `AIMONITOR:ping`, `AIMONITOR:ack:v1`)만 주고받습니다. 사용 기록·인증 키는 보내지 않습니다.
설정 파일(`desktop_config.json`)의 서버 주소(`backend_url`)를 다른 서버로 바꾸면 사용량 조회·수집 요청과 서버 인증 키가 그 서버로 가며, 이때는 로컬 서버를 띄우지 않습니다.

**Python SDK**로 내 앱의 호출을 기록하면 기록은 `api_url`에 지정한 수집 서버(기본은 이 PC의 localhost)로 갑니다.
자동 추출은 응답의 토큰 수만 읽습니다. 공급자·모델·토큰 수·지연·상태와 직접 넣은 식별자·태그·정제된 메타데이터·`error_message`를 보내며,
`api_key`를 설정하면 `X-API-Key` 헤더로 함께 보냅니다. SDK는 도구의 로그나 로그인 파일을 읽지 않습니다.

## 저장하는 것

| 위치 | 내용 |
| :--- | :--- |
| DB (기본값: `aimonitor.db` — exe는 `~/.aimonitor/`, 소스 실행은 저장소 폴더; 실제 저장소는 서버 설정 `DATABASE_URL`이 결정) | 기록마다 공급자·모델·토큰 수·시각·지연·비용·상태, 계정 해시·trace/session ID·태그·정제된 메타데이터·오류 메시지, 수집기가 보낸 중복 제거 키(`dedupe_key`, 해시). 알림 규칙(**Webhook URL은 평문**)과 발동·발송 결과 이력 |
| `~/.aimonitor/api_keys.json` | 키 원문만 DPAPI로 암호화해(`sealed`) 저장합니다. 공급자·키 ID·라벨·검증 상태·마지막 검증 시각·잔액 설명은 평문입니다. |
| `~/.aimonitor/api_keys.json.lock` | 데스크톱과 서버가 키 파일을 동시에 고치지 않게 하는 잠금 파일. 비밀값은 없습니다. |
| `~/.aimonitor/ai_watcher.lock` | 여러 수집기가 같은 로그를 동시에 수집하지 않게 하는 잠금 파일. 비밀값은 없습니다. |
| `~/.aimonitor/desktop_config.json` | 화면 설정, 서버 주소, 서버 인증 키(DPAPI 암호화), USD/KRW 환율과 출처·기준일·마지막 조회 성공 시각 |
| `~/.aimonitor/accounts.json`, `account_timeline.json` | 계정 해시·라벨·설정 폴더 경로·관측 시각 (타임라인은 최신 5,000개까지). `accounts.json`에는 계정별 인증 방식(`subscription/api/unknown`), 현재 방식 첫 관측·마지막 변경 시각도 저장합니다. 키·토큰 값은 없습니다. |
| `~/.aimonitor/quota/`, `claude_official_quota.json` | 한도 조회 결과(퍼센트·리셋 시각·구독 종류). 토큰은 없습니다. |
| `~/.aimonitor/*offsets*.json`, `folder_offsets/`, `ai_cursors.json`, `ai_seen_claude.json` | 로그 파일별 읽은 위치와 중복 제거 키, 파일 교체를 알아보기 위한 파일 ID·크기·시각과 앞부분 일부의 해시. 본문은 없습니다. 30일 동안 사라진 파일의 기록은 지웁니다. |
| `~/.aimonitor/logs/` (exe만) | 역할별 실행 로그 (파일당 1 MiB, 백업 3개). 손상된 로그 줄은 원문 없이 건수만 남깁니다. 요청 로그는 요청 ID·메서드·경로·상태·소요 시간만 남기고 쿼리 문자열·요청 본문은 남기지 않습니다. 오류 로그는 예외 종류만 남기고 예외 문구·SQL 매개변수는 남기지 않습니다. |
| 레지스트리 `HKCU\…\Run\AIMONITOR` | 자동 실행을 켰을 때만 |

`docker-compose.yml`의 기본 DB는 `aimonitor-data` 데이터 볼륨의 `/app/data/aimonitor.db`입니다.

API 응답은 Webhook URL을 `scheme://host/…`로 가려서 돌려주지만 원본은 DB에 평문으로 남습니다. DB 파일과 백업은 비밀값처럼 다루세요.

자동 보관 정리는 기본적으로 꺼져 있습니다(`RETENTION_DAYS=0`, 무제한 보관). 기간을 정하면 그보다 오래된 기록과 알림 이력을
1시간마다 UTC 기준으로 지우며, 수동 정리 API도 같은 기준을 씁니다. 지운 기록은 복구할 수 없습니다.

## 한계

- **SDK로 직접 기록할 때** 자동 오류 기록은 예외 종류(클래스 이름)만 보냅니다. `error_message`를 직접 넘기면 그 문구가 2,000자까지 저장되므로 프롬프트·계정 정보·비밀값을 넣지 마세요. 메타데이터의 `prompt`·`content` 같은 키는 중첩된 dict·list까지 `[redacted]`로 바꾸지만, 키 이름 기반이라 다른 이름으로 넣은 원문은 막지 못합니다. 이 보호는 새로 수집하는 기록부터 적용되며 예전 DB 내용을 다시 정제하지 않습니다.
- 로컬 API는 기본적으로 API 키가 없어 **이 PC의 다른 프로그램은 조회할 수 있습니다.** 키 목록에는 원문 대신 앞뒤 4자만 보이는 마스킹 값(8자 이하는 전부 가림)과 라벨·검증 상태·청구액이 들어 있습니다. 웹페이지가 브라우저를 통해 조작하는 시도(교차 출처 쓰기, DNS rebinding)는 API 키 설정과 관계없이 차단합니다. 공유 PC라면 `API_KEY`와 `REQUIRE_AUTH_FOR_READS=true`를 설정하고, 외부 네트워크에 공개한다면 `ALLOWED_HOSTS`와 `CORS_ORIGINS`도 실제로 쓰는 값만 허용하세요.
- Webhook 대상 검사와 실제 연결 사이에는 DNS rebinding 경쟁이 남습니다. 서버를 외부에 공개한다면 네트워크 송신 제한을 함께 두세요.

## 지우기

DB 위치를 바꾸지 않은 기본 로컬 설치의 데이터는 다음 폴더들에 있습니다. 앱 설정에서 자동 실행을 끄고 앱과 서버를 종료한 뒤
앱 폴더와 `%USERPROFILE%\.aimonitor`를 삭제합니다. 소스 실행이었다면 저장소 폴더의 `aimonitor.db`와 SQLite 부속 파일
(`aimonitor.db-wal`·`aimonitor.db-shm`·`aimonitor.db-journal`)도 지웁니다.

서버 설정 `DATABASE_URL`을 바꿨다면 그 값이 가리키는 실제 DB도 따로 삭제해야 합니다. SQLite 파일이면 해당 파일과
같은 이름에 `-wal`·`-shm`·`-journal`이 붙은 부속 파일을 함께 지웁니다. 기본 폴더 삭제만으로 다른 위치의 DB는 지워지지 않습니다.

Docker 설치는 `docker-compose.yml`의 `aimonitor-data` 데이터 볼륨도 따로 삭제해야 합니다. 컨테이너나 이미지만 삭제하면
DB는 남습니다. 실제 볼륨 이름에는 Compose 프로젝트 이름 접두사가 붙을 수 있으므로 해당 배포의 볼륨을 확인하세요.

서버 주소(`backend_url`)가 가리키는 원격 서버의 데이터와 DB 백업은 따로 삭제해야 합니다. 이 PC의 폴더를 지워도 지워지지 않습니다.
다른 도구의 로그·로그인 파일은 건드리지 않습니다.
