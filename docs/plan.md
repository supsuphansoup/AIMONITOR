# Plan: v0.4.0 — 비용 정확도

Status: draft (승인 대기). 근거: [spec/cost.md](spec/cost.md), [spec/sdk.md](spec/sdk.md)의 Areas of concern.
목표: "모르면 `계산 불가`, 알면 정확히" 원칙을 깨는 결함 4건을 고친다. 2026-10-06 모두 재현 확인.

| # | 결함 | 재현 |
| :--- | :--- | :--- |
| 1 | 부분 문자열 매칭이 다른 모델 단가를 씌움 | `openai/gpt-4.5-preview` → `gpt-4` 단가, `pricing_table` |
| 2 | 유료 API의 오픈 웨이트 모델명을 무료 처리 | `mistral/mistral-large-latest`, `deepseek/deepseek-chat` → `local_free` |
| 3 | SDK가 OpenAI 캐시 토큰을 놓침 | `prompt_tokens_details.cached_tokens` 미인식, 캐시 할인 누락 |
| 4 | SDK가 Gemini 캐시 토큰을 이중 계산 | `prompt_token_count`에 캐시분 포함된 채 전송 |

## Files that change

- `backend/app/services/cost.py` — 매칭 규칙, 오픈 웨이트 0원 단가의 적용 조건
- `backend/tests/test_cost.py`, `backend/tests/test_cost_source.py` — 회귀 테스트
- `sdk/python/aimonitor/client.py` — `extract_usage` 캐시 보정
- `sdk/python/tests/test_sdk_usage.py` — 형태별 테스트
- `docs/spec/cost.md`, `docs/spec/sdk.md` — 결정 반영, Areas of concern 정리
- `CHANGELOG.md`, 버전 문자열 5곳(`core/config.py`, `pyproject.toml`, `sdk/python/pyproject.toml`, `sdk/python/aimonitor/__init__.py`, `docker-compose.yml` 이미지 태그)

## Order of work

1. **테스트 먼저** — 위 4건을 실패하는 테스트로 고정한다.
2. **매칭(1)** — 키 뒤에 오는 문자가 버전·날짜 접미사(`-YYYY-MM-DD`, `-YYYYMMDD`, `-latest`, `-preview` 등 결정 필요)일 때만 일치로 본다. `gpt-4.5-preview`·`o3-pro`는 `unknown`이 된다.
3. **무료 처리(2)** — 단가 0인 오픈 웨이트 항목은 provider가 `LOCAL_PROVIDERS`일 때만 적용. 그 외 provider는 `unknown`.
4. **SDK(3, 4)** — OpenAI: `prompt_tokens_details.cached_tokens`를 읽고 `prompt_tokens`에서 뺀다. Google: `prompt_token_count − cached_content_token_count`. Anthropic은 그대로.
5. 스펙·CHANGELOG 갱신, 버전 0.4.0.

## Risks

- **가장 깨지기 쉬운 곳: 매칭 규칙 변경.** 지금 단가가 붙던 모델명이 `unknown`으로 바뀌면 대시보드에서 `계산 불가`가 늘어난다. 기존 테스트의 모델명 전부와 `scripts/simulate.py`·데모 버튼의 모델명이 계속 매칭되는지 확인한다.
- 과거 데이터는 재계산하지 않는다 (COST-5). 이미 잘못 계산된 행은 남는다 — CHANGELOG에 명시.
- SDK 보정은 서버보다 먼저 배포된 구버전 SDK에는 적용되지 않는다. 서버 쪽 보정은 provider 응답을 모르므로 하지 않는다.
- 단가표 자체의 노후화(Claude 4.x, GPT-5 등 미등록)는 이번 범위가 아니다. 단가 출처·기준일 확인이 필요해 별도 결정으로 둔다.

## Proof

```bash
python -m pytest -q backend/tests/test_cost.py backend/tests/test_cost_source.py sdk/python/tests/test_sdk_usage.py
python -m pytest -q          # 전체 회귀
ruff check .
```

- 완료 기준: 위 재현 표의 4개 입력이 각각 `unknown`, `unknown`, 캐시 할인 반영, 이중 계산 없음으로 테스트에서 확인된다.
- 기존 테스트 전부 통과 (기준선 2026-10-06: `152 passed, 1 skipped`).

## 후속 후보 (이번 범위 아님, 승인 시 새 plan)

- 알림: 빈 시간 창 처리, 비용 규칙의 미상 비용 ([spec/alerts.md](spec/alerts.md))
- 수집: CSV 수식 주입 이스케이프 ([spec/ingestion.md](spec/ingestion.md))
- 집계: v0.1.0 잔존 추정 비용의 합계 제외 ([spec/metrics.md](spec/metrics.md))
- 대시보드: 운영 환경에서 데모 트래픽 버튼 숨김 ([spec/dashboard.md](spec/dashboard.md))
- 단가표 갱신 + 기준일 기록 ([spec/cost.md](spec/cost.md))
