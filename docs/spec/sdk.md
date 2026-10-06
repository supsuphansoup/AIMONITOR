# Spec: Python SDK (`aimonitor-sdk`)

사용법은 [sdk/python/README.md](../../sdk/python/README.md). 이 문서는 동작 규칙만 다룬다.

## Requirements

- SDK-1 데코레이터 `@monitor.observe()`가 동기·비동기 함수의 지연 시간·성공/실패·토큰 수를 자동 기록한다.
- SDK-2 명시적 기록 `track()` / `a_track()`, 배경 전송 `enqueue()` / `flush()`를 제공한다.
- SDK-3 수집 서버 장애가 호출자 애플리케이션에 전파되지 않는다.
- SDK-4 카운터·시간·상태만 전송한다. 프롬프트·응답 텍스트는 읽지도 보내지도 않는다.

## Design

| 항목 | 값 |
| :--- | :--- |
| 기본 `api_url` | `http://localhost:8000/api/v1` |
| `timeout` | 3.0초 |
| 배경 모드 | `background=True`, `batch_size` 25, `flush_interval` 2.0초, `max_queue_size` 10,000 |
| 큐가 가득 참 | 블로킹하지 않고 드롭, 100건마다 경고 로그 |
| 인증 | `api_key` 전달 시 `X-API-Key` 헤더 |
| 종료 | `close()` / `a_close()`가 큐를 비운 뒤 종료. `with` / `async with` 지원 |
| 의존성 | `httpx>=0.27.0` |

### 데코레이터

- 예외 발생 시 `status=ERROR`, `error_message=str(e)`로 기록하고 **예외는 다시 던진다.**
- 배경 모드면 `enqueue`, 아니면 `track`으로 보낸다 (동기 `track`은 요청 경로에서 최대 `timeout`만큼 대기).
- 토큰 추출(`extract_usage`): 응답의 `usage` 또는 `usage_metadata`에서 정수 카운터만 읽는다. 모르는 형태는 0. `usage_extractor=`로 교체, `capture_usage=False`로 끔.

| 형태 | prompt | completion | cache read | cache write |
| :--- | :--- | :--- | :--- | :--- |
| OpenAI | `prompt_tokens` | `completion_tokens` | `cached_tokens`(최상위만) | — |
| Anthropic | `input_tokens` | `output_tokens` | `cache_read_input_tokens` | `cache_creation_input_tokens` |
| Google | `prompt_token_count` | `candidates_token_count` | `cached_content_token_count` | — |

## Open questions

- 전송 실패 시 재시도 → 이월: 현재는 버린다. 재시도를 넣으면 서버 쪽 `trace_id` 멱등 처리가 먼저 필요 ([ingestion.md](ingestion.md)).
- 다른 언어 SDK(JS/TS) → 이월.

## Areas of concern

★ 2026-10-06 재현 확인. 서버 규칙("`prompt_tokens`는 캐시되지 않은 입력만")과 provider 응답 의미가 다르다.

1. **OpenAI 캐시 토큰을 놓친다.** OpenAI는 캐시 토큰을 `usage.prompt_tokens_details.cached_tokens`(중첩)에 주는데 SDK는 최상위만 읽는다. 또 `prompt_tokens`가 캐시분을 **포함**하므로, 캐시 800/입력 1000 호출이 입력 1000 정가로 계산된다 (캐시 할인 누락).
2. **Gemini 캐시 토큰을 이중 계산한다.** `prompt_token_count`가 `cached_content_token_count`를 포함하는데 둘 다 그대로 보내, 800 토큰이 정가 + 캐시 단가로 두 번 계산된다.
3. Anthropic은 `input_tokens`가 이미 캐시 제외 값이라 정상.

→ OpenAI·Google 형태에서는 `prompt_tokens = 전체 입력 − 캐시 읽기`로 보정해야 한다. [plan.md](../plan.md) 최우선 작업에 포함.
