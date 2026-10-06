# Spec: AIMONITOR (v0.3.0)

[intent.md](../intent.md)의 의도를 기능별 규칙과 수치로 바꾼 명세다. 각 파일은
`Requirements / Design / Open questions / Areas of concern` 구조를 따른다.
코드와 다르면 둘 중 하나가 틀린 것이다 — 고칠 때 같은 커밋에서 맞춘다.

| 파일 | 범위 | 주요 코드 |
| :--- | :--- | :--- |
| [ingestion.md](ingestion.md) | 트레이스 수집 API, 이벤트 스키마, 조회·CSV | `routers/traces.py`, `models/trace.py`, `core/sanitize.py` |
| [cost.md](cost.md) | 비용 계산, 단가표, 캐시 단가 | `services/cost.py` |
| [metrics.md](metrics.md) | KPI·시계열·모델별 집계 | `services/aggregator.py`, `routers/metrics.py` |
| [alerts.md](alerts.md) | 알림 규칙, 평가, 쿨다운, Webhook | `services/alert.py`, `models/alert.py`, `services/scheduler.py` |
| [dashboard.md](dashboard.md) | React 대시보드 | `frontend/src/` |
| [security-ops.md](security-ops.md) | 인증, 원문 보호, 보관, 스키마 업그레이드, 배포 | `core/security.py`, `db/session.py`, `services/retention.py`, `Dockerfile` |
| [sdk.md](sdk.md) | Python SDK | `sdk/python/aimonitor/client.py` |

## 공통 규칙

- **시각**: 저장은 naive UTC, API 응답은 `+00:00`이 붙은 ISO-8601. 입력 오프셋은 UTC로 변환 후 저장.
- **정직한 숫자**: 모르는 값은 0으로 채우지 않는다. 비용은 `계산 불가`, 표본 상한에 걸린 값은 `latency_sampled`/`partial`로 표시.
- **원문 미저장**: 프롬프트·응답 텍스트는 DB·로그·오류 응답·CSV 어디에도 남기지 않는다.
- **API 접두사**: `/api/v1`. 쓰기 엔드포인트는 API 키 필수(설정 시), 읽기는 `REQUIRE_AUTH_FOR_READS`로 선택.
- **설정**: 모든 값은 환경 변수(`backend/app/core/config.py`, `.env.example`)이며 안전한 기본값을 가진다.

## Open questions (intent.md 질문별)

| intent의 질문 | 처리 |
| :--- | :--- |
| PostgreSQL 정식 지원? | 이월: dialect 컴파일 테스트(`test_dialect_compat.py`)까지만. 실서버 검증은 수요가 생기면 진행. |
| 사용자별 계정·권한? | 이월: 단일 공유 키로 소규모 팀 요구는 충족. 다중 테넌트 요구가 생기면 새 intent로. |
| 단가표를 설정/DB로? | 이월: 현재 코드 상수. 단가 갱신 주기가 짧아 문제가 되고 있음 → [cost.md](cost.md) Areas of concern, [plan.md](../plan.md) 참고. |
| WebSocket·알림 채널 확장? | 이월: 5초 폴링, Webhook(Slack/Discord)만. 요청 속도 제한은 리버스 프록시 계층으로 결정. |
