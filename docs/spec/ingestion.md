# Spec: 수집·조회 (Ingestion)

## Requirements

- ING-1 단건 `POST /api/v1/traces`, 배치 `POST /api/v1/traces/batch`로 LLM 호출 1건 이상을 기록한다 (201).
- ING-2 배치는 한 트랜잭션이다. 저장 중 오류가 나면 전부 롤백하고 일반 메시지의 500을 반환한다.
- ING-3 수집 시 비용을 계산해 `cost_usd`·`cost_source`를 함께 저장한다 ([cost.md](cost.md)).
- ING-4 메타데이터의 내용성 키는 저장 전에 `[redacted]`로 치환한다 ([security-ops.md](security-ops.md)).
- ING-5 `GET /traces`는 필터·기간·페이지네이션을 지원하고 전체 건수를 `X-Total-Count` 헤더로 준다.
- ING-6 `GET /traces/{trace_id}` 단건 조회, 없으면 404.
- ING-7 `GET /traces/export.csv`로 비용 정산용 CSV를 스트리밍한다.

## Design

### 이벤트 스키마 (`trace_events`)

| 컬럼 | 규칙 |
| :--- | :--- |
| `id` | PK, 자동 증가 |
| `trace_id` | 인덱스. 미전달 시 UUID4 발급 |
| `session_id` | 인덱스, 선택 |
| `timestamp` | 인덱스. 미전달 시 서버 시각. 입력 오프셋은 UTC로 변환 후 naive 저장 |
| `provider` | 인덱스, 1–64자 |
| `model_name` | 인덱스, 1–200자 |
| `prompt_tokens` | 캐시되지 않은 입력 토큰. 음수는 0으로 보정 |
| `completion_tokens` | 출력 토큰. 음수는 0 |
| `cache_read_tokens` / `cache_write_tokens` | 캐시 읽기/쓰기 토큰. 음수는 0 |
| `total_tokens` | 미전달 시 위 네 값의 합 |
| `latency_ms` | ≥ 0 |
| `cost_usd` / `cost_source` | [cost.md](cost.md). `unknown`이면 `cost_usd=0.0`이지만 금액으로 취급하지 않는다 |
| `status` | `SUCCESS`·`ERROR`·`TIMEOUT`·`RATE_LIMITED` (대소문자 무시, 그 외 422) |
| `error_message` | 최대 `MAX_ERROR_MESSAGE_CHARS`(2000)자로 절단 |
| `tags` | 최대 `MAX_TAGS`(20)개, 각 64자, 쉼표 구분 저장 |
| `metadata_json` | 정제 후 직렬화, 최대 `MAX_METADATA_BYTES`(8192) |

### 수집 한도

| 항목 | 기본값 | 위반 시 |
| :--- | :--- | :--- |
| `MAX_REQUEST_BYTES` | 8 MiB | 본문을 읽기 전 `Content-Length`로 413 |
| `MAX_BATCH_SIZE` | 500건 | 422 |

### 조회

- 필터: `provider`, `model_name`, `status`, `session_id`, `start_time`, `end_time`. 잘못된 `status`는 422 (빈 결과로 숨기지 않음).
- 정렬: `timestamp DESC, id DESC` — 같은 시각 행이 페이지 경계에서 중복되지 않도록 `id`를 2차 키로 쓴다.
- `limit` 기본 50(1–500), `offset` ≥ 0.

### CSV 내보내기

- 컬럼: `id, trace_id, timestamp, session_id, provider, model_name, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens, total_tokens, latency_ms, cost_usd, cost_source, status, tags`.
- `error_message`·`metadata_json`은 **내보내지 않는다** — 내용이 시스템 밖으로 나가는 가장 쉬운 경로다.
- `cost_source='unknown'`이면 `cost_usd`는 `0`이 아니라 **빈 칸**.
- `limit` 기본 10,000(최대 100,000), 500행 단위 스트리밍.

## Open questions

- 동일 `trace_id` 재전송 시 중복 저장 → 이월: 현재는 중복 허용. SDK 재시도가 생기면 멱등 키 도입 필요.

## Areas of concern

- **CSV 수식 주입**: `model_name`·`tags`·`session_id`는 호출자가 정하는 문자열인데, `=`·`+`·`-`·`@`로 시작하는 값을 이스케이프하지 않는다. 스프레드시트에서 열면 수식으로 실행된다. → [plan.md](../plan.md) 후속 항목.
- 음수 토큰은 거부하지 않고 0으로 보정한다. 잘못된 연동이 조용히 묻힐 수 있다 — 의도된 관용 정책으로 유지.
