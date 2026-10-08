<h1 align="center">AIMONITOR</h1>

<p align="center">
  <b>Claude Code · Codex · Copilot의 남은 한도와 사용량을 Windows 트레이에서 한눈에.</b><br>
  계정 여러 개도 나눠서. 프롬프트는 어디에도 남기지 않고.
</p>

<p align="center">
  <a href="https://github.com/supsuphansoup/AIMONITOR/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/supsuphansoup/AIMONITOR/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/supsuphansoup/AIMONITOR/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/supsuphansoup/AIMONITOR"></a>
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center"><b>한국어</b> · <a href="README.en.md">English</a></p>

<!-- 스크린샷 자리: docs/images/widget.png (항상 위 위젯), docs/images/main.png (메인 창) — 공개 전에 추가 -->

## 이런 분께

- Claude Code의 **5시간·주간 한도가 얼마나 남았는지** 매번 `/usage`로 확인하기 번거로운 분
- **개인·회사 Claude 계정**이나 Codex·Copilot을 함께 써서 어디서 얼마나 썼는지 헷갈리는 분
- API 비용을 보고 싶지만 **프롬프트를 외부 SaaS에 넘기기는 싫은** 분

## 주요 기능

- **항상 위 위젯** — 도구별 남은 한도(%)와 리셋 시각, 오늘 사용량·비용을 화면 구석에 띄워 둡니다. 트레이에서 숨기기·열기.
- **계정별 분리** — 기본 설정 폴더와 `CLAUDE_CONFIG_DIR`·`CODEX_HOME`, `~/.claude-*`·`~/.codex-*`, 직접 추가한 폴더를 모두 읽고 계정마다 나눠 보여줍니다.
- **정직한 숫자** — 공식 값이 없으면 지어내지 않고 `확인 불가`·`계산 불가`로 표시합니다. 추정치는 추정이라고 표시합니다.
- **메인 창** — 기록·차트(계정·모델별), 공급자 API 키와 청구액·잔액, 알림 규칙(Slack/Discord), CSV 내보내기.
- **내 앱의 LLM 호출도 함께** — Python SDK 3줄 또는 HTTP API로 OpenAI·Anthropic·Gemini·Ollama 호출의 지연·토큰·비용을 같은 화면에서.
- **Windows 시작 시 자동 실행** — 설정에서 켜고 끕니다.

### 지원 도구

| 도구 | 사용량 (토큰·비용) | 남은 한도 | 출처 |
| :--- | :--- | :--- | :--- |
| Claude Code | ✅ | ✅ 5시간·주간 | 로컬 세션 로그 + Anthropic 사용량 조회 |
| OpenAI Codex | ✅ | ✅ 세션 로그의 한도 창 | 로컬 세션 로그 |
| GitHub Copilot | ✅ | ⚠️ 이번 달 프리미엄 요청 수 ÷ 직접 입력한 한도 | 로컬 세션 로그 (GitHub는 잔여량을 제공하지 않음) |
| Google Antigravity | ⚠️ 추정 (본문 길이 기반) | — | 로컬 로그 |
| Cursor·Windsurf·Ollama | 연결 여부만 | — | 설치 폴더 |

## 설치

### exe (권장, Python 필요 없음)

1. [Releases](https://github.com/supsuphansoup/AIMONITOR/releases/latest)에서 `AIMONITOR-<버전>-windows-x64.zip`을 받아 원하는 폴더에 풉니다.
2. `AIMONITOR\AIMONITOR.exe`를 실행합니다.

- 코드 서명이 없어 처음 실행할 때 **SmartScreen 경고**가 뜰 수 있습니다. `추가 정보 → 실행`을 누르세요. 받은 파일은 함께 올린 `.sha256`으로 확인할 수 있습니다 (`Get-FileHash <zip> -Algorithm SHA256`).
- 설정·DB·로그는 `%USERPROFILE%\.aimonitor`에 저장됩니다.
- 지우려면 설정에서 자동 실행을 끈 뒤 앱 폴더와 `%USERPROFILE%\.aimonitor`를 삭제합니다.

### 소스에서 실행

Python 3.10 이상(Tkinter 포함)이 필요합니다.

```powershell
git clone https://github.com/supsuphansoup/AIMONITOR.git
cd AIMONITOR
pip install -r backend/requirements.txt -r desktop/requirements.txt
python run_desktop.py          # 콘솔 없이: pythonw run_desktop.pyw
```

앱이 `127.0.0.1:8000`에 로컬 API 서버를 함께 띄웁니다. 소스 실행 시 DB는 저장소 폴더의 `aimonitor.db`입니다. exe를 직접 만들려면 `pip install -r build-windows/requirements.txt` 후 `pyinstaller build-windows/aimonitor.spec --noconfirm`.

## 🔒 개인정보: 무엇을 읽고, 어디로 보내나

AIMONITOR는 다른 도구의 로그와 로그인 파일을 읽는 앱입니다. 그래서 읽는 것과 보내는 것을 모두 적어 둡니다. 자세한 동작은 [데스크톱 명세](docs/spec/desktop.md)에 있습니다.

**이 PC에서 읽는 것**

| 대상 | 쓰는 값 |
| :--- | :--- |
| Claude Code 세션 로그 (`~/.claude/projects/**/*.jsonl` 등) | 응답의 토큰 수·모델·시각·요청 ID. **본문은 쓰지 않습니다.** |
| Claude `.credentials.json` | 로그인 토큰 — 아래 Anthropic 사용량 조회에만 사용. 저장·기록하지 않습니다. |
| Claude `.claude.json`, Codex `auth.json` | 계정 ID — 해시(12자)로만 저장. Codex 토큰·API 키는 읽지 않습니다. |
| Codex 세션 로그 (`~/.codex/sessions`) | 토큰 수·모델·시각·한도 스냅샷 |
| Copilot 세션 로그 (`~/.copilot/session-state`) | 세션 종료 이벤트의 토큰·요청 수. 로그인 토큰은 **개수만** 셉니다. |
| Antigravity 로그 | 본문 **길이**로 토큰을 추정합니다. 본문은 메모리에서만 쓰고 저장·전송하지 않습니다. |
| 다른 앱의 DB (VS Code·Copilot·Antigravity) | 플랜·연결 여부만, **읽기 전용**으로 엽니다. |

**밖으로 보내는 곳** — 이것이 전부입니다. 텔레메트리·분석·자동 업데이트는 없습니다.

| 목적지 | 언제 | 보내는 것 |
| :--- | :--- | :--- |
| `api.anthropic.com/api/oauth/usage` | Claude 카드·위젯 갱신 시 (계정별 60초 캐시) | 해당 계정의 Claude 로그인 토큰. **비공식 엔드포인트**라 예고 없이 바뀔 수 있습니다. |
| 환율 API (`open.er-api.com` → `exchangerate-api.com` → jsDelivr) | 시작 시, 30분마다 | 없음 (원화 표시용, 키 없음) |
| 등록한 키의 공급자 (OpenAI·Anthropic·OpenRouter·DeepSeek 등) | 키를 등록했거나 `OPENROUTER_API_KEY`·`DEEPSEEK_API_KEY`·`*_ADMIN_KEY` 환경 변수가 있을 때 (15분 캐시), 검증 버튼 | 그 공급자의 키 (발급처에만 보냄) |
| 알림 규칙의 Webhook URL | 규칙이 발동할 때 | 규칙 이름·지표·값 |

**저장하는 것** — `~/.aimonitor`와 DB에 토큰 수·모델·시각·비용·계정 해시·폴더 경로를 둡니다. 공급자 API 키는 **Windows DPAPI로 암호화**해 현재 Windows 사용자만 풀 수 있습니다. Webhook URL은 DB에 평문으로 저장됩니다.

- 로컬 로그에서 모은 기록에는 프롬프트·응답 원문이 없습니다. SDK로 직접 기록할 때는 예외 메시지(`error_message`, 최대 2,000자)가 저장되므로, 예외 문구에 프롬프트 일부가 들어 있다면 남을 수 있습니다.
- 로컬 API는 `127.0.0.1`에서만 열립니다. 기본은 API 키가 없어 **이 PC의 다른 프로그램은 조회할 수 있습니다.** 웹페이지가 브라우저를 통해 API를 조작하는 시도(교차 출처 쓰기, DNS rebinding)는 차단합니다.

## 🐍 Python SDK

```bash
pip install -e sdk/python
```

```python
from aimonitor import AIMonitor

monitor = AIMonitor(api_url="http://localhost:8000/api/v1", api_key="...")

# 1) 데코레이터 — 지연 시간과 토큰 수를 자동 기록
@monitor.observe(provider="openai", model_name="gpt-4o", tags=["production"])
def call_gpt(prompt: str):
    return openai_client.chat.completions.create(...)

# 2) 명시적 기록 — 캐시 토큰 분리 전달
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

동기·비동기 모두 지원하며 프로바이더 응답에서 토큰 수를 자동 추출합니다. 요청 경로에서 네트워크 대기를 없애려면 `AIMonitor(background=True)`를 사용하세요 — 백그라운드 워커가 배치로 전송합니다. 자세한 내용은 [sdk/python/README.md](sdk/python/README.md).

## 🖥️ API 서버만 따로 (Docker)

데스크톱 없이 서버에서 수집만 하려면:

```bash
cp .env.example .env
python -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))" >> .env
docker compose up --build -d
```

- API 기준 주소 `http://localhost:8000/api/v1`, Swagger `http://localhost:8000/api/v1/docs`, 헬스체크 `/health`
- 기본 포트 바인딩은 `127.0.0.1:8000`입니다. 외부에 노출하려면 **먼저 `API_KEY`를 설정**한 뒤 `docker-compose.yml`의 포트를 바꾸세요. 웹 화면은 없습니다.

## 🔐 보안 설정

| 항목 | 설정 | 기본값 | 비고 |
| :--- | :--- | :--- | :--- |
| API 키 | `API_KEY` | *(없음)* | 미설정 시 모든 엔드포인트가 공개되며, `localhost` 이름으로 온 요청에만 응답합니다 |
| 조회 보호 | `REQUIRE_AUTH_FOR_READS` | `false` | `true`면 데스크톱·SDK 조회에도 서버 키가 필요합니다 |
| 허용 출처 | `CORS_ORIGINS` | localhost | 이 밖의 브라우저 출처에서 온 쓰기 요청은 거부합니다. `*`이면 credentials 비활성화 |
| 허용 호스트 | `ALLOWED_HOSTS` | *(없음)* | API 키 없이 `localhost` 외 이름으로 쓸 때만 (신뢰하는 네트워크) |
| 보관 기간 | `RETENTION_DAYS` | `0` (무제한) | 설정 시 오래된 기록을 주기적으로 삭제합니다 |

클라이언트는 `X-API-Key: <키>` 또는 `Authorization: Bearer <키>`로 인증합니다. 전체 설정은 [.env.example](.env.example).

## 🔌 API 요약

| Method | Path | 설명 | 인증 |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/traces` | 단일 트레이스 수집 | 쓰기 |
| `POST` | `/api/v1/traces/batch` | 배치 수집 (최대 `MAX_BATCH_SIZE`) | 쓰기 |
| `GET` | `/api/v1/traces` | 목록 조회 (필터·기간·페이지네이션) | 읽기 |
| `GET` | `/api/v1/traces/{trace_id}` | 단건 조회 | 읽기 |
| `GET` | `/api/v1/traces/export.csv` | CSV 스트리밍 내보내기 | 읽기 |
| `GET` | `/api/v1/metrics/overview` | KPI 요약 (`hours_back` 지원) | 읽기 |
| `GET` | `/api/v1/metrics/timeseries` | 시계열 버킷 | 읽기 |
| `GET` | `/api/v1/metrics/breakdown` | 모델·프로바이더별 집계 | 읽기 |
| `GET` | `/api/v1/metrics/active_contexts` | 데스크톱 도구별 사용량·최신 컨텍스트 | 읽기 |
| `GET` | `/api/v1/metrics/connected_ais` | 연결 AI·공식 한도·로컬 사용량 | 읽기 |
| `GET` | `/api/v1/keys` | 공급자 키 상태·마스킹 값 | 읽기 |
| `POST` | `/api/v1/keys` | 공급자 키 등록 (라벨별로 여러 개) | 쓰기 |
| `PATCH` | `/api/v1/keys/{provider}/{key_id}` | 키 라벨 변경 | 쓰기 |
| `DELETE` | `/api/v1/keys/{provider}/{key_id}` | 키 하나 삭제 | 쓰기 |
| `DELETE` | `/api/v1/keys/{provider}` | 그 공급자의 키 모두 삭제 | 쓰기 |
| `POST` | `/api/v1/keys/{provider}/verify` | 공급자 키 검증 | 쓰기 |
| `GET` | `/api/v1/keys/spend` | 공식 청구액·잔액 (15분 캐시) | 읽기 |
| `GET` | `/api/v1/alerts/metrics` | 사용 가능한 지표·연산자 목록 | 읽기 |
| `GET` | `/api/v1/alerts/rules` | 규칙 목록 | 읽기 |
| `POST` | `/api/v1/alerts/rules` | 규칙 생성 | 쓰기 |
| `PATCH` | `/api/v1/alerts/rules/{id}` | 규칙 부분 수정 (일시 중지 등) | 쓰기 |
| `DELETE` | `/api/v1/alerts/rules/{id}` | 규칙 및 관련 이력 삭제 | 쓰기 |
| `POST` | `/api/v1/alerts/evaluate` | 수동 평가 (`ignore_cooldown`) | 쓰기 |
| `GET` | `/api/v1/alerts/history` | 발동 이력 | 읽기 |
| `GET` | `/api/v1/system/info` | 런타임 설정 및 스케줄러 상태 | 읽기 |
| `GET` | `/api/v1/system/pricing` | 적용 중인 단가표 | 읽기 |
| `POST` | `/api/v1/system/purge?days=N` | N일 이전 데이터 삭제 | 쓰기 |
| `GET` | `/health` | Liveness (DB 미접근) | 공개 |
| `GET` | `/api/v1/system/ready` | Readiness (DB 확인) | 공개 |

모든 타임스탬프는 **UTC 오프셋이 포함된 ISO-8601** 로 반환됩니다.
`GET /traces` 는 전체 건수를 `X-Total-Count` 헤더로 함께 반환합니다.


## 💰 비용 계산 규칙

`cost_source` 필드가 비용의 출처를 명시합니다.

| 값 | 의미 | 비용 해석 |
| :--- | :--- | :--- |
| `pricing_table` | 내장 단가표 기준 계산 | 계산된 금액 |
| `custom` | 호출자가 `cost_usd` 를 직접 전달 | 전달된 금액 |
| `local_free` | 로컬 추론(Ollama 등), 토큰 과금 없음 | `$0.00000` |
| `unknown` | **단가 정보 없음** | **`계산 불가`** |

프롬프트 캐시는 쓰기와 읽기 단가가 다르고, 할인율도 모델마다 다릅니다(예: Claude Opus 5.5 읽기 0.05배, gpt-4o 0.5배, gpt-5 0.1배). AIMONITOR는 이 둘을 합산하지 않고 모델별 공식 단가로 각각 계산합니다. 적용 중인 단가와 확인일(`as_of`)·출처는 `GET /api/v1/system/pricing` 으로 확인할 수 있습니다.

비용은 **표준 단가 기준 추정치**입니다. Batch 할인, 긴 프롬프트 구간 요금, 1시간 캐시, 클라우드 파트너 가격 등은 반영하지 않습니다 ([docs/spec/cost.md](docs/spec/cost.md)).

## 🏗️ 구조

```
AIMONITOR/
├── desktop/           # Windows 데스크톱 앱: 메인 창·트레이·위젯, 로그 watcher, 계정·한도
├── backend/app/       # FastAPI: core(설정·인증·정제·UTC), db, models, routers, services(비용·집계·알림)
├── backend/tests/     # 백엔드·데스크톱 테스트
├── sdk/python/        # Python 클라이언트 (aimonitor-sdk)
├── build-windows/     # PyInstaller exe 빌드 (spec·진입점)
├── docs/              # intent.md(기획) → spec/(기능별 명세) → plan.md(현재 작업)
├── run_desktop.py     # 소스 실행기 (콘솔 없이: run_desktop.pyw)
├── AGENTS.md          # AI 코딩 에이전트 공통 지침 (CLAUDE.md·GEMINI.md는 포인터)
└── CHANGELOG.md
```

## 🧪 개발

```bash
pip install -r backend/requirements.txt -r desktop/requirements.txt
python -m pytest -q
ruff check .
python -m uvicorn backend.app.main:app --port 8000 --reload   # API 개발 서버
python scripts/simulate.py --batches 20 --spread-hours 6      # 데모 트래픽 (별도 DB 권장)
```

변경 내역은 [CHANGELOG.md](CHANGELOG.md), 기획·명세는 [docs/intent.md](docs/intent.md) → [docs/spec/](docs/spec/README.md) → [docs/plan.md](docs/plan.md).

## 📄 라이선스

[MIT](LICENSE)
