# Spec: Python SDK (`aimonitor-sdk`)

사용법은 [sdk/python/README.md](../../sdk/python/README.md). 이 문서는 동작 규칙만 다룬다.

## Requirements

- SDK-1 데코레이터 `@monitor.observe()`가 동기·비동기 함수의 지연 시간·성공/실패·토큰 수를 자동 기록한다.
- SDK-2 명시적 기록 `track()` / `a_track()`, 배경 전송 `enqueue()` / `flush()`를 제공한다.
- SDK-3 수집 서버 장애가 호출자 애플리케이션에 전파되지 않는다.
- SDK-4 자동 추출기는 응답의 카운터만 읽으며 프롬프트·응답 텍스트를 읽지 않는다. 명시적 기록은 식별자·태그·정제한 metadata와 선택적인 `error_message`도 전송한다.
- SDK-5 `track`, `a_track`, `enqueue`, `observe`는 선택 인자 `account_id`를 전달한다. 생략하면 `null`이며 임의 기본 계정을 만들지 않는다. 형식 검증은 서버가 담당한다.

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

- 예외 발생 시 `status=ERROR`, `error_message=type(e).__name__`으로 종류만 기록하고 **원래 예외는 다시 던진다.** 원문 진단을 보내려면 `track(error_message=...)` / `a_track(error_message=...)` / `enqueue(error_message=...)`에 직접 전달한다.
- 배경 모드면 `enqueue`, 아니면 `track`으로 보낸다 (동기 `track`은 요청 경로에서 최대 `timeout`만큼 대기).
- 토큰 추출(`extract_usage`): 응답의 `usage` 또는 `usage_metadata`에서 정수 카운터만 읽는다. 모르는 형태는 0. `usage_extractor=`로 교체, `capture_usage=False`로 끔.
- 사용자 추출기는 토큰 필드만 전달할 수 있다. dict가 아니거나 카운터가 정수가 아니거나 서버 상한을 넘으면 측정을 생략한다. 알 수 없는 키는 무시한다. 추출·기록 실패는 관측 대상 함수의 반환값·예외를 바꾸지 않는다.
- 명시적 기록의 `latency_ms` 기본값은 `None`이다. 생략·`None`은 JSON null(미측정), 직접 전달하거나 observe가 측정한 0ms는 숫자 0이다.

| 형태 | prompt | completion | cache read | cache write |
| :--- | :--- | :--- | :--- | :--- |
| OpenAI | `prompt_tokens − cached` | `completion_tokens` | `prompt_tokens_details.cached_tokens` | — |
| OpenAI Responses | `input_tokens − cached` | `output_tokens` | `input_tokens_details.cached_tokens` | — |
| Anthropic | `input_tokens` (원래 캐시 제외) | `output_tokens` | `cache_read_input_tokens` | `cache_creation_input_tokens` |
| Google | `prompt_token_count − cached` | `candidates_token_count` | `cached_content_token_count` | — |

서버는 `prompt_tokens`를 **캐시되지 않은 입력**으로 계산하므로, 캐시분을 포함해 보고하는 OpenAI·Google은 SDK에서 뺀다 (음수는 0).

Anthropic의 `usage.cache_creation.ephemeral_1h_input_tokens`는 `cache_write_1h_tokens`로
전달한다. `cache_write_tokens` 전체의 부분집합이며 합계에 더하지 않는다. 두 값은
`track`·`a_track`·`enqueue`·동기/비동기 `observe` 모두에서 유지한다
([공식 응답 필드](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#mixing-different-ttls)).

### 전송 범위

SDK는 공급자·모델명, 토큰 카운터, 지연 시간·상태와 선택 식별자(`trace_id`,
`session_id`, `account_id`), tags, metadata, 명시적 error_message를 설정한 수집 서버로
전송한다. metadata는 전송 및 큐 적재 전에 서버와 같은 내용성 키 목록으로 dict·list를
재귀 정제한다(컨테이너마다 50개, 루트 깊이 0에서 최대 6). 알려진 키 이름 기반 정제는
모든 개인정보를 탐지하지 못하므로 임의 키·태그·식별자·명시적 오류에 원문이나 비밀을
넣지 않는다. 서버의 metadata 크기 상한·오류 절단도 적용된다.

## Open questions

- 전송 실패 시 재시도 → 이월: 현재는 버린다. 재시도를 넣으면 서버 쪽 `trace_id` 멱등 처리가 먼저 필요 ([ingestion.md](ingestion.md)).
- 다른 언어 SDK(JS/TS) → 이월.

## Areas of concern

- 보정은 SDK에서만 한다. 0.4.0 미만 SDK나 직접 HTTP로 보내는 클라이언트가 OpenAI·Google의 캐시 포함 값을 그대로 보내면 서버는 구분할 수 없다.
- 해결됨(v0.4.0): OpenAI 캐시 토큰 누락, Gemini 캐시 토큰 이중 계산.
