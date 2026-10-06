# Spec: 보안·운영 (Security & Operations)

## Requirements

- OPS-1 API 키로 쓰기 엔드포인트를 보호하고, 필요하면 읽기도 보호한다. 헬스체크는 항상 공개.
- OPS-2 프롬프트·응답 원문은 DB·로그·오류 응답·CSV 어디에도 남기지 않는다.
- OPS-3 보관 기간을 지나면 트레이스와 알림 이력을 자동 삭제할 수 있다. 기본은 무제한 (업그레이드만으로 데이터를 잃지 않음).
- OPS-4 이전 버전 DB를 그대로 두고 업그레이드해도 기동된다 (누락 컬럼·인덱스 자동 추가, 데이터 보존).
- OPS-5 단일 컨테이너로 API와 대시보드를 서빙하고, liveness/readiness를 분리한다.

## Design

### 인증 (`core/security.py`)

- `X-API-Key: <키>` 또는 `Authorization: Bearer <키>`, `hmac.compare_digest`로 비교.
- `API_KEY` 미설정 = 개방 모드 + 시작 시 경고 로그. 빈 문자열도 미설정으로 본다.
- `REQUIRE_AUTH_FOR_READS=true`면 조회 API도 키 필요. 공개: `/health`, `/api/v1/system/ready`.

### 원문 보호 (`core/sanitize.py`)

- 메타데이터 키 이름에 `prompt, completion, response, message, content, answer, question, query, input_text, output_text, system, choices, transcript, document, context_text`가 포함되면 값을 재귀적으로 `[redacted]`.
- 요청 본문은 로그에 남기지 않는다. 접근 로그는 메서드·경로·상태·소요 시간·`X-Request-ID`만.
- 500 응답은 일반 메시지만 반환하고 예외는 서버 로그로 (`raise ... from None`).

### HTTP 계층 (`main.py`)

- 미들웨어 순서: **CORS가 최외곽** — 오류 응답에도 CORS 헤더가 붙는다.
- `CORS_ORIGINS`에 `*`가 있으면 credentials 자동 비활성화. 노출 헤더: `X-Request-ID`, `X-Total-Count`.
- 모든 요청에 `X-Request-ID` (전달값 또는 16자 hex 발급).

### 보관·정리 (`services/retention.py`)

- `RETENTION_DAYS`(기본 0 = 무제한) > 0이면 스케줄러가 `RETENTION_INTERVAL_SECONDS`(3600)마다 기준일 이전 `trace_events`·`alert_histories` 삭제.
- 수동: `POST /api/v1/system/purge?days=N` (쓰기 인증). `days ≤ 0`은 아무것도 지우지 않는다.

### 스키마 업그레이드 (`db/session.py: ensure_schema`)

- 모델 메타데이터와 실제 스키마를 비교해 **누락 컬럼은 `ALTER TABLE ADD COLUMN`, 누락 인덱스는 `CREATE INDEX`.** 삭제·타입 변경은 하지 않으며 멱등.
- 모델 import를 명시적으로 수행(`_load_model_metadata`)해 import 순서에 의존하지 않는다.
- SQLite: WAL, `busy_timeout=15000`, `PRAGMA foreign_keys=ON`.

### 배포

- Dockerfile multi-stage: `node:22-alpine`에서 프론트 빌드 → `python:3.11-slim` 런타임, non-root `aimonitor` 사용자.
- `docker-compose.yml`: 포트 `127.0.0.1:8000`만 바인딩, DB는 볼륨 `/app/data`, readiness 헬스체크.
- 시스템 API: `GET /system/info`(버전·인증 여부·스케줄러 상태), `GET /system/pricing`, `GET /system/ready`(DB 확인), `GET /health`(DB 미접근).
- CI(`.github/workflows/ci.yml`): pytest(Python 3.10/3.11/3.12), ruff, 프론트 typecheck·test·build, Docker 빌드 후 readiness 확인.

## Open questions

- 요청 속도 제한 → 결정: 앱에 넣지 않는다. 모니터링 데이터를 조용히 버리는 부작용이 커서 리버스 프록시 계층에 둔다.
- 사용자별 계정·권한 → 이월 (intent Open questions).

## Areas of concern

- 원문 차단이 **키 이름 기반**이다. `{"note": "<프롬프트 원문>"}`처럼 무해한 키에 원문을 넣으면 통과한다. `error_message`도 업스트림 오류에 프롬프트 일부가 섞이면 그대로 저장된다(2000자 절단만).
- 단일 공유 키라 키를 바꾸면 모든 SDK·대시보드를 동시에 갱신해야 한다. 키 회전 절차가 문서화돼 있지 않다.
- 다중 워커(`uvicorn --workers N`)에서는 각 워커가 스케줄러를 띄운다. 알림 중복은 CAS로 막지만 보관 작업은 N번 실행된다(멱등이라 결과는 같음).
