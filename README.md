# 🛡️ AIMONITOR

> **Real-time AI / LLM Observability, Cost & Performance Monitoring Platform**

AIMONITOR는 OpenAI, Anthropic, Gemini, 로컬 Ollama 등 다양한 LLM 호출의 **지연 시간(P50/P95/P99), 토큰 사용량, 실시간 비용, 에러율 및 이상 징후 알림**을 추적하고 대시보드로 시각화하는 셀프호스팅 모니터링 시스템입니다.

**현재 버전: v0.3.0** — 인증, 백그라운드 알림 스케줄러, 데이터 보관 정책, 프롬프트 캐시 단가 분리 계산에 더해 동시 실행 안전성, CSV 내보내기, 실 페이지네이션이 들어간 릴리스입니다.
변경 내역: [CHANGELOG.md](CHANGELOG.md) · 기획·명세: [docs/intent.md](docs/intent.md) → [docs/spec/](docs/spec/README.md) → [docs/plan.md](docs/plan.md)

---

## 🌟 주요 기능

- **⚡ Zero-Friction Ingestion** — 코드 3줄 또는 `@monitor.observe()` 데코레이터. 동기·비동기 모두 지원하며, 프로바이더 응답에서 토큰 수를 **자동 추출**합니다.
- **📊 실시간 대시보드** — 1H/6H/24H/7D/30D 시간대 전환, KPI 카드, 지연 시간·비용 차트, 라이브 트레이스 스트림.
- **🚨 자동 알림** — 백그라운드 스케줄러가 규칙을 주기적으로 평가합니다. **쿨다운**과 조건부 UPDATE 기반 선점으로, 워커를 여러 개 띄워도 한 장애에 한 번만 알립니다. Slack / Discord Webhook 지원.
- **📄 CSV 내보내기** — 비용 정산용 스트리밍 내보내기. 프롬프트·응답 관련 컬럼은 제외되며, 단가 미상 호출은 `0` 이 아닌 빈 칸으로 나갑니다.
- **💰 정직한 비용 계산** — 단가를 모르는 모델은 **0원이 아니라 `계산 불가`** 로 표시합니다. 프롬프트 캐시의 **읽기·쓰기 단가를 분리**해 계산합니다.
- **🔐 API 키 인증** — 수집·변경 엔드포인트 보호, 필요 시 조회까지 보호.
- **🔒 원문 미저장** — 프롬프트·응답 텍스트는 DB에도 로그에도 남지 않습니다. 메타데이터의 관련 필드는 수집 시 자동 제거됩니다.
- **📦 단일 컨테이너 배포** — Multi-stage Docker 빌드, non-root 실행, readiness 헬스체크.

---

## 🚀 빠른 시작

### 1. Docker Compose (권장)

```bash
cp .env.example .env
python -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))" >> .env
docker compose up --build -d
```

- 대시보드 & API: `http://localhost:8000`
- Swagger 문서: `http://localhost:8000/api/v1/docs`

기본 포트 바인딩은 `127.0.0.1:8000` 입니다. 외부에 노출하려면 **먼저 `API_KEY`를 설정한 뒤** `docker-compose.yml`의 포트를 `"8000:8000"`으로 변경하세요.

### 2. 로컬 개발

```bash
pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --port 8000 --reload
```

```bash
cd frontend && npm install && npm run dev
```

Vite 개발 서버(`http://localhost:3000`)가 `/api` 요청을 8000 포트로 프록시합니다.

### 3. 테스트

```bash
python -m pytest
```

```bash
cd frontend && npm run typecheck && npm test && npm run build
```

### 4. 데모 트래픽

대시보드의 **"Generate Demo Traffic"** 버튼을 누르거나:

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
| 조회 보호 | `REQUIRE_AUTH_FOR_READS` | `false` | `true`면 대시보드도 키가 필요합니다 |
| 허용 출처 | `CORS_ORIGINS` | localhost | `*` 지정 시 credentials 는 자동 비활성화됩니다 |
| 보관 기간 | `RETENTION_DAYS` | `0` (무제한) | 설정 시 오래된 트레이스를 주기적으로 삭제합니다 |

클라이언트는 `X-API-Key: <키>` 또는 `Authorization: Bearer <키>` 로 인증합니다. 대시보드에서는 우측 상단 **설정** 아이콘에서 키를 입력하면 브라우저에 저장됩니다.

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

| 값 | 의미 | 대시보드 표기 |
| :--- | :--- | :--- |
| `pricing_table` | 내장 단가표 기준 계산 | 계산된 금액 |
| `custom` | 호출자가 `cost_usd` 를 직접 전달 | 전달된 금액 |
| `local_free` | 로컬 추론(Ollama 등), 토큰 과금 없음 | `$0.00000` |
| `unknown` | **단가 정보 없음** | **`계산 불가`** |

프롬프트 캐시는 쓰기와 읽기 단가가 다릅니다(예: Anthropic 은 쓰기 1.25배, 읽기 0.1배). AIMONITOR는 이 둘을 합산하지 않고 각각의 단가로 계산합니다. 적용 중인 단가는 `GET /api/v1/system/pricing` 으로 확인할 수 있습니다.

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
├── frontend/src/      # React 18 + Vite 대시보드 (api.ts 단일 클라이언트)
├── sdk/python/        # 설치 가능한 Python 클라이언트 (aimonitor-sdk)
├── scripts/simulate.py
├── docs/              # intent.md(기획) · spec/(기능별 명세) · plan.md(현재 작업 계획)
├── AGENTS.md          # AI 코딩 에이전트 공통 지침 (CLAUDE.md·GEMINI.md는 포인터)
└── CHANGELOG.md
```

---

## 📄 라이선스

MIT
