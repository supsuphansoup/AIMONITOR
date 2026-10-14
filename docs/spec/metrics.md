# Spec: 집계 (Metrics)

## Requirements

- MET-1 `GET /metrics/overview`: 기간 내 KPI 요약 (요청 수, 성공/에러 수, 에러율, 평균·P50·P95·P99 지연, 토큰, 비용).
- MET-2 `GET /metrics/timeseries`: 고정 간격 버킷별 요청 수·에러·지연·비용·토큰.
- MET-3 `GET /metrics/breakdown`: 모델·provider별 요청 수·비용 점유.
- MET-4 근사치는 근사치라고 밝힌다. 비용 미상 건수(`cost_unknown_count`)와 표본 여부(`latency_sampled`, `partial`)를 함께 반환한다.
- MET-5 데이터가 많아도 데스크톱 API 폴링 한 번이 프로세스를 멈추게 하지 않는다.
- MET-6 `latency_measured=false` 행은 평균·백분위에서 제외하고 overview에 `latency_measured_count`를 반환한다. 요청은 있으나 측정값이 없으면 지연은 `null`이다. 지연 표본 여부는 측정 행 수를 기준으로 판단한다.
- MET-7 `GET /metrics/active_contexts`: 데스크톱 도구별 최신 컨텍스트와 5시간·오늘·7일·이번 달 사용량, 계정별 항목([accounts.md](accounts.md)).
- MET-8 `GET /metrics/connected_ais`는 백엔드 PC에서 감지한 연결 AI별 공식 한도·최신 컨텍스트·로컬 사용량·공식 청구/잔액을 반환한다. 읽기 인증은 기존 `require_read_auth`를 따른다.

## Design

### 파라미터

| API | 파라미터 | 기본·범위 |
| :--- | :--- | :--- |
| overview | `hours_back` | 생략 시 전체 기간, 1–8760. 명시적 `start_time`이 우선 |
| overview | `provider`, `model_name`, `account_id`, `start_time`, `end_time` | 선택 |
| timeseries | `interval_minutes` / `hours_back` | 5 (1–1440) / 24 (1–8760) |
| timeseries | `provider`, `model_name`, `account_id` | 선택 |
| breakdown | `hours_back`, `provider`, `account_id` | 24 (1–8760), 필터 선택 |

`overview`·`timeseries`·`breakdown`은 `account_unknown=true`로 계정 미상(`account_id IS NULL`) 기록만 집계한다. 생략 또는 `false`는 추가 필터가 없다. `account_id`와 `account_unknown=true`의 동시 지정은 기존 `detail` 배열 형식의 HTTP 422이며 입력 원문은 반환하지 않는다.

### 계산 방식

- 건수·합계·평균: SQL `COUNT`/`SUM`/`AVG`, 모델별은 `GROUP BY` — 행을 ORM 객체로 올리지 않는다.
- 백분위수: `latency_ms` 컬럼만 **최신순(`DESC`)으로 최대 `MAX_AGGREGATION_ROWS`(200,000)행** 조회 후 선형 보간(numpy 기본과 동일). 상한에 걸리면 `latency_sampled: true`.
- 시계열: 상한+1행을 최신순으로 읽어 실제 초과 여부를 확인한 뒤 같은 상한을 적용한다. 정확히 상한과 같으면 `partial: false`다. 잘리는 쪽은 항상 과거이며, 잘린 가장 오래된 버킷에 `partial: true`.
- 버킷 경계: UTC 자정 기준 경과 분을 `interval_minutes`로 내림. 60의 약수가 아닌 간격도 겹치지 않는다.
- 에러율 = `error_count / total_requests × 100`. `SUCCESS` 외 상태(`ERROR`, `TIMEOUT`, `RATE_LIMITED`)는 모두 에러로 센다.
- 비용 합계는 `SUM(CASE WHEN cost_source='unknown' THEN 0 ELSE cost_usd END)`(`KNOWN_COST`)이다. overview·timeseries·breakdown 모두 같다. 미상 건수는 `cost_unknown_count`로 따로 준다. 저장값은 바꾸지 않는다.
- 시계열의 `cost_unknown_count`도 버킷별 정수다. 전부 미상인 0원 소계와 실제 무료·알려진 0원은 미상 건수로 구별한다. 일부 미상은 알려진 소계와 미상 건수를 함께 반환한다.

### 도구별 사용량 (`/metrics/active_contexts`)

- 정확한 쉼표 구분 태그 `claude-code`·`codex`·`copilot`·`antigravity`·`cursor`로 도구를 구분하며, 나머지는 `<provider>_api`로 묶는다.
- 오늘·이번 달 경계는 백엔드 PC의 로컬 시각, 응답 시각은 UTC 오프셋 ISO-8601이다. SQL 합계에서 미상 비용을 제외하고 `cost_unknown`을 반환한다.
- 최신 요청은 `timestamp DESC, id DESC`로 선택하며 미래 시각 행을 제외한다. Copilot 세션 합계와 Antigravity 추정 토큰은 컨텍스트 크기로 쓰지 않는다.
- 도구별 합계·기존 필드를 유지하고 `accounts` 배열에 계정별로 같은 창/컨텍스트 구조를 둔다. `null` 계정은 별도 항목이다.

### 연결 AI (`/metrics/connected_ais`)

- `connected_ais`는 도구별 합계·기존 필드를 유지하며 각 항목의 `accounts`에 계정별 `usage`, `context`, `last_request_at` 등을 추가한다. 전체 합계에 하위 계정을 다시 더하지 않는다. 도구 기본 자격증명의 공식 한도·청구액을 다른 계정에 복사하지 않는다.
- `CLAUDE_CONFIG_DIR`·`CODEX_HOME`, 홈의 `.claude-*`·`.codex-*`, 기존 계정 파일에 수동 등록한 폴더도 읽기 전용으로 발견한다. 기본 폴더 없이 사용자 지정 폴더만 있으면 도구를 표시하되 기본 자격증명의 플랜·공식 한도를 대신 붙이지 않는다. 계정별 Claude·Codex 플랜은 확인된 계정별 정보가 없으면 `플랜 확인 불가`다.
- 계정별 `quota_windows`는 데스크톱 소유 계정·타임라인의 읽기 전용 스냅샷을 통해 조회한다. 계정 파일 부재·읽기 실패 시 `null`이고 HTTP 200과 로컬 사용량은 유지한다. 공식 조회 불가 시 값이 없는 `unavailable` 창 또는 `null`이다. 백엔드는 `accounts.json`·`account_timeline.json`을 생성하거나 갱신하지 않는다.

- 파라미터 없음. `{items, by_key, total, hidden}` 구조이며 `items`에는 `key`, `display_name`, `provider`, `plan_name`, `cost_meaning`, `quota_windows`, `context`, `usage`, `last_request_at`, `session_status`, `billing_note`, `official_spend`가 있다. 빈 연결 목록은 `items=[]`다.
- 도구 감지 실패는 대상별로 격리한다. 실패 항목은 `detection_status='unavailable'`, `plan_name='감지 불가'`로 남기며 정상 항목은 `detection_status='available'`이다. 감지기의 연결·구독 상태는 실패 시 `null`이며 미설치(`false`)와 구별한다. 실패 대상의 공식 한도·청구를 추측하지 않는다.
- `MetricAggregator.get_active_contexts`를 그대로 재사용한다. 사용 창은 5시간·오늘·7일·이번 달이며 오늘/월 경계는 백엔드 PC의 로컬 시각이다. SQL 합계에는 표본 상한이 없고 최신 컨텍스트만 `timestamp DESC, id DESC LIMIT 1`로 선택한다. 미래 행은 제외하며 정확한 도구 태그로 구분한다.
- 연결 감지·한도·요약은 기존 `desktop.ai_detector`/`desktop.context_manager`를 재사용하며 데스크톱 파일을 변경하지 않는다. 데스크톱 설정의 숨김 목록도 반영한다. 공식 청구/잔액은 기존 `spend_service`의 15분 캐시를 사용한다.
- `context`와 `official_spend`는 없으면 `null`, 공식 한도의 `remaining_pct`도 모르면 `null`이다. `usage`의 `cost_unknown`이 요청 수와 같고 0보다 크면 비용은 `cost_usd=null, cost_krw=null`이다. 일부 미상은 알려진 소계와 미상 건수를 반환한다. `total.billed_*`는 로컬 기록의 알려진 비용 소계로 공식 청구액이 아니며 `billed_unknown_month`를 함께 읽어야 한다.
- 모든 응답 시각(`last_request_at`, 컨텍스트/한도의 `as_of`, `resets_at`, 청구액의 `as_of`, `period_start`)은 공통 timeutil로 UTC 오프셋 ISO-8601을 만든다. 로컬 경로·감지기의 예외 상세·공급자 조회 오류 상세는 응답에서 제외한다. 키·프롬프트·응답·세션 원문을 추가로 저장하거나 반환하지 않는다.

## Open questions

- 사전 집계(롤업 테이블) 도입 → 이월: 현재 규모(수십만 행)에서는 SQL 집계로 충분. 장기간 API 조회가 느려지면 재검토.

## Areas of concern

- v0.1.0에서 업그레이드한 DB에는 임의 단가로 채운 `cost_usd`가 `unknown` 행에 여전히 저장돼 있다. 집계·CSV는 무시하지만 `GET /traces` 원본 응답에는 그대로 보인다 (클라이언트는 `cost_source`를 보고 `계산 불가`로 표시해야 한다).
- 해결됨(v0.5.0): 잔존 추정 비용이 합계에 섞이던 문제.
- 30일 범위(720시간)는 트래픽이 많으면 거의 항상 표본 상한에 걸린다. API의 표본 플래그를 읽고 장기 추세가 근사치임을 표시해야 한다.
- 버킷이 UTC 자정 기준이라 KST 사용자에게 360분(30D) 버킷 경계가 09:00/15:00/21:00/03:00이 된다. 의도된 동작으로 둔다.
