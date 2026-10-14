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
| `dedupe_key` | 선택·nullable. 1–128자, `^[A-Za-z0-9._:-]+$`. 중복 제거용 불투명 해시 식별자(원문 내용 금지), non-null 유일 부분 인덱스 |
| `session_id` | 인덱스, 선택 |
| `account_id` | 선택·nullable. `<tool>:<id>`, 최대 64자, `[A-Za-z0-9:_-]`. tool은 `claude_code`, `codex`, `antigravity`, `copilot`, `cursor`, `api`. `(account_id, timestamp)` 복합 인덱스 |
| `timestamp` | 인덱스. 미전달 시 서버 시각. 입력 오프셋은 UTC로 변환 후 naive 저장. 비용은 이 기록 시각에 유효한 단가로 계산한다 ([cost.md](cost.md)의 시행일) |
| `provider` | 인덱스, 1–64자 |
| `model_name` | 인덱스, 1–200자 |
| `prompt_tokens` | 캐시되지 않은 입력 토큰. 음수는 0으로 보정 |
| `completion_tokens` | 출력 토큰. 음수는 0 |
| `cache_read_tokens` / `cache_write_tokens` | 캐시 읽기/쓰기 토큰. 음수는 0 |
| `cache_write_1h_tokens` | 전체 캐시 쓰기 중 1시간 TTL 부분집합, 0 이상·전체 쓰기 이하로 보정 |
| `total_tokens` | 미전달 시 위 네 값의 합 |
| `latency_ms` / `latency_measured` | 입력 생략·null은 미측정. DB는 0 + false, 응답은 null. 명시적 0ms는 true |
| `cost_usd` / `cost_source` | [cost.md](cost.md). `unknown`이면 `cost_usd=0.0`이지만 금액으로 취급하지 않는다 |
| `status` | `SUCCESS`·`ERROR`·`TIMEOUT`·`RATE_LIMITED` (대소문자 무시, 그 외 422) |
| `error_message` | 최대 `MAX_ERROR_MESSAGE_CHARS`(2000)자로 절단 |
| `tags` | 최대 `MAX_TAGS`(20)개, 각 64자, 쉼표 구분 저장 |
| `metadata_json` | 정제 후 직렬화, 최대 `MAX_METADATA_BYTES`(8192) |

메타데이터의 dict·list를 모두 재귀 순회하며, 중첩 리스트 안에서도 내용성 키는 대소문자와
무관하게 `[redacted]`로 바꾼다. 컨테이너마다 첫 50개 항목만 보존하고 루트 깊이 0에서
깊이 6을 넘는 컨테이너는 절단 표식으로 바꾼다. 저장 실패 로그에는 안전한 오류 코드·예외
클래스·건수만 남기며 예외 문구·체인·SQL 매개변수는 기록하지 않는다.

모든 토큰 필드와 미전달 시 파생하는 `total_tokens`의 상한은 1,000,000,000(10억)이며
초과하면 단건·배치 모두 422로 거부한다. 음수 보정 후 네 구성 필드의 합을 검사하며,
1시간 캐시 쓰기는 전체 쓰기의 부분집합이므로 합에 다시 더하지 않는다. 각 카운터의
SQLite signed 64-bit SUM이 넘치려면 최대값 행만 9,223,372,037건 이상 누적되어야 하므로
로컬 앱의 현실적인 저장 규모에서 정수 오버플로를 방지한다.

`cost_usd`·`latency_ms`는 유한 float만 허용한다. NaN·±Infinity와 float 범위를 넘는
`1e309` 등은 422이며, 음수 유한 값의 기존 보정 정책은 유지한다. UTC 정규화 결과가
지원 시각 범위를 벗어나면 수집·조회·CSV 기간 필터 모두 422로 거부한다.
조회·CSV의 날짜 범위 오류는 일반 쿼리 검증과 같은 `detail` 배열에 `loc=["query", 필드명]`·
`type=datetime_parsing`·고정 `msg="Invalid field value"`만 반환한다. 잘못된 상태 필터도 같은
배열에 `loc=["query", "status"]`·`type=value_error`·고정 메시지를 반환한다. 입력 원문·검증
문맥을 포함하지 않으며 OpenAPI `HTTPValidationError` 스키마를 따른다.

수집 트랜잭션은 flush·refresh·응답 모델 검증을 모두 마친 뒤 커밋한다. 그 단계의
실패는 단건·배치 전체를 롤백한다. 커밋 후 네트워크 단절까지 되돌릴 수는 없다.

### 멱등 수집

`dedupe_key`가 같으면 단건·배치 모두 기존 행을 그대로 반환하고 새 행을 만들지
않는다. 같은 배치 안의 중복도 한 번만 저장하며 응답 목록은 입력 순서·길이를
유지한다. 중복도 신규와 같은 201을 반환한다. SQLite·PostgreSQL의 유일 부분 인덱스와
`INSERT ... ON CONFLICT DO NOTHING`으로 동시 요청을 보호한다. 중복 키를 다른 내용에
재사용하면 처음 저장된 내용이 유지된다. 기존 DB는 `ensure_schema`가 nullable 컬럼과
유일 부분 인덱스를 추가한다. CSV에는 이 키를 내보내지 않는다.

`dedupe_key` 생략·null은 기존 중복 허용 동작을 유지한다. `trace_id`만 같아도 중복
제거하지 않으며, 수집기는 재시도할 때 같은 이벤트의 키를 유지해야 한다.

### 수집 한도

| 항목 | 기본값 | 위반 시 |
| :--- | :--- | :--- |
| `MAX_REQUEST_BYTES` | 8 MiB | 본문을 읽기 전 `Content-Length`로 413 |
| `MAX_BATCH_SIZE` | 500건 | 422 |

### 조회

- 필터: `provider`, `model_name`, `status`, `session_id`, `account_id`, `start_time`, `end_time`. 잘못된 `status`·`account_id`는 422 (빈 결과로 숨기지 않음).
- 정렬: `timestamp DESC, id DESC` — 같은 시각 행이 페이지 경계에서 중복되지 않도록 `id`를 2차 키로 쓴다.
- `limit` 기본 50(1–500), `offset` ≥ 0.
- 단건 조회도 `timestamp DESC, id DESC LIMIT 1`로 최신 행 한 건을 선택한다.

### CSV 내보내기

- 스트림 시작에 UTF-8 BOM을 한 번 붙여 Excel에서 한글을 인식하게 한다. 프로그램에서 읽을 때는 `utf-8-sig`로 디코딩한다.
- 필터: `provider`, `model_name`, `status`, `account_id`, `start_time`, `end_time`을 함께 적용한다. `status`는 조회 API와 동일한 `VALID_STATUSES`를 사용해 공백 제거·대문자 변환 후 검증하며, 잘못된 값은 같은 422 오류로 거부한다 (빈 문자열은 status 필터 생략).
- 기간 경계는 양쪽 모두 포함한다 (`timestamp >= start_time`, `timestamp <= end_time`). 오프셋이 있는 입력 시각은 `timeutil.to_naive_utc`로 변환해 비교한다.
- 컬럼: `id, trace_id, timestamp, session_id, provider, model_name, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens, total_tokens, latency_ms, cost_usd, cost_source, status, tags, account_id`. 계정 미상은 마지막 열을 빈 칸으로 둔다.
- `latency_measured=False`이면 `latency_ms`는 **빈 칸**으로 내보내며, 측정된 `0ms`와 양수 지연 시간은 숫자로 유지한다.
- `error_message`·`metadata_json`은 **내보내지 않는다** — 내용이 시스템 밖으로 나가는 가장 쉬운 경로다.
- `cost_source='unknown'`이면 `cost_usd`는 `0`이 아니라 **빈 칸**.
- 호출자가 정하는 문자열(`trace_id`, `session_id`, `provider`, `model_name`, `tags`)이 `=` `+` `-` `@` 탭·CR로 시작하면 앞에 `'`를 붙인다 — 스프레드시트에서 수식으로 실행되지 않게 (OWASP CSV injection). 숫자 컬럼은 그대로.
- `limit` 기본 10,000(최대 100,000), 500행 단위 스트리밍.
- `timestamp DESC, id DESC`로 최신 행부터 상한을 적용한다. `X-Total-Count`는 필터에 맞는 전체 건수이며, 그 건수가 `limit`보다 클 때만 `X-Export-Partial: true`를 반환한다 (그 외 `false`). HTTP 클라이언트는 `X-Total-Count`와 요청 상한으로도 잘림 여부를 확인할 수 있다.

### 계정 식별

`GET /traces`와 `/traces/export.csv`는 `account_unknown=true`로 `account_id IS NULL`인 기록만 조회한다. 생략 또는 `false`는 추가 필터가 없다. `account_id`와 `account_unknown=true`를 함께 보내면 HTTP 422의 `detail` 배열에 `loc=["query", "account_unknown"]`·`type=value_error`·고정 `msg="Invalid field value"`만 반환한다.

계정 ID는 도구 로그인 고유값의 SHA-256 hex 앞 12자, 로그인 고유값이 없는 폴더는 `dir-`와 절대경로 SHA-256 hex 앞 12자로 수집자가 만든다. 서버는 원문 로그인 값·이메일·토큰을 받지 않으며 `null` 기록을 다른 계정에 붙이지 않는다. 사용자 계정 label은 데스크톱 설정에서만 보관한다.

## Open questions

- SDK 자동 재시도 → 이월: 재시도를 넣을 때는 수집기와 같이 안정적인 `dedupe_key`를 전달해야 한다.

## Areas of concern

- CSV의 `'` 접두사 때문에 CSV를 프로그램으로 다시 읽으면 해당 값이 원본과 다르다. 스프레드시트 안전을 우선했다.
- 해결됨(v0.5.0): CSV 수식 주입.
- 음수 토큰은 거부하지 않고 0으로 보정한다. 잘못된 연동이 조용히 묻힐 수 있다 — 의도된 관용 정책으로 유지.
