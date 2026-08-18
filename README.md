# 🛡️ AIMONITOR

> **Real-time AI / LLM Observability, Cost & Performance Monitoring Platform**

AIMONITOR는 OpenAI, Anthropic, Gemini, 로컬 Ollama 등 다양한 LLM 호출의 **지연 시간(P50/P95/P99), 토큰 사용량, 실시간 비용 산출, 에러율 및 이상 징후 알림**을 실시간으로 추적하고 대시보드로 시각화하는 풀스택 모니터링 시스템입니다.

---

## 🌟 Key Features

- **⚡ Zero-Friction Ingestion**: 코드 3줄 또는 `@monitor.observe()` 데코레이터로 LLM 요청/응답 로깅 (동기 & 비동기 `async def` 완벽 지원).
- **📊 Real-time Web Dashboard**: 초단위 지연 시간, 토큰, 실시간 비용, 에러율을 시각화하는 React + Vite 모던 다크 테마 콘솔.
- **🚨 Proactive Alerting & Anomaly Detection**: 에러율 스파이크, P95 지연 시간 초과, 비용 급증 시 Slack/Discord Webhook 자동 알림 및 대시보드 알림 관리.
- **💰 Accurate Cost Tracking**: OpenAI, Anthropic, Gemini, Ollama 모델별 최신 토큰 단가 기준 실시간 비용 자동 산출.
- **🔒 Production & Concurrency Ready**: SQLite WAL 모드 + Busy Timeout으로 동시 다중 쓰기 락 원천 방어, Multi-stage Docker 단일 컨테이너 통합 배포.

---

## 🏗️ Architecture & Tech Stack

```
AIMONITOR/
├── .github/copilot-instructions.md   # GitHub Copilot Worker 전용 지침
├── AGENTS.md                         # AI 협업 원칙 및 역할 정의
├── docs/
│   ├── PRD.md                        # 제품 요구사항 정의서
│   ├── PLAN.md                       # 마스터 개발 로드맵
│   ├── architecture.md               # 시스템 아키텍처 & 데이터 흐름
│   ├── workflow.md                   # Coordinator-Worker 협업 워크플로우
│   └── PROGRESS.md                   # 전체 개발 진행 및 완료 상세 보고서
├── tasks/
│   ├── task_board.md                 # 실시간 태스크 관리 보드 (100% 완료)
│   └── TASK-*.md                     # 단위 태스크 명세서
├── backend/                          # FastAPI + SQLModel + SQLite Backend
│   ├── app/
│   │   ├── core/                     # 앱 환경 설정 & CORS
│   │   ├── db/                       # SQLModel DB 엔진 (WAL 모드) & 세션
│   │   ├── models/                   # TraceEvent, AlertRule, AlertHistory 스키마
│   │   ├── routers/                  # /traces, /metrics, /alerts REST API
│   │   └── services/                 # 비용 계산기, 집계 엔진, 알림 평가기
│   └── tests/                        # 30개 단위/통합/스트레스 테스트 (100% PASS)
├── frontend/                         # React 18 + Vite + TypeScript Dashboard
│   └── src/
│       ├── components/               # Header, KPICards, Charts, TraceTable, Modals
│       ├── types.ts                  # TypeScript 타입 정의
│       ├── App.tsx                   # 메인 대시보드 (5초 자동 폴링 + Demo 시뮬레이터)
│       └── index.css                 # 모던 다크 테마 디자인 시스템
├── sdk/python/                       # Python Client SDK (동기/비동기 & Context Manager)
└── scripts/
    └── simulate.py                   # 가상 LLM 트래픽 생성기
```

---

## 🚀 빠른 시작 (Quick Start)

### 방법 1. Docker Compose 원클릭 실행 (추천)
```bash
docker compose up --build -d
```
- Web Dashboard & API: `http://localhost:8000`
- Swagger API Docs: `http://localhost:8000/api/v1/docs`

### 방법 2. 로컬 개발 환경 실행
```bash
# 1. 백엔드 서버 구동 (포트 8000)
python3 -m uvicorn backend.app.main:app --port 8000 --reload

# 2. 프론트엔드 대시보드 구동 (포트 3000)
cd frontend && npm run dev
```

### 방법 3. 테스트 실행 (30/30 All Passed)
```bash
PYTHONPATH=. python3 -m pytest backend/tests sdk/python/tests
```

### 방법 4. 데모 트래픽 생성 (Simulation)
대시보드 상단의 **"Generate Demo Traffic"** 버튼을 클릭하거나 아래 CLI를 실행하세요:
```bash
python3 scripts/simulate.py
```

---

## 🐍 Python SDK 사용법

```python
from sdk.python.aimonitor.client import AIMonitor

# 1. 데코레이터 사용 (동기 및 비동기 함수 지원)
monitor = AIMonitor(api_url="http://localhost:8000/api/v1")

@monitor.observe(provider="openai", model_name="gpt-4o", tags=["production"])
def call_gpt(prompt: str):
    # 실제 LLM 호출 로직
    return "LLM Response"

# 2. 컨텍스트 매니저 및 명시적 추적
with AIMonitor() as mon:
    mon.track(
        provider="anthropic",
        model_name="claude-3-5-sonnet",
        prompt_tokens=1200,
        completion_tokens=600,
        latency_ms=450.0,
        status="SUCCESS",
        tags=["agent-task"],
    )
```