# Spec: 비용 계산 (Cost)

## Requirements

- COST-1 모든 트레이스는 `cost_source`로 비용의 출처를 밝힌다.
- COST-2 단가를 모르면 금액을 만들지 않는다. 대시보드 `계산 불가`, CSV 빈 칸, 집계에는 `cost_unknown_count`.
- COST-3 프롬프트 캐시 읽기·쓰기 토큰은 각각의 단가로 계산한다. 일반 입력과 합산하지 않는다.
- COST-4 적용 중인 단가표를 `GET /api/v1/system/pricing`으로 공개한다.
- COST-5 과거 행의 비용은 단가표가 바뀌어도 재계산하지 않는다 (수집 시점 단가로 고정).

## Design

### `cost_source` 결정 순서 (`calculate_cost_detail`)

| 순서 | 조건 | `cost_source` | `cost_usd` |
| :--- | :--- | :--- | :--- |
| 1 | 호출자가 `cost_usd ≥ 0` 전달 | `custom` | 전달값 (소수 6자리) |
| 2 | provider ∈ `ollama, local, vllm, llamacpp, lmstudio` | `local_free` | 0 |
| 3 | 단가표에서 모델을 못 찾음 | `unknown` | 0 (금액 아님) |
| 4 | 단가표 입력·출력 단가가 모두 0 | `local_free` | 0 |
| 5 | 그 외 | `pricing_table` | 계산값 |

### 계산식 (USD, 100만 토큰당 단가)

```
cost = prompt × input + completion × output + cache_write × write_rate + cache_read × read_rate
```

- `prompt_tokens`는 **캐시되지 않은 입력만** 센다.
- 모델별 캐시 단가가 없으면 provider 배수를 입력 단가에 곱한다.

| provider | 쓰기 배수 | 읽기 배수 |
| :--- | ---: | ---: |
| anthropic | 1.25 | 0.10 |
| openai | 1.00 | 0.50 |
| gemini / google | 1.00 | 0.25 |
| 그 외 | 1.00 | 1.00 (할인 없음) |

### 모델명 매칭

`normalize_model_name`: 소문자화 후 단가표 키 중 **모델명에 포함된 가장 긴 키**를 고른다 (`gpt-4o-mini-2024-07-18` → `gpt-4o-mini`). 길이가 같으면 표의 삽입 순서 — 표 순서를 바꾸면 매칭이 바뀐다.

## Open questions

- 단가표를 코드 상수 대신 설정 파일/DB로 → 이월 (intent Open questions). 단, 아래 결함 수정은 이월하지 않는다.
- 과거 비용 재계산 도구 → 이월: v0.1.0이 임의 단가로 채운 값은 `cost_source='unknown'`으로만 표시돼 있다.

## Areas of concern

★ 사람이 먼저 결정할 것. 2026-10-06 코드로 재현 확인.

1. **부분 문자열 매칭이 다른 모델 단가를 씌운다.** `gpt-4.5-preview` → `gpt-4` 단가($30/$60)로 `pricing_table` 처리 (1M+1M 토큰 = $90). `o3-pro` → `o3` 단가. "모르면 계산 불가" 원칙 위반.
2. **유료 API를 무료로 처리한다.** provider `mistral`/`deepseek`의 `mistral-large-latest`, `deepseek-chat`이 오픈 웨이트 키(`mistral`, `deepseek`, `qwen` 등)에 걸려 `local_free` $0이 된다. 오픈 웨이트 0원 단가는 로컬 provider일 때만 적용해야 한다.
3. **단가표가 오래됐다.** Claude 4.x, GPT-5 계열 등 최신 모델은 전부 `unknown`. 원칙상 안전하지만 대시보드 비용이 대부분 `계산 불가`가 된다. 단가 출처·기준일을 표에 기록하지 않아 갱신 시점을 알 수 없다.

→ 1·2는 [plan.md](../plan.md)의 최우선 작업. 3은 단가 갱신과 함께 "기준일" 필드를 두는 방향으로 결정 필요.
