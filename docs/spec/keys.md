# Spec: 공급자 API 키·청구액 (Provider keys & spend)

OpenAI·Anthropic·Gemini 등 공급자 API 키를 저장·검증하고, 공식 청구액·잔액을 조회한다.
코드: `backend/app/services/api_key_manager.py`, `services/secret_store.py`, `services/provider_spend.py`, `routers/api_keys.py`.
AIMONITOR 서버 자체의 인증 키(`API_KEY`)와는 별개다([security-ops.md](security-ops.md)).

## Requirements

- KEY-1 키는 Windows 사용자 범위 DPAPI로 암호화하여 `~/.aimonitor/api_keys.json`에 저장한다. 암호화를 지원하지 않는 플랫폼은 파일 저장을 거부하고 환경 변수 키만 쓴다.
- KEY-2 공급자마다 라벨을 붙인 키를 여러 개 둘 수 있다. 응답·화면·로그에 키 원문을 내보내지 않는다(마스킹만).
- KEY-3 키는 그 키를 발급한 공급자에게만 보낸다. Gemini 키는 URL이 아닌 `x-goog-api-key` 헤더로 보낸다.
- KEY-4 공식 청구액을 모르면 `kind=unavailable`, `amount=null`이다. 키별 금액을 임의로 합산하거나 다른 키 값으로 채우지 않는다.

## Design

### 저장

- DPAPI `CryptProtectData`(현재 사용자 범위, UI 금지, 엔트로피 `aimonitor-api-keys-v1`). 이전 평문 키는 조회 전에 암호화하여 이관한다.
- 저장소 옆 `api_keys.json.lock`의 프로세스 간 잠금으로 읽기부터 쓰기까지 보호한다. 잠금 대기는 최대 3초이며 실패는 통제된 저장소 오류다.
- 같은 디렉터리의 임시 파일에 쓰고 flush/fsync 후 원자 교체한다. 읽기 실패·손상 JSON/구조는 빈 저장소와 구분하며 원본에 쓰지 않는다.
- 목록은 저장소 오류를 `status=storage_error`, 개별 복호화 불가는 `status=unreadable`로 표시하고 기존 항목 필드를 유지한다. 비 Windows에서는 레거시 파일을 이관·복호화하지 않고 환경변수 키를 독립적으로 사용한다.
- 키는 최대 4096자이며 HTTP 헤더에 사용할 수 없는 제어 문자·비 ASCII 문자를 저장하지 않는다. 공급자 접두사는 강제하지 않는다.
- 서버가 만드는 `key_id`는 32자 UUID hex다. 기존 공급자 단일 키는 읽을 때 `default`/`기본`으로 이전하며 DPAPI 봉인을 보존한다.
- 라벨 기본값은 `기본`, 최대 40자이며 등록된/새 키 원문·키 접두사·이메일을 포함할 수 없다.
- 환경 변수 키(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `*_ADMIN_KEY`)는 읽기 전용으로 함께 보인다.

### API (`/api/v1/keys`)

| Method | Path | 내용 | 인증 |
| :--- | :--- | :--- | :--- |
| GET | `/keys` | 공급자 카드 배열. 각 카드의 `keys`에 `{key_id, label, masked, status, last_verified, is_admin_key}`. 기존 `masked_key`·`has_key` 유지 | 읽기 |
| POST | `/keys` | `{provider, key, label}` — 새 키 추가. 응답에 원문 없음 | 쓰기 |
| PATCH | `/keys/{provider}/{key_id}` | `{label}` — 이름만 변경 | 쓰기 |
| DELETE | `/keys/{provider}/{key_id}` | 그 키만 삭제 | 쓰기 |
| DELETE | `/keys/{provider}` | 그 공급자의 저장 키 모두 삭제 (환경 변수 키는 남음) | 쓰기 |
| POST | `/keys/{provider}/verify` | 실제 대표 키를 모델 목록/잔액/관리자 비용 API로 검증 | 쓰기 |
| POST | `/keys/{provider}/{key_id}/verify` | 지정한 키만 검증 (ID 패턴 `^[A-Za-z0-9_-]{1,32}$`) | 쓰기 |
| GET | `/keys/spend` | 키별 공식 청구액·잔액 (15분 캐시) | 읽기 |

키 라우터의 검증 오류는 일반 `Invalid key request` 메시지만 반환한다. 검증 실패도 요청·응답 원문을 싣지 않는다.

데스크톱은 HTTP 대신 `api_key_manager`를 직접 import한다. `list_keys()`는 공급자 카드, `set_key(provider, key)`는 기본/첫 키 갱신,
`get_registered_key(provider)`는 기본/첫 키 또는 환경 키를 반환한다. 개별 키용은 `list_keys(provider)`, `get_key(provider, key_id)`, `add_key`, `update_label`.
`verify_key(provider, key_id=None)`는 실제 조회한 ID와 secret 지문을 고정하며, 결과 기록 시 같은 키인지 다시 확인한다.
성공은 `valid`, 401/403은 `invalid`, 일시적 실패는 `verification_unavailable`이다. 환경변수 키의 검증 상태는 메모리에서만 유지한다.
인증 성공과 잔액 확인은 별개이며 누락 잔액은 `잔액 확인 불가`, 한도 0은 0으로 표시한다. DeepSeek 잔액은 통화별로 표시하고 환산·통화 간 합산하지 않는다.

### 청구액 조회 (`provider_spend.py`)

| 공급자 | 엔드포인트 | 필요한 키 |
| :--- | :--- | :--- |
| Anthropic | `/v1/organizations/cost_report` (이번 달) | 관리자 키 |
| OpenAI | `/v1/organization/costs` (이번 달) | 관리자 키 |
| OpenRouter | `/api/v1/auth/key` (사용액) | 일반 키 |
| DeepSeek | `/user/balance` (잔액) | 일반 키 |

일반 OpenAI·Anthropic 키로는 청구액을 조회하지 않는다.
`/keys/spend`의 `items`가 키별 `{provider, key_id, label, kind, amount, currency, …}` 정본이며, 기존 도구 키 필드의 기본/첫 키 결과도 유지한다.
관리자 키의 조직 청구액은 서로 겹칠 수 있으므로 키별 금액을 합산하지 않는다.

### 화면 (메인 창 API 키 탭)

공급자별 키를 별도 행으로 표시하며 라벨·마스킹·상태·키별 청구/잔액을 보여 준다. 라벨을 붙여 추가하고 개별 라벨 변경·삭제를 제공한다.
환경 변수 키는 읽기 전용이다. 저장에 성공하면 키 입력칸을 바로 비운다. 키별 청구 조회 실패는 ‘청구액 확인 불가’로 표시하며 다른 키 합계를 대신 붙이지 않는다.

## Areas of concern

- 환경 변수에 키가 있으면 사용자가 앱에 입력하지 않아도 청구액 조회(15분 캐시)에 쓰인다. [개인정보 문서](../privacy.md)에 공개돼 있다.
- Windows 외 플랫폼(Docker 서버)에서는 키를 저장할 수 없고 환경 변수만 쓴다.
