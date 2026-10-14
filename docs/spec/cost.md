# Spec: 비용 계산 (Cost)

## Requirements

- COST-1 모든 트레이스는 `cost_source`로 비용의 출처를 밝힌다.
- COST-2 단가를 모르면 금액을 만들지 않는다. 데스크톱 화면 `계산 불가`, CSV 빈 칸, 집계에는 `cost_unknown_count`.
- COST-3 프롬프트 캐시 읽기·쓰기 토큰은 각각의 단가로 계산한다. 일반 입력과 합산하지 않는다.
- COST-4 적용 중인 단가표, 무료 provider 목록, 단가 확인일(`as_of`)과 출처(`sources`)를 `GET /api/v1/system/pricing`으로 공개한다.
- COST-5 수집할 때 기록의 UTC `timestamp`에 유효한 단가로 계산해 저장한다. 이미 저장한 행은 단가표가 바뀌어도 재계산하지 않는다.
- COST-6 단가표 키의 일부만 같은 다른 모델(`gpt-4.5-preview`, `gpt-4-32k`)에 그 단가를 빌려주지 않는다. 정확히 등록된 `o3-pro` 등은 자체 단가를 쓴다.
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
- `gpt-3.5-turbo-0613`은 공식 deprecations의 과거 입력 $1.50/출력 $2.00(1M당 USD) 키를 따로 둔다. 날짜가 붙었다는 이유만으로 현행 별칭 가격을 적용하지 않는다.
- `o1-mini`는 현행 공식 입력 $1.10/출력 $4.40/캐시 읽기 $0.55, Gemini 1.5 Flash-8B는 공식 과거 공지의 <128K 입력 $0.0375/출력 $0.15/캐시 읽기 $0.01을 적용한다(모두 1M 토큰당 USD). 기존 DB 행은 재계산하지 않는다.
- `gemini-3.5-flash`·`gemini-3.7-flash`는 [2026-10-08 changelog](https://ai.google.dev/gemini-api/docs/changelog)에 각각 3.6·3.8로의 자동 라우팅만 명시되고 별칭 청구 단가는 확인되지 않아 `unknown`이다. `PRICING_SOURCES.gemini_changelog`와 [가격 페이지](https://ai.google.dev/gemini-api/docs/pricing)를 2026-10-14에 확인했다. 공식 별칭 단가가 확인되면 다시 매핑하며, 기존 DB 행은 재계산하지 않는다.

### 장문 구간과 시행일

- **한 기록 = 한 API 호출**로 본다. 전체 입력 길이는 `prompt_tokens + cache_read_tokens + cache_write_tokens`이며, `cache_write_1h_tokens`는 쓰기의 부분집합이므로 길이에 다시 더하지 않는다. 임계값을 초과하면 그 호출의 입력·출력·캐시 각각에 장문 단가를 적용한다. 토큰 종류별 요금 계산은 계속 분리한다.
- 공식 페이지를 2026-10-14에 확인한 장문 구간만 `MODEL_LONG_CONTEXT_PRICING`에 둔다. 임계값과 정확히 같으면 짧은 구간이다.
  - OpenAI **272,000 초과**: `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.5-pro`, `gpt-5.4`, `gpt-5.4-pro`. [공식 Standard 가격표](https://developers.openai.com/api/docs/pricing)의 펼친 표에 있는 모델별 입력·출력·캐시 읽기·쓰기 단가를 사용한다. 캐시 가격이 `-`인 항목은 기존처럼 해당 구간의 일반 입력 단가(할인 없음)로 계산한다.
  - Gemini **200,000 초과**: `gemini-3.1-pro-preview` 입력/출력/쓰기/읽기 $4/$18/$4/$0.40, `gemini-2.5-pro` $2.50/$15/$2.50/$0.25 (1M 토큰당 USD, [공식 가격표](https://ai.google.dev/gemini-api/docs/pricing)).
- `MODEL_PRICING_SCHEDULE`은 시행일과 이후 단가를 보관한다. `calculate_cost_detail`·`calculate_cost`·`resolve_pricing`의 키워드 인자 `timestamp`로 기록 시각을 받으며, naive 시각은 UTC로 해석하고 aware 시각은 UTC로 변환한다. 생략하면 현재 UTC를 쓴다.
- Gemini 3.6·3.8 Flash는 **2027-01-01 00:00 UTC 전**에 입력/출력/쓰기/읽기 $0.75/$3.75/$0.75/$0.075, **이후(경계 포함)** $1.50/$7.50/$1.50/$0.15를 적용한다. [공식 가격표](https://ai.google.dev/gemini-api/docs/pricing)는 2026-12-31까지 프로모션, 2027-01-01부터 표준 단가라고만 명시하며 시각·시간대는 밝히지 않는다(2026-10-14 확인). 보수적으로 UTC 자정을 기준으로 삼는 제품 정책이다. 나중에 수집한 과거 기록도 해당 기록 시각의 단가를 사용한다.

### 단가표 API

`GET /api/v1/system/pricing`은 기존 메타데이터와 `models`를 유지하며 다음 정보를 공개한다. 모든 금액은 1M 토큰당 USD다.

| 필드 | 의미 |
| :--- | :--- |
| `as_of`, `sources` | 공식 가격 확인일과 출처 URL |
| `effective_at` | 현재 단가를 고른 서버 UTC 시각(오프셋 포함 ISO-8601) |
| `models.<key>.input/output/cache_write/cache_read` | `effective_at`에 유효한 짧은 구간 단가. 옛 모델의 미명시 캐시 값은 기존처럼 `null`이며 provider 배수로 보완한다. |
| `models.<key>.cache_write_1h` | 확인된 1시간 캐시 쓰기 단가, 미확인은 `null` |
| `models.<key>.long_context` | 확인된 장문 구간 객체 또는 `null`. `input_tokens_gt`는 초과 조건이며 나머지 5개 단가 필드는 같은 의미다. |
| `models.<key>.price_periods` | 짧은 구간의 시행 기간별 5개 단가 필드와 `effective_from`(포함)·`effective_until`(미포함). 시각은 UTC ISO-8601이며 `null`은 해당 방향의 경계 없음이다. 변경 이력이 없는 모델도 한 기간으로 공개한다. |

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
(문자열 시작 | "/" | ".") + (정확한 단가표 키 | 확인한 별칭) + 라우팅 접미사* + 문자열 끝
라우팅 접미사 = ("-" | "@") + (latest | vN | vN:M)
```

| 입력 | 결과 |
| :--- | :--- |
| `gpt-4o-mini-2024-07-18`, `gpt-3.5-turbo-0125` | 공식 확인한 각 기본 모델 |
| `claude-3-5-sonnet@20240620` (Vertex), `us.anthropic.claude-3-5-sonnet-20241022-v2:0` (Bedrock) | 각 날짜의 명시 키 (Vertex `@YYYYMMDD`를 `-YYYYMMDD`로 해석) |
| `models/gemini-1.5-pro` | `gemini-1.5-pro` |
| `claude-opus-4-1-20250805` | `claude-opus-4-1` (`claude-opus-4`가 아님 — 긴 키 우선) |
| `gpt-3.5-turbo-0613`, `o3-pro`, `gemini-3.1-pro-preview` | 정확한 등록 키의 자체 단가 (`-pro`·`-preview`여도 예외) |
| `gpt-4.5-preview`, `gpt-4-32k`, `ft:gpt-4o-mini:...` | `unknown` (미등록 모델) |
| `gpt-4o-2099-01-01`, `gemini-1.5-pro-001/002`, `gemini-2.0-flash-001` | `unknown` (현재 별칭과 가격 동일성이 확인되지 않은 날짜·숫자 버전) |

확인한 날짜 별칭 목록은 코드의 `_VERIFIED_MODEL_ALIASES`에만 둔다. 공식 모델 문서 또는 Anthropic 가격·deprecations 문서(2026-10-14 확인)에 근거하며, 여기에 없는 날짜·숫자 버전을 자동으로 벗겨내지 않는다.

## Open questions

- 단가표를 코드 상수 대신 설정 파일/DB로 → 이월 (intent Open questions).
- 과거 비용 재계산 도구 → 이월: v0.1.0이 임의 단가로 채운 값은 `cost_source='unknown'`으로만 표시돼 있다.

## Areas of concern

- 장문 구간은 위에서 공식 확인한 13개 모델만 반영한다. 현행 공식 페이지에서 확인하지 못한 옛 Claude Sonnet 4/4.5, Gemini 1.5 Pro/Flash/Flash-8B 등의 장문 구간은 **구간 미반영**이며 기존 단가를 유지한다. 한 기록이 여러 호출의 합계이면 구간 선택도 실제 청구와 다를 수 있다.
- Anthropic 1시간 캐시 쓰기는 공식 확인된 모델만 지원한다. Gemini 캐시 저장 시간 요금·OpenAI/Anthropic Batch 할인·`inference_geo` 1.1배·fast mode는 반영하지 않는다. 실제 청구액과 다를 수 있는 "표준 단가 기준 추정치"다.
- Bedrock·Vertex 경유 Claude는 파트너 가격(지역 엔드포인트 10% 할증 등)이지만 1st-party 단가로 계산한다.
- 은퇴·미게재 모델(`claude-3-*`, `gemini-1.5-pro/flash`, `gemini-2.0-*` 등)은 마지막으로 알려진 단가를 재확인 없이 유지한다. `o1-mini` 현행 단가와 `gemini-1.5-flash-8b` 과거 단가는 2026-10-14에 공식 문서로 재확인했다.
- 해결됨(v0.5.0): 최신 모델 미등록, 기준일·출처 없음, 공식 가격과 다른 항목(`o3` $10/$40→$2/$8, `gemini-2.5-flash` $0.075/$0.30→$0.30/$2.50, `gpt-4o-2024-05-13`이 `gpt-4o` 단가를 쓰던 문제). 확인할 수 없는 `gemini-2.5-flash-lite`·`gemini-2.0-pro-exp`는 제거(`계산 불가`).
- 날짜·숫자 버전은 공식 가격 동일성을 확인해 목록에 등록한 경우만 매칭한다. 그 밖의 미등록 접미사는 `unknown`이며, 정확한 단가표 키(`o3-pro`, `gemini-3.1-pro-preview` 등)는 자체 단가를 쓴다.
- 해결됨(v0.4.0): 부분 문자열 매칭으로 다른 모델 단가를 빌려 쓰던 문제, 유료 API의 오픈 웨이트 모델을 $0으로 처리하던 문제.
