# Spec: 데스크톱 앱 (Desktop)

## Requirements

- DESK-1 Windows 네이티브 창, 항상 위 위젯, 트레이, 단일 인스턴스를 제공한다.
- DESK-2 로컬 Claude Code·Copilot·Antigravity 로그에서는 카운터와 상태만 수집한다. 원문을 전송하지 않는다.
- DESK-3 공식 한도·청구액과 로컬 기록의 추정 비용을 구별하고, 없는 값은 `확인 불가`/`계산 불가`로 표시한다.
- DESK-4 API 키는 Windows 사용자 범위 DPAPI로 암호화하여 저장한다. 암호화를 지원하지 않는 플랫폼은 파일 저장을 거부하고 환경 변수 키만 사용한다.
- DESK-5 OS 또는 앱의 라이트·다크 모드 변경은 백엔드 폴링을 기다리지 않고 메인 창·위젯·차트·트레이 아이콘에 반영한다. 팔레트와 배치는 유지한다.
- DESK-6 서버 인증 키는 config의 `api_key`, 환경 변수 `AIMONITOR_API_KEY` 순서로 선택한다. 없으면 헤더 없이 요청하고, 401/403은 `인증 필요`로 표시하며 다음 폴링을 계속한다.

## Design

### 실행

저장소 루트에서 `python run_desktop.py`를 실행한다. Windows에서는 `AIMONITOR.bat`,
`AIMONITOR.vbs`, `run_desktop.pyw`로 콘솔 없이 실행할 수 있다.
데스크톱 의존성은 `desktop/requirements.txt`, 백엔드 의존성은 `backend/requirements.txt`에 분리한다.
Tkinter가 포함된 Python이 필요하다. 필요한 환경에서는 사람이 두 requirements 파일을 설치한다.

앱은 `http://127.0.0.1:8000`의 기존 백엔드에 연결하며, 없으면 로컬 백엔드를 실행한다.
watcher는 데스크톱 프로세스에서 시작하며 백엔드 lifespan에서는 시작하지 않는다.
`~/.aimonitor/desktop_config.json`에 화면 설정과 `backend_url`을 저장하고,
`~/.aimonitor/api_keys.json`에는 암호화된 키만 쓴다. 이전 평문 키는 조회 전에 암호화하여 이관한다.
원본 앱과 단일 인스턴스·watcher 잠금을 공유하므로 두 체크아웃을 동시에 실행하지 않는다.

### API (`/api/v1` 접두사)

| Method | Path | 내용 |
| :--- | :--- | :--- |
| POST | `/traces`, `/traces/batch` | `cache_write_1h_tokens`와 미측정 지연을 포함한 수집 (201) |
| GET | `/traces`, `/metrics/overview`, `/metrics/breakdown` | 로그, 요청·비용·지연 통계 |
| GET | `/metrics/active_contexts` | 도구별 최신 컨텍스트와 5시간·오늘·7일·이번 달 사용량 |
| GET | `/keys`, `/keys/spend` | 마스킹된 키 상태, 공식 사용 금액 또는 잔액 |
| POST | `/keys`, `/keys/{provider}/verify` | 키 등록·공식 API 검증 |
| DELETE | `/keys/{provider}` | 로컬 키 삭제 |

읽기·쓰기 인증은 기존 `require_read_auth`/`require_write_auth` 규칙을 따른다.
데스크톱 직접 키 관리는 `api_key_manager`를 import하여 사용한다.
모든 AIMONITOR 백엔드 HTTP 요청(health·조회·watcher 수집)에 `X-API-Key`를 붙인다.
공급자 API 키 및 Anthropic OAuth 키와는 별개이며 OAuth·Ollama·환율 서버에는 서버 인증 키를 보내지 않는다.
`API_KEY`가 설정되면 수집은 키가 필요하고, `REQUIRE_AUTH_FOR_READS=true`면 조회에도 같은 키가 필요하다.
401/403은 메인 상태 표시줄·위젯에 `인증 필요`로 표시한다. 수집 watcher는 같은 문구를 로그에 남기며 실패한 수집을 성공으로 처리하지 않는다.

환경 변수 `AIMONITOR_API_KEY`에 백엔드의 `API_KEY` 값을 설정하거나,
저장소 루트에서 다음 명령으로 config에 저장한다(입력은 화면에 표시되지 않는다).

```python
from getpass import getpass
from desktop.config import load_config, save_config
config = load_config()
config["api_key"] = getpass("AIMONITOR server key: ")
save_config(config)
```

config 키는 메모리에서만 복호화하며 파일에는 `api_key_scheme`·`api_key_sealed`로 DPAPI 암호화하여 쓴다.
인증 키는 화면이나 오류 문자열에 출력하지 않는다. 암호화 미지원 환경에서는 환경 변수를 사용한다.

테마는 CustomTkinter의 `AppearanceModeTracker` 콜백을 공유한다. 앱 모드 변경 시 즉시,
OS 모드 변경 시 라이브러리의 가벼운 감지 루프에서 반영하며 백엔드 요청은 추가하지 않는다.
위젯은 캐시된 데이터로 다시 그리며 트레이는 `pystray.Icon.icon` 이미지를 교체한다.
창 파괴와 트레이 중지 시 콜백을 해제한다.

`active_contexts`는 정확한 쉼표 구분 태그 `claude-code`·`copilot`·`antigravity`로 도구를 구분하며,
나머지는 `<provider>_api`로 묶는다. 오늘·이번 달 경계는 백엔드 PC의 로컬 시각,
응답 시각은 UTC 오프셋 ISO-8601이다. SQL 합계에서 미상 비용을 제외하고 `cost_unknown`을 반환한다.
최신 요청은 `timestamp DESC, id DESC`로 선택하며 미래 시각 행을 제외한다.
Copilot 세션 합계와 Antigravity 추정 토큰은 컨텍스트 크기로 사용하지 않는다.

공식 청구액 조회는 Anthropic·OpenAI 관리자 키, OpenRouter 사용액, DeepSeek 잔액을 지원한다.
15분 캐시를 사용하며 API가 없거나 조회 실패하면 `kind=unavailable`, `amount=null`이다.
Gemini 키는 URL에 넣지 않고 `x-goog-api-key` 헤더로 보낸다. 키 검증 오류는 요청·응답 원문을 반환하지 않는다.

## Open questions

- 웹의 `ApiKeyModal.tsx`·`ConnectedAICards.tsx` 및 `/metrics/connected_ais`는 이번 이식 범위 밖이다.

## Areas of concern

- 로컬 기록은 이 PC에서 관측한 사용량이며 전체 계정 청구액과 다를 수 있다.
- Antigravity 토큰은 추정치다. 공식 단가가 없는 모델 이름은 비용 계산 불가로 유지한다.
- GUI 테스트 의존성이 없는 백엔드 전용 환경에서는 해당 GUI 테스트를 건너뛴다.
