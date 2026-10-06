# Spec: 비용 계산 (Cost)

## Requirements

- COST-1 모든 트레이스는 `cost_source`로 비용의 출처를 밝힌다.
- COST-2 단가를 모르면 금액을 만들지 않는다. 대시보드 `계산 불가`, CSV 빈 칸, 집계에는 `cost_unknown_count`.
- COST-3 프롬프트 캐시 읽기·쓰기 토큰은 각각의 단가로 계산한다. 일반 입력과 합산하지 않는다.
- COST-4 적용 중인 단가표와 무료 provider 목록을 `GET /api/v1/system/pricing`으로 공개한다.
- COST-5 과거 행의 비용은 단가표가 바뀌어도 재계산하지 않는다 (수집 시점 단가로 고정).
- COST-6 단가표 키의 일부만 같은 다른 모델(`gpt-4.5-preview`, `o3-pro`)에 그 단가를 빌려주지 않는다.
- COST-7 오픈 웨이트 모델이라도 유료 API로 호출했으면 무료가 아니다. 무료 여부는 provider로만 판단한다.

## Design

### `cost_source` 결정 순서 (`calculate_cost_detail`)

| 순서 | 조건 | `cost_source` | `cost_usd` |
| :--- | :--- | :--- | :--- |
| 1 | 호출자가 `cost_usd ≥ 0` 전달 | `custom` | 전달값 (소수 6자리) |
| 2 | provider ∈ `ollama, local, vllm, llamacpp, lmstudio` | `local_free` | 0 |
| 3 | 단가표에서 모델을 못 찾음 | `unknown` | 0 (금액 아님) |
| 4 | 그 외 | `pricing_table` | 계산값 |

단가표에는 유료 API 모델만 둔다. 단가 0인 항목은 두지 않는다.

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

`normalize_model_name`: 소문자화 후 긴 키부터 아래 형태에 맞는지 본다. 맞는 키가 없으면 `unknown`.

```
(문자열 시작 | "/" | ".") + 키 + 버전 접미사* + 문자열 끝
버전 접미사 = ("-" | "@") + (YYYYMMDD | YYYY-MM-DD | 3–4자리 숫자 | latest | vN | vN:M)
```

| 입력 | 결과 |
| :--- | :--- |
| `gpt-4o-mini-2024-07-18`, `gpt-3.5-turbo-0125`, `gemini-1.5-pro-002` | 각 기본 모델 |
| `claude-3-5-sonnet@20240620` (Vertex), `us.anthropic.claude-3-5-sonnet-20241022-v2:0` (Bedrock), `models/gemini-1.5-pro` | 각 기본 모델 |
| `gpt-4.5-preview`, `gpt-4-32k`, `o3-pro`, `*-preview`, `ft:gpt-4o-mini:...` | `unknown` (다른 모델) |

## Open questions

- 단가표를 코드 상수 대신 설정 파일/DB로 → 이월 (intent Open questions).
- 과거 비용 재계산 도구 → 이월: v0.1.0이 임의 단가로 채운 값은 `cost_source='unknown'`으로만 표시돼 있다.

## Areas of concern

- **단가표가 오래됐다.** Claude 4.x, GPT-5 계열 등 최신 모델은 전부 `unknown`. 원칙상 안전하지만 대시보드 비용이 대부분 `계산 불가`가 된다. 단가 출처·기준일을 표에 기록하지 않아 갱신 시점을 알 수 없다. 단가 갱신과 함께 "기준일"을 두는 방향으로 결정 필요.
- 매칭이 엄격해져(v0.4.0) 날짜·버전 외 접미사가 붙은 이름은 모두 `unknown`이다. 같은 단가인 별칭이 자주 쓰이면 키를 표에 추가해야 한다.
- 해결됨(v0.4.0): 부분 문자열 매칭으로 다른 모델 단가를 빌려 쓰던 문제, 유료 API의 오픈 웨이트 모델을 $0으로 처리하던 문제.
