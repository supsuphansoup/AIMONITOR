# Spec: 비용 계산 (Cost)

## Requirements

- COST-1 모든 트레이스는 `cost_source`로 비용의 출처를 밝힌다.
- COST-2 단가를 모르면 금액을 만들지 않는다. 데스크톱 화면 `계산 불가`, CSV 빈 칸, 집계에는 `cost_unknown_count`.
- COST-3 프롬프트 캐시 읽기·쓰기 토큰은 각각의 단가로 계산한다. 일반 입력과 합산하지 않는다.
- COST-4 적용 중인 단가표, 무료 provider 목록, 단가 확인일(`as_of`)과 출처(`sources`)를 `GET /api/v1/system/pricing`으로 공개한다.
- COST-5 과거 행의 비용은 단가표가 바뀌어도 재계산하지 않는다 (수집 시점 단가로 고정).
- COST-6 단가표 키의 일부만 같은 다른 모델(`gpt-4.5-preview`, `o3-pro`)에 그 단가를 빌려주지 않는다.
- COST-7 오픈 웨이트 모델이라도 유료 API로 호출했으면 무료가 아니다. 무료 여부는 provider로만 판단한다.

## Design

### `cost_source` 결정 순서 (`calculate_cost_detail`)

| 순서 | 조건 | `cost_source` | `cost_usd` |
| :--- | :--- | :--- | :--- |
| 0 | 토큰 수·제출 비용·계산 결과가 비유한 값(inf/NaN 등) | `unknown` | 0 (금액 아님) |
| 1 | 호출자가 유한한 `cost_usd ≥ 0` 전달 | `custom` | 전달값 (소수 12자리) |
| 2 | provider ∈ `ollama, local, vllm, llamacpp, lmstudio` | `local_free` | 0 |
| 3 | 단가표에서 모델을 못 찾음 | `unknown` | 0 (금액 아님) |
| 4 | 그 외 | `pricing_table` | 계산값 |

단가표에는 유료 API 모델만 둔다. 단가 0인 항목은 두지 않는다.

### 단가표 관리

- 기준일 `PRICING_AS_OF`(현재 2026-10-14)와 출처 `PRICING_SOURCES`(공식 가격·모델·과거 공지 페이지)를 코드에 둔다. 단가를 고칠 때 함께 갱신한다.
- 항목은 "확인됨"(기준일에 공식 페이지와 대조)과 "은퇴·미게재, 재확인 안 함"으로 나눠 주석을 단다. 확인할 수 없고 의심스러운 값은 넣지 않는다.
- 모든 확인된 항목은 **캐시 쓰기·읽기 단가를 명시**한다. 캐시 할인율이 모델마다 달라(Anthropic Opus 5.5 0.05배·Fable 5.1 0.025배, OpenAI gpt-4o 0.5배·gpt-4.1 0.25배·gpt-5 0.1배, Gemini 2.5+ 0.1배) provider 배수로는 틀린다. provider 배수는 단가가 명시되지 않은 옛 항목에만 쓰인다.
  - OpenAI: 쓰기는 모델별 명시 단가. `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`의 쓰기 단가는 각각 $12.50/$2.50/$2.50/$0.125/$5.00/$2.50/$0.25이다. 그 외 등록 모델은 쓰기 = 입력 단가. 캐시 가격이 없는 모델은 읽기도 입력 단가(할인 없음).
  - Anthropic: 기본 쓰기는 5분 캐시 단가. `cache_write_1h_tokens`는 전체 쓰기의 부분집합이며 공식 확인 모델에 한해 입력의 2배 단가로 별도 계산한다 (2026-10-14, PRICING_SOURCES의 Anthropic 페이지). 확인하지 못한 모델의 1시간 쓰기는 전체 호출을 `unknown`으로 처리한다. Sonnet 5.5 읽기는 $0.10/1M이다.
  - Gemini: 쓰기 = 입력 단가, 읽기 = 공식 context caching 단가.
- 같은 계열이라도 가격이 다른 스냅샷은 별도 키로 둔다 (`gpt-4o-2024-05-13` = $5/$15, `gpt-4o`는 $2.50/$10).
- `o1-mini`는 현행 공식 입력 $1.10/출력 $4.40/캐시 읽기 $0.55, Gemini 1.5 Flash-8B는 공식 과거 공지의 <128K 입력 $0.0375/출력 $0.15/캐시 읽기 $0.01을 적용한다(모두 1M 토큰당 USD). 기존 DB 행은 재계산하지 않는다.
- `gemini-3.5-flash`는 2026-10-08 changelog에 3.6으로의 자동 라우팅만 명시되고 별칭 청구 단가는 확인되지 않아 `unknown`이다. `PRICING_SOURCES.gemini_changelog`와 가격 페이지를 2026-10-14에 확인했다.

### 계산식 (USD, 100만 토큰당 단가)

```
cost = prompt × input + completion × output
     + (cache_write - cache_write_1h) × write_rate + cache_write_1h × write_1h_rate
     + cache_read × read_rate
```

- `prompt_tokens`는 **캐시되지 않은 입력만** 센다.
- 모델별 캐시 단가가 없으면 provider 배수를 입력 단가에 곱한다.
- 저장 정밀도는 소수 12자리다(부동소수 잡음 정리). 단건 응답은 저장값 그대로, 집계 응답 총액은 소수 6자리로 반올림한다. 요청마다 소수 6자리로 잘라 소액 비용을 잃지 않는다.
- 비유한 토큰 수·제출 비용은 계산 전에 검사하며, 계산 결과도 유한해야 한다. 계산 함수는 inf/NaN을 반환하지 않는다.

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
| `claude-opus-4-1-20250805` | `claude-opus-4-1` (`claude-opus-4`가 아님 — 긴 키 우선) |
| `gpt-4.5-preview`, `gpt-4-32k`, `o3-pro`, `*-preview`, `ft:gpt-4o-mini:...` | `unknown` (다른 모델) |

## Open questions

- 단가표를 코드 상수 대신 설정 파일/DB로 → 이월 (intent Open questions).
- 과거 비용 재계산 도구 → 이월: v0.1.0이 임의 단가로 채운 값은 `cost_source='unknown'`으로만 표시돼 있다.

## Areas of concern

- **2027-01-01 전에 단가표를 갱신해야 한다.** Gemini 3.6–3.8 Flash는 2026-12-31까지 프로모션 가격이고 이후 2배가 된다. 단가표에는 유효 기간 개념이 없어 갱신하지 않으면 조용히 과소 계산된다.
- **구간·옵션 가격을 모델링하지 않는다.** 긴 프롬프트 구간(OpenAI gpt-5.4/5.5 272K 초과, Gemini Pro 200K 초과)은 더 비싸지만 짧은 구간 단가로 계산한다. Anthropic 1시간 캐시 쓰기는 공식 확인된 모델만 지원한다. Gemini 캐시 저장 시간 요금·OpenAI/Anthropic Batch 할인·`inference_geo` 1.1배·fast mode는 반영하지 않는다. → 실제 청구액과 다를 수 있는 "표준 단가 기준 추정치".
- Bedrock·Vertex 경유 Claude는 파트너 가격(지역 엔드포인트 10% 할증 등)이지만 1st-party 단가로 계산한다.
- 은퇴·미게재 모델(`claude-3-*`, `gemini-1.5-pro/flash`, `gemini-2.0-*` 등)은 마지막으로 알려진 단가를 재확인 없이 유지한다. `o1-mini` 현행 단가와 `gemini-1.5-flash-8b` 과거 단가는 2026-10-14에 공식 문서로 재확인했다.
- 해결됨(v0.5.0): 최신 모델 미등록, 기준일·출처 없음, 공식 가격과 다른 항목(`o3` $10/$40→$2/$8, `gemini-2.5-flash` $0.075/$0.30→$0.30/$2.50, `gpt-4o-2024-05-13`이 `gpt-4o` 단가를 쓰던 문제). 확인할 수 없는 `gemini-2.5-flash-lite`·`gemini-2.0-pro-exp`는 제거(`계산 불가`).
- 매칭이 엄격해져(v0.4.0) 날짜·버전 외 접미사가 붙은 이름은 모두 `unknown`이다. 같은 단가인 별칭이 자주 쓰이면 키를 표에 추가해야 한다.
- 해결됨(v0.4.0): 부분 문자열 매칭으로 다른 모델 단가를 빌려 쓰던 문제, 유료 API의 오픈 웨이트 모델을 $0으로 처리하던 문제.
