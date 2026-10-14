# Spec: 데스크톱 앱 (Desktop)

계정·로그 수집은 [accounts.md](accounts.md), 공급자 API 키·청구액은 [keys.md](keys.md), 읽고 보내는 데이터 전체는 [개인정보 문서](../privacy.md)에 있다.

## Requirements

- DESK-1 Windows 네이티브 창, 항상 위 위젯, 트레이, 단일 인스턴스를 제공한다.
- DESK-2 공식 한도·청구액과 로컬 기록의 추정 비용을 구별하고, 없는 값은 `확인 불가`/`계산 불가`로 표시한다.
- DESK-3 OS 또는 앱의 라이트·다크 모드 변경은 백엔드 폴링을 기다리지 않고 메인 창·위젯·차트·트레이 아이콘에 반영한다. 팔레트와 배치는 유지한다.
- DESK-4 서버 인증 키는 config의 `api_key`, 환경 변수 `AIMONITOR_API_KEY` 순서로 선택한다. 없으면 헤더 없이 요청하고, 401/403은 `인증 필요`로 표시하며 다음 폴링을 계속한다.
- DESK-5 Windows 사용자 로그인 시 자동 실행을 선택할 수 있다. 상태는 사용자 Run 레지스트리에서 조회하며 관리자 권한과 추가 의존성이 필요 없다.

## Design

### 실행 (소스)

저장소 루트에서 `python run_desktop.py`, 콘솔 없이는 `pythonw run_desktop.pyw`를 실행한다.
데스크톱 의존성은 `desktop/requirements.txt`, 백엔드 의존성은 `backend/requirements.txt`에 분리하며 Tkinter가 포함된 Python이 필요하다.
두 런처는 모듈을 import하기 전에 저장소 루트로 작업 디렉터리를 고정한다. 시작 위치가 System32여도 `.env`와 상대 DB 경로를 저장소 기준으로 해석한다.

기본 백엔드는 `http://127.0.0.1:8000`이다. 설정한 loopback HTTP URL의 호스트·포트로만 로컬 서버를 실행한다.
외부 URL·HTTPS URL이면 자동 서버 실행을 생략하고 로그로 안내한다.
10초마다 `/health`를 확인해 두 번 연속 응답이 없으면 다시 띄운다.
시작 실패·메인 루프 종료·트레이 종료 시 소유한 서버 자식을 terminate→wait(5초)→필요 시 kill→wait로 회수한다.
종료 시작 이후 생성된 자식도 즉시 회수하며 종료 처리는 중복 호출해도 한 번만 실행한다.
watcher는 데스크톱 프로세스에서 시작하며 백엔드 lifespan에서는 시작하지 않는다.
`~/.aimonitor/desktop_config.json`에 화면 설정과 `backend_url`을 저장한다.
설정은 키별 타입·범위를 검증하고 잘못된 필드만 기본값으로 복구한다(폴링 주기 500~3,600,000ms).
UI·환율 저장은 같은 잠금 안에서 최신 설정에 변경 필드만 반영한다. 임시 파일을 기록·동기화한 뒤
원자적으로 교체하며, 저장 실패를 성공으로 표시하지 않는다.
단일 인스턴스 잠금(`127.0.0.1:47821`)과 watcher 잠금을 체크아웃 간에 공유하므로 두 체크아웃을 동시에 실행하지 않는다.
두 번째 실행은 기존 창을 앞으로 가져오고 종료한다.
SHOW/PING 요청은 AIMONITOR 고유 응답으로 확인한다. 다른 프로그램이 포트를 점유하면 시작 실패를 안내한다.
트레이·위젯의 항상 위 상태는 같은 설정과 명시적 bool 설정 함수를 사용하며 메뉴 체크 상태도 동기화한다.
위젯 전체 사각형은 실제 모니터 작업 영역 안으로 제한한다. 음수 가상 좌표를 허용하고 모니터 분리·
DPI 변경 후에는 다음 갱신에서 가장 가까운 작업 영역으로 옮기며 필요하면 크기도 줄인다.
위젯 폴링 타이머는 하나만 예약하고 수동 갱신·종료 시 기존 예약을 취소한다.
수동 환율 갱신은 백그라운드에서 수행하며 완료 전까지 버튼을 비활성화한다.

### 배포용 exe (`build-windows/`)

PyInstaller 단일 폴더 빌드 `AIMONITOR.exe` 하나가 세 역할을 한다(`build-windows/entry.py`).
기본은 데스크톱 앱, `--serve-backend [--port N]`은 `127.0.0.1` 로컬 API 서버, `--self-test`는 GUI·API 모듈을 모두
import한 뒤 종료 코드로 결과를 알린다. exe 실행 시 작업 폴더는 `~/.aimonitor`이므로 기본 DB는
`~/.aimonitor/aimonitor.db`, `.env`도 그 폴더에서 읽는다. 콘솔이 없으므로 출력은 `~/.aimonitor/logs/<역할>.log`에
실행마다 새로 쓴다. 데스크톱 앱은 exe 자신을 `--serve-backend`로 실행해 백엔드를 띄운다.
코드 서명이 없어 첫 실행 시 SmartScreen 경고가 뜰 수 있다. 빌드·릴리스 절차는 [security-ops.md](security-ops.md)의 배포 절.

### Windows 시작 시 자동 실행

환경설정 탭의 `Windows 시작 시 자동 실행` 토글로 등록하거나 해제한다.
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`의 문자열 값 `AIMONITOR`만 쓰거나 지운다.
설정 파일에는 상태를 저장하지 않으며, 설정 탭을 열거나 메인 창을 다시 표시할 때 레지스트리에서 다시 읽는다.
다른 위치나 다른 명령이 등록돼 있으면 `다른 위치를 가리킴`으로 표시하고 토글을 켤 때 현재 명령으로 교체한다.
Windows 외에서는 `사용 불가`로 표시하며 레지스트리에 쓰지 않는다.
조회·변경 실패는 설정 행과 하단 상태 표시줄로 안내하고 예외 문자열은 화면에 노출하지 않는다.

등록 명령(`desktop.autostart.command()`):

| 실행 형태 | 명령 |
| :--- | :--- |
| exe | `"<AIMONITOR.exe>" --autostart` |
| 소스 | `"<pythonw.exe>" "<저장소 루트>\run_desktop.pyw" --autostart` — 인터프리터가 `python.exe`면 같은 폴더의 `pythonw.exe`, 없으면 현재 인터프리터 |

`--autostart`는 메인 창을 숨기고 위젯·트레이로 시작한다. 이미 실행 중이면 아무것도 만들지 않고 조용히 종료한다.
끄려면 토글을 끄거나 해당 Run 값만 삭제한다. 프로그램에서는 `desktop.autostart.enable()` / `disable()`을 사용한다.

### 서버 인증 키

모든 AIMONITOR 백엔드 HTTP 요청(health·조회·watcher 수집)에 `X-API-Key`를 붙인다(DESK-4).
공급자 API 키·Anthropic OAuth 토큰과는 별개이며 OAuth·Ollama·환율 서버에는 보내지 않는다.
`API_KEY`가 설정되면 수집은 키가 필요하고, `REQUIRE_AUTH_FOR_READS=true`면 조회에도 같은 키가 필요하다.
401/403은 메인 상태 표시줄·위젯에 `인증 필요`로 표시한다. 수집 watcher는 같은 문구를 로그에 남기며 실패한 수집을 성공으로 처리하지 않는다.

환경 변수 `AIMONITOR_API_KEY`에 백엔드의 `API_KEY` 값을 넣거나, 저장소 루트에서 config에 저장한다(입력은 화면에 표시되지 않는다).

```python
from getpass import getpass
from desktop.config import load_config, save_config
config = load_config()
config["api_key"] = getpass("AIMONITOR server key: ")
save_config(config)
```

config 키는 메모리에서만 복호화하며 파일에는 `api_key_scheme`·`api_key_sealed`로 DPAPI 암호화하여 쓴다.
인증 키는 화면이나 오류 문자열에 출력하지 않는다. 암호화 미지원 환경에서는 환경 변수를 사용한다.

### 테마

CustomTkinter의 `AppearanceModeTracker` 콜백을 공유한다. 앱 모드 변경 시 즉시,
OS 모드 변경 시 라이브러리의 가벼운 감지 루프에서 반영하며 백엔드 요청은 추가하지 않는다.
위젯은 캐시된 데이터로 다시 그리며 트레이는 `pystray.Icon.icon` 이미지를 교체한다.
창 파괴와 트레이 중지 시 콜백을 해제한다.

### 화면이 쓰는 API

위젯의 한 줄·카드 모드 모두 한도 기준 시각·오래됨·컨텍스트 추정 표식을 표시한다.
연결 실패 후 유지한 숫자에는 `연결 실패 · 이전 값`을 붙인다.
메인 창은 엔드포인트별 성공 여부를 구별하며 사용량 조회가 실패하면 이전 화면을 유지하고 갱신 실패를 알린다.
차트·로그만 실패한 경우 해당 이전 데이터를 유지하고 `일부 갱신 실패`를 표시한다.
정상적인 빈 응답만 0 사용량으로 취급한다. 모델별 비용 목록·차트는 미확인 요청 수를 포함해 갱신하며,
전부 단가 미확인이면 `계산 불가`, 일부이면 알려진 금액과 미확인 건수를 표시한다.
Copilot의 단일 계정이 미상이더라도 로컬 프리미엄 요청 수로 계산한 한도는 유지한다.

도구별 사용량은 `/metrics/active_contexts`·`/metrics/connected_ais`([metrics.md](metrics.md)),
로그·차트는 `/traces`·`/metrics/overview`·`/metrics/breakdown`, 키·청구액은 `/keys`([keys.md](keys.md))를 사용한다.
읽기·쓰기 인증은 `require_read_auth`/`require_write_auth`를 따른다.

## Areas of concern

- 로컬 기록은 이 PC에서 관측한 사용량이며 전체 계정 청구액과 다를 수 있다.
- GUI 테스트 의존성이 없는 백엔드 전용 환경에서는 해당 GUI 테스트를 건너뛴다. Windows CI job에서만 실행된다.
- 화면 문구가 한국어뿐이다. 다국어는 아직 지원하지 않는다.
