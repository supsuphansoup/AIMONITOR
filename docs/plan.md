# Plan: v0.5.0 — 알림·내보내기·집계 정합성, 단가표 갱신

Status: in progress (2026-10-06 승인: "다음 작업도 순서대로 진행"). 이전 계획(v0.4.0)은 git 이력과 [CHANGELOG](../CHANGELOG.md).
근거: 각 spec의 Areas of concern. 아래 순서대로 하나씩 테스트 → 구현 → 커밋한다.

| # | 항목 | spec | 결정 |
| :--- | :--- | :--- | :--- |
| 1 | 빈 시간 창에서 알림 오발동 | [alerts](spec/alerts.md) | 창에 요청이 0건이면 **비율·지연 지표**(에러율, 평균/P50/P95/P99)는 평가하지 않는다. 건수·합계 지표(요청 수, 비용, 토큰)는 0이 실제 값이므로 평가한다 — 무트래픽 감시(`total_requests < 1`)는 계속 동작. |
| 1b | 비용 규칙이 미상 비용을 모름 | [alerts](spec/alerts.md) | 창에 `계산 불가` 건이 있으면 알려진 비용은 **하한값**이다. `>`·`>=`는 하한이 넘으면 실제도 넘으므로 평가하고 메시지에 "단가 미상 N건 제외"를 붙인다. `<`·`<=`·`==`·`!=`는 판정할 수 없으므로 건너뛴다. |
| 2 | CSV 수식 주입 | [ingestion](spec/ingestion.md) | 문자열 컬럼 값이 `=` `+` `-` `@` 탭·CR로 시작하면 앞에 `'`를 붙인다 (OWASP 권고). 숫자 컬럼은 건드리지 않는다. |
| 3 | v0.1.0 잔존 추정 비용이 합계에 섞임 | [metrics](spec/metrics.md) | 저장값은 바꾸지 않고(파괴적 마이그레이션 금지) **집계에서** `cost_source='unknown'` 행의 비용을 0으로 본다. overview·timeseries·breakdown 모두. |
| 4 | 운영 DB에 데모 트래픽 | [dashboard](spec/dashboard.md) | `/system/info`의 `environment`가 `production`이면 데모 버튼을 숨긴다. 정보를 못 받으면(인증 실패 등) 숨긴다 — 기본은 안전한 쪽. |
| 5 | 단가표 노후·기준일 없음 | [cost](spec/cost.md) | 공식 가격 페이지에서 **확인된 모델만** 추가한다. 확인 못 한 모델은 넣지 않는다(`계산 불가` 유지). 단가표 기준일 `PRICING_AS_OF`를 두고 `/system/pricing`에 공개. |

## Files that change

- 1: `backend/app/services/alert.py`, `backend/app/core/metrics_catalog.py`, `backend/tests/test_alert_lifecycle.py`
- 2: `backend/app/routers/traces.py`, `backend/tests/test_export.py`
- 3: `backend/app/services/aggregator.py`, `backend/tests/test_cost_source.py`
- 4: `frontend/src/App.tsx`, `frontend/src/components/Header.tsx`, 새 `frontend/src/components/Header.test.tsx`
- 5: `backend/app/services/cost.py`, `backend/app/routers/system.py`, `backend/tests/test_cost_source.py`, `backend/tests/test_system.py`
- 공통: 해당 `docs/spec/*.md`, `CHANGELOG.md`, 버전 문자열(0.5.0)

## Order of work

1. 알림(1, 1b) → 2. CSV → 3. 집계 → 4. 대시보드 → 5. 단가표. 항목마다 실패 테스트 먼저, 통과 후 spec 갱신·커밋.
6. 버전 0.5.0, CHANGELOG, README.

## Risks

- **1이 가장 깨지기 쉽다.** 지표를 "비율/지연"과 "건수/합계"로 나누는 기준을 카탈로그 한 곳에 두지 않으면 별칭(`p95`, `latency` 등)이 새어 나간다. 별칭 전부를 테스트로 확인한다.
- 1b로 `<` 비용 규칙이 미상 건이 있는 동안 침묵한다. 의도된 동작이며 spec에 명시한다.
- 2의 `'` 접두사는 CSV를 다시 읽는 프로그램에서 값이 달라 보인다. 스프레드시트 안전을 우선한다.
- 3은 v0.1.0 DB를 업그레이드한 경우에만 숫자가 바뀐다 (합계가 줄어듦 = 정상화).
- 5는 외부 가격 정보에 의존한다. 출처 URL과 확인일을 코드 주석에 남기고, 불확실하면 넣지 않는다.

## Proof

```bash
python -m pytest -q
ruff check .
cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

- 기준선(2026-10-06): 백엔드 `179 passed, 1 skipped`.
