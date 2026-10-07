# 🛡️ AIMONITOR

> **Real-time AI / LLM Observability, Cost & Performance Monitoring Platform**

AIMONITOR는 OpenAI, Anthropic, Gemini, 로컬 Ollama 등 다양한 LLM 호출의 **지연 시간(P50/P95/P99), 토큰 사용량, 실시간 비용, 에러율 및 이상 징후 알림**을 추적하는 셀프호스팅 모니터링 시스템입니다. UI는 Windows 데스크톱 앱(메인 창·트레이·위젯)으로 제공합니다.

**v0.6 UI 변경** — 웹 대시보드는 제거되었습니다. 백엔드는 HTTP API만 제공하며 SDK·키 관리·연결 AI·CSV API는 유지합니다. 적용된 서버 버전과 단가표 기준일은 `/api/v1/system/info`, `/api/v1/system/pricing`에서 확인할 수 있습니다.
변경 내역: [CHANGELOG.md](CHANGELOG.md) · 기획·명세: [docs/intent.md](docs/intent.md) → [docs/spec/](docs/spec/README.md) → [docs/plan.md](docs/plan.md)

---

## 🌟 주요 기능

- **⚡ Zero-Friction Ingestion** — 코드 3줄 또는 `@monitor.observe()` 데코레이터. 동기·비동기 모두 지원하며, 프로바이더 응답에서 토큰 수를 **자동 추출**합니다.
- **🖥️ Windows 데스크톱 앱** — 연결 AI 사용량·컨텍스트·비용, 메인 창·트레이·상시 위젯. 트레이의 '대시보드 열기'는 데스크톱 메인 창을 엽니다.
- **🚨 자동 알림** — 백그라운드 스케줄러가 규칙을 주기적으로 평가합니다. **쿨다운**과 조건부 UPDATE 기반 선점으로, 워커를 여러 개 띄워도 한 장애에 한 번만 알립니다. Slack / Discord Webhook 지원.
- **📄 CSV 내보내기** — 비용 정산용 스트리밍 내보내기. 프롬프트·응답 관련 컬럼은 제외되며, 단가 미상 호출은 `0` 이 아닌 빈 칸으로 나갑니다.
- **💰 정직한 비용 계산** — 단가를 모르는 모델은 **0원이 아니라 `계산 불가`** 로 표시합니다. 프롬프트 캐시의 **읽기·쓰기 단가를 분리**해 계산합니다.
- **🔐 API 키 인증** — 수집·변경 엔드포인트 보호, 필요 시 조회까지 보호.
- **🔒 원문 미저장** — 프롬프트·응답 텍스트는 DB에도 로그에도 남지 않습니다. 메타데이터의 관련 필드는 수집 시 자동 제거됩니다.
- **📦 API 전용 컨테이너 배포** — Python Docker 이미지, non-root 실행, readiness 헬스체크. 데스크톱 UI는 PC에서 실행합니다.

---

## 🚀 빠른 시작

### 1. Windows 데스크톱 앱

```powershell
pip install -r backend/requirements.txt -r desktop/requirements.txt
python run_desktop.py
```

데스크톱 실행기가 로컬 API 서버를 준비합니다. 콘솔 없이 실행하려면 `pythonw run_desktop.pyw`를 사용합니다.
자세한 동작과 설정은 [데스크톱 명세](docs/spec/desktop.md)를 확인하세요.

### 2. Docker Compose (API 서버)

```bash
cp .env.example .env
python -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))" >> .env
docker compose up --build -d
```

- API 기준 주소: `http://localhost:8000/api/v1`
- 헬스체크: `http://localhost:8000/health`
- Swagger 문서: `http://localhost:8000/api/v1/docs`

기본 포트 바인딩은 `127.0.0.1:8000` 입니다. 외부에 노출하려면 **먼저 `API_KEY`를 설정한 뒤** `docker-compose.yml`의 포트를 `"8000:8000"`으로 변경하세요.
`http://localhost:8000/`에는 웹 화면이 없으며 JSON 404를 반환합니다.

### 3. 로컬 API 개발

```bash
pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --port 8000 --reload
```

### 4. 테스트

```bash
python -m pytest -q
ruff check .
```

### 5. 데모 트래픽

테스트용 API 서버에 데모 기록을 넣으려면 다음 스크립트를 실행합니다. 실제 수집 API를 사용하므로 운영 데이터에 섞이지 않도록 별도 DB를 사용하세요.

```bash
python scripts/simulate.py --batches 20 --spread-hours 6
```

---

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
        model_name="claude-3-5-sonnet",
        prompt_tokens=1200,
        completion_tokens=600,
        cache_read_tokens=8000,
        cache_write_tokens=1500,
        latency_ms=450.0,
    )
```

요청 경로에서 네트워크 대기를 없애려면 `AIMonitor(background=True)` 를 사용하세요 — 백그라운드 워커가 배치로 전송합니다. 자세한 내용은 [sdk/python/README.md](sdk/python/README.md).

---

## 🔐 보안 설정

| 항목 | 설정 | 기본값 | 비고 |
| :--- | :--- | :--- | :--- |
| API 키 | `API_KEY` | *(없음)* | 미설정 시 **모든 엔드포인트가 공개**되며 시작 시 경고 로그가 남습니다 |
| 조회 보호 | `REQUIRE_AUTH_FOR_READS` | `false` | `true`면 데스크톱·SDK 조회에도 서버 키가 필요합니다 |
| 허용 출처 | `CORS_ORIGINS` | localhost | `*` 지정 시 credentials 는 자동 비활성화됩니다 |
| 보관 기간 | `RETENTION_DAYS` | `0` (무제한) | 설정 시 오래된 트레이스를 주기적으로 삭제합니다 |

클라이언트는 `X-API-Key: <키>` 또는 `Authorization: Bearer <키>` 로 인증합니다. 데스크톱 서버 인증은 [데스크톱 명세](docs/spec/desktop.md)를 따릅니다.

전체 설정 목록은 [.env.example](.env.example)에 있습니다.

---

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
| `POST` | `/api/v1/keys` | 공급자 키 등록·교체 | 쓰기 |
| `DELETE` | `/api/v1/keys/{provider}` | 등록 키 삭제 | 쓰기 |
| `POST` | `/api/v1/keys/{provider}/verify` | 공급자 키 검증 | 쓰기 |
| `GET` | `/api/v1/keys/spend` | 공식 청구액·잔액 (15분 캐시) | 읽기 |
| `GET` | `/api/v1/alerts/metrics` | 사용 가능한 지표·연산자 목록 | 읽기 |
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

---

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

---

## 🏗️ 구조

```
AIMONITOR/
├── backend/app/
│   ├── core/          # 설정, 인증, 로깅, UTC 처리, 입력 정제, 지표 카탈로그
│   ├── db/            # 엔진(WAL), 세션, 경량 스키마 업그레이드
│   ├── models/        # TraceEvent, AlertRule, AlertHistory
│   ├── routers/       # /traces, /metrics, /alerts, /system
│   └── services/      # 비용, 집계, 알림, 보관, 스케줄러
├── backend/tests/     # 단위·통합·동시성·마이그레이션 테스트
├── desktop/           # Windows 데스크톱 메인 창·트레이·위젯 (HTTP API)
├── run_desktop.py     # 데스크톱 실행기 (콘솔 없이 실행: run_desktop.pyw)
├── sdk/python/        # 설치 가능한 Python 클라이언트 (aimonitor-sdk)
├── scripts/simulate.py
├── docs/              # intent.md(기획) · spec/(기능별 명세) · plan.md(현재 작업 계획)
├── AGENTS.md          # AI 코딩 에이전트 공통 지침 (CLAUDE.md·GEMINI.md는 포인터)
└── CHANGELOG.md
```

---

## 📄 라이선스

MIT
