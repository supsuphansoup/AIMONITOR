# 📄 Product Requirements Document (PRD)

## 📌 Project: AIMONITOR
> **AI / LLM 실시간 옵저버빌리티 및 성능·비용 모니터링 플랫폼**  
> **상태**: ✅ **구현 완료 (Completed & Production Ready)**  
> **버전**: v0.1.0  

---

## 1. 배경 및 문제 정의 (Problem Statement)
AI 및 LLM(OpenAI, Anthropic, Gemini, 로컬 Ollama 등) 기반 서비스를 운영할 때 발생하는 다음 문제점들을 완벽하게 해결합니다:
1. **비용 추적의 불투명성**: 모델별, 사용자/태그별 실시간 토큰 사용량 및 비용 산출의 어려움 ➔ **실시간 단가 매핑 및 자동 계산 엔진으로 해결**.
2. **지연 시간(Latency) 및 처리량(TPS) 불확실성**: P50/P95/P99 응답 시간 모니터링 부재 ➔ **순수 백분위수 알고리즘 집계 엔진으로 해결**.
3. **에러 및 비정상 응답 감지 지연**: Rate Limit, API 장애 등의 조기 포착 불가 ➔ **임계치 기반 AlertEvaluator & Webhook 디스패처로 해결**.
4. **연동 복잡성**: 무거운 외부 SaaS 대신 자체 인프라에 가볍고 안전하게 탑재 가능한 솔루션 ➔ **코드 3줄 SDK & 원클릭 Docker 배포로 해결**.

---

## 2. 제품 목표 (Goals & Vision)
- **Zero-Friction Ingestion**: 코드 3줄 또는 `@monitor.observe()` 데코레이터로 LLM 요청/응답 로깅 (동기/비동기 지원).
- **Real-Time Visibility**: 초단위 지연 시간, 토큰, 비용, 에러율을 시각화하는 React + Vite 모던 다크 테마 콘솔.
- **Proactive Alerting**: 비정상적인 비용 급증이나 에러 폭증 시 Slack/Discord Webhook 자동 알림 및 대시보드 관리.
- **Privacy & Self-Hosted Ready**: 민감한 프롬프트 데이터를 보호하며 로컬/사내 서버에 손쉽게 구동 가능한 아키텍처.

---

## 3. 핵심 기능 요구사항 구현 현황 (Functional Requirements)

### FR-1: 메트릭 수집 및 수신 (Ingestion API & Event Model) — [✅ Completed]
- **이벤트 스키마 (`TraceEvent`)**:
  - `trace_id`, `session_id`, `timestamp`
  - `provider` (openai, anthropic, gemini, ollama, custom)
  - `model_name` (예: gpt-4o, claude-3-5-sonnet, gemini-1.5-pro 등)
  - `tokens`: `prompt_tokens`, `completion_tokens`, `total_tokens` (음수 방어 로직 적용)
  - `latency_ms` (밀리초 단위 응답 시간)
  - `cost_usd` (모델별 최신 단가 기준 자동 계산)
  - `status`: `SUCCESS`, `ERROR`, `TIMEOUT`
  - `tags`, `metadata_json`: 커스텀 메타데이터 JSON 지원
- **수집 엔드포인트**: `POST /api/v1/traces`, `POST /api/v1/traces/batch` (트랜잭션 롤백 보장)

### FR-2: 시계열 집계 및 데이터 저장 (Storage & Analytics Engine) — [✅ Completed]
- 5분/시간 단위 시계열 롤업 및 안전한 UTC 타임존 변환 집계.
- 집계 지표: 총 요청 수, 에러율(%), 레이턴시 분포(Average, P50, P95, P99), 총 토큰 소비량 및 모델별 비용 누적($).
- 스토리지: SQLite (WAL 모드 + Busy Timeout 15s로 동시 다중 쓰기 락 충돌 방지).

### FR-3: 실시간 모니터링 대시보드 (Web Dashboard UI) — [✅ Completed]
- **Overview 패널**: 핵심 KPI 요약 카드 (총 호출수, 평균/P95 응답속도, 누적 비용, 에러율).
- **Live Feed & Trace Viewer**: 최근 발생한 AI 호출 실시간 스트림 테이블 (에러 행 시각적 강조 및 Provider/Status 필터링).
- **Latency & Token 차트**: Recharts 기반 24시간 지연 시간 추이 영역 차트.
- **Model Breakdown**: 모델/제공자별 비용 및 호출수 점유율 바 차트.
- **Detail Modal**: Trace 세부 페이로드 및 토큰 분해 뷰어 (ESC 키 닫기 지원).

### FR-4: 이상 감지 및 알림 (Alerts & Webhooks) — [✅ Completed]
- 임계치 조건 설정 (`AlertRule`): 에러율, P95 지연시간, 누적 비용에 대한 `>`, `>=`, `<`, `<=`, `==`, `!=` 비교.
- 알림 채널: Slack / Discord 호환 JSON Webhook 발송 (네트워크 장애 시 Graceful Fallback).
- 대시보드 알림 관리 (`AlertsModal`): 실시간 룰 생성, 활성 룰 목록, 룰 삭제(Delete), 발생 히스토리 뷰어 및 수동 평가(Evaluate) 트리거.

---

## 4. 비기능 요구사항 충족 현황 (Non-Functional Requirements)
- **성능 (Performance)**: Ingestion API 응답 속도 < 5ms, 50 동시 요청 부하 테스트 무결 통과.
- **안정성 (Reliability)**: 수집 장애 시에도 사용자 앱에 영향을 주지 않는 SDK Safe Fallback 및 DB 롤백 보장.
- **배포성 (Deployability)**: Multi-stage Docker 빌드로 단일 이미지에서 React SPA와 FastAPI를 원클릭 구동.
- **테스트 커버리지 (Test Coverage)**: 30 / 30 단위·통합·스트레스 테스트 100% PASS.
