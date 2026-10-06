# Spec: 집계 (Metrics)

## Requirements

- MET-1 `GET /metrics/overview`: 기간 내 KPI 요약 (요청 수, 성공/에러 수, 에러율, 평균·P50·P95·P99 지연, 토큰, 비용).
- MET-2 `GET /metrics/timeseries`: 고정 간격 버킷별 요청 수·에러·지연·비용·토큰.
- MET-3 `GET /metrics/breakdown`: 모델·provider별 요청 수·비용 점유.
- MET-4 근사치는 근사치라고 밝힌다. 비용 미상 건수(`cost_unknown_count`)와 표본 여부(`latency_sampled`, `partial`)를 함께 반환한다.
- MET-5 데이터가 많아도 대시보드 폴링 한 번이 프로세스를 멈추게 하지 않는다.

## Design

### 파라미터

| API | 파라미터 | 기본·범위 |
| :--- | :--- | :--- |
| overview | `hours_back` | 생략 시 전체 기간, 1–8760. 명시적 `start_time`이 우선 |
| overview | `provider`, `model_name`, `start_time`, `end_time` | 선택 |
| timeseries | `interval_minutes` / `hours_back` | 5 (1–1440) / 24 (1–8760) |
| breakdown | `hours_back`, `provider` | 24 (1–8760) |

### 계산 방식

- 건수·합계·평균: SQL `COUNT`/`SUM`/`AVG`, 모델별은 `GROUP BY` — 행을 ORM 객체로 올리지 않는다.
- 백분위수: `latency_ms` 컬럼만 **최신순(`DESC`)으로 최대 `MAX_AGGREGATION_ROWS`(200,000)행** 조회 후 선형 보간(numpy 기본과 동일). 상한에 걸리면 `latency_sampled: true`.
- 시계열: 같은 상한을 최신순으로 적용한다. 잘리는 쪽은 항상 과거이며, 잘린 가장 오래된 버킷에 `partial: true`.
- 버킷 경계: UTC 자정 기준 경과 분을 `interval_minutes`로 내림. 60의 약수가 아닌 간격도 겹치지 않는다.
- 에러율 = `error_count / total_requests × 100`. `SUCCESS` 외 상태(`ERROR`, `TIMEOUT`, `RATE_LIMITED`)는 모두 에러로 센다.
- 비용 합계는 `SUM(cost_usd)`이다. 미상 행은 `cost_usd=0`으로 저장되므로 합에 기여하지 않고, 건수는 `cost_unknown_count`로 따로 준다.

## Open questions

- 사전 집계(롤업 테이블) 도입 → 이월: 현재 규모(수십만 행)에서는 SQL 집계로 충분. 30D 화면이 느려지면 재검토.

## Areas of concern

- v0.1.0 DB를 업그레이드한 경우, 당시 임의 단가로 채운 `cost_usd`가 `cost_source='unknown'`인 채로 남아 **합계에 섞인다.** "미상 행은 0" 전제가 깨진다. 집계에서 `unknown` 행을 명시적으로 제외할지 결정 필요.
- 30D 범위(720시간)는 트래픽이 많으면 거의 항상 표본 상한에 걸린다. 화면에는 표시되지만 장기 추세가 근사치라는 점을 사용자가 놓치기 쉽다.
- 버킷이 UTC 자정 기준이라 KST 사용자에게 360분(30D) 버킷 경계가 09:00/15:00/21:00/03:00이 된다. 의도된 동작으로 둔다.
