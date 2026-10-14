# Spec: 보안·운영 (Security & Operations)

## Requirements

- OPS-1 API 키로 쓰기 엔드포인트를 보호하고, 필요하면 읽기도 보호한다. 헬스체크는 항상 공개.
- OPS-2 프롬프트·응답 원문은 DB·로그·오류 응답·CSV 어디에도 남기지 않는다.
- OPS-3 보관 기간을 지나면 트레이스와 알림 이력을 자동 삭제할 수 있다. 기본은 무제한 (업그레이드만으로 데이터를 잃지 않음).
- OPS-4 이전 버전 DB를 그대로 두고 업그레이드해도 기동된다 (누락 컬럼·인덱스 자동 추가, 데이터 보존).
- OPS-5 단일 컨테이너로 API만 서빙하고, liveness/readiness를 분리한다. UI는 별도 Windows 데스크톱 앱이다.
- OPS-6 웹페이지가 사용자의 브라우저를 통해 로컬 API를 조작하지 못한다 (교차 출처 쓰기, DNS rebinding).

## Design

### 인증 (`core/security.py`)

- `X-API-Key: <키>` 또는 `Authorization: Bearer <키>`, `hmac.compare_digest`로 비교.
- `API_KEY` 미설정 = 개방 모드 + 시작 시 경고 로그. 빈 문자열도 미설정으로 본다.
- 설정 키는 앞뒤 공백 제거 후 공백 없는 출력 가능한 ASCII만 허용한다. 비 ASCII 인증 헤더는 401로 거부한다.
- `REQUIRE_AUTH_FOR_READS=true`면 조회 API도 키 필요. 공개: `/health`, `/api/v1/system/ready`.

### 원문 보호 (`core/sanitize.py`)

- 메타데이터 키 이름에 `prompt, completion, response, message, content, answer, question, query, input_text, output_text, system, choices, transcript, document, context_text`가 포함되면 값을 재귀적으로 `[redacted]`.
- 요청 본문은 로그에 남기지 않는다. 접근 로그는 메서드·경로·상태·소요 시간·`X-Request-ID`만.
- 500 응답은 일반 메시지만 반환한다. 전역 오류·DB readiness 로그에는 예외 종류만 기록하며 예외 문자열·체인·SQL 매개변수는 기록하지 않는다(`hide_parameters=True`).
- 스케줄러 작업·데스크톱 자동 실행 설정 실패도 예외 종류만 기록한다. 스케줄러의 `last_alert_error`는 작업 이름·예외 종류만 유지하며 다음 주기에 재시도한다. exe `--self-test` 실패는 고정 문구·예외 종류만 stderr에 남기고 종료 코드 1을 반환한다. 이 경로들에서 traceback·예외 문자열·원인 체인을 출력하지 않는다.
- `httpx`·`httpcore` 로그는 WARNING 이상으로 제한한다. Uvicorn 접근 로그는 비활성화하고 쿼리 문자열 없이 앱의 요청 메타데이터 로그만 사용한다(Docker도 `--no-access-log`).
- FastAPI `RequestValidationError`의 422 응답은 `detail` 배열의 `loc`·`type`과 고정 메시지 `Invalid field value`만 반환한다. 제출한 `input`·`ctx`·원래 오류 메시지를 직렬화하지 않아 원문 반사와 비유한 수/거대 정수의 오류 직렬화 실패를 막는다. 키 라우터는 기존 일반 `Invalid key request` 메시지를 유지한다.

### HTTP 계층 (`main.py`)

- 미들웨어 순서: **오류 응답을 만드는 계층의 최외곽은 CORS** — 오류 응답에도 CORS 헤더가 붙는다. 그 바깥의 요청 ID 계층은 응답 헤더만 추가하며 오류 응답을 만들지 않는다.
- `CORS_ORIGINS`에 `*`가 있으면 credentials 자동 비활성화. 노출 헤더: `X-Request-ID`, `X-Total-Count`.
- 모든 요청에 `X-Request-ID` (전달값 또는 16자 hex 발급). Host·Origin 거부, 413·400, CORS 프리플라이트에도 적용한다. 프리플라이트도 Host 허용 검사를 한다.
- 본문은 `Content-Length` 사전 검사와 ASGI 수신 바이트 누적 검사를 함께 사용한다. chunked·길이 누락·축소 선언에도 `MAX_REQUEST_BYTES`(기본 8 MiB) 초과 청크를 JSON 버퍼에 넘기기 전에 413으로 중단하고 `Connection: close`로 남은 스트림을 정리한다. 음수·잘못된 길이는 400이다.
- 브라우저 공격 차단(`reject_browser_attacks`, CORS 안쪽): `Origin`이 `CORS_ORIGINS` 밖인 쓰기 요청(GET·HEAD·OPTIONS 제외)은 403. `*`이면 검사하지 않는다. API 키 유무와 관계없이 Host가 `localhost`·`127.0.0.1`·`[::1]`·`ALLOWED_HOSTS`가 아닌 요청을 403으로 거부한다(DNS rebinding). 데스크톱 앱·SDK는 `Origin`을 보내지 않는다.
- CORS 기본 출처는 로컬 API 포트 8000이며 웹 개발 서버 출처는 없다. 정적 파일·SPA fallback은 없고 `GET /`는 JSON 404다. Swagger·ReDoc API 문서는 유지한다.

### 보관·정리 (`services/retention.py`)

- `RETENTION_DAYS`(기본 0 = 무제한) > 0이면 스케줄러가 `RETENTION_INTERVAL_SECONDS`(3600)마다 기준일 이전 `trace_events`·`alert_histories` 삭제.
- 수동: `POST /api/v1/system/purge?days=N` (쓰기 인증). API의 `days ≤ 0` 또는 UTC 기준일에서 빼면 1년 이전이 되는 값은 422이며 삭제하지 않는다. 서비스 함수의 `days ≤ 0`은 삭제 없이 반환한다.
- `RETENTION_DAYS`는 0 또는 현재 UTC 날짜의 ordinal − 1 이하만 허용한다. 설정 시 검증하고 삭제 직전에도 다시 검사해 `timedelta`·날짜 오버플로를 막는다.

### 스키마 업그레이드 (`db/session.py: ensure_schema`)

- 모델 메타데이터와 실제 스키마를 비교해 **누락 컬럼은 `ALTER TABLE ADD COLUMN`, 누락 인덱스는 `CREATE INDEX`.** 삭제·타입 변경은 하지 않으며 멱등.
- 모델 import를 명시적으로 수행(`_load_model_metadata`)해 import 순서에 의존하지 않는다.
- SQLite는 `BEGIN IMMEDIATE`로 프로세스 간 쓰기 잠금을 얻은 뒤 같은 연결에서 스키마를 다시 조회하고 컬럼·인덱스를 추가한다. 첫 테이블 생성도 이 잠금 안에서 수행하며 실패 시 DDL과 데이터를 함께 롤백한다.
- SQLite의 기존 ±Infinity는 같은 업그레이드 트랜잭션에서 정리한다: 비용은 `cost_usd=0, cost_source='unknown'`, 지연은 `latency_ms=0, latency_measured=false`. 기존 NOT NULL 스키마를 보존하고 nullable DB도 동일하게 처리한다. 유한 값·다른 필드는 보존하며 반복 실행해도 같다. PostgreSQL에는 이 데이터 정리를 적용하지 않는다(실행 검증은 SQLite만).
- 데스크톱 필드 `cache_write_1h_tokens DEFAULT 0`, `latency_measured DEFAULT 1`과 복합 인덱스 `(latency_measured, timestamp, id)`도 같은 업그레이드 경로로 추가한다. 과거 지연 값은 보존한다.
- 계정 필드 `account_id`는 nullable로 추가하며 기존 행은 `NULL`을 유지한다. 단일 인덱스와 복합 인덱스 `(account_id, timestamp)`를 함께 추가한다.
- SQLite: WAL, `busy_timeout=15000`, `PRAGMA foreign_keys=ON`. 시각 컬럼은 SQLModel 버전과 관계없이 naive UTC로 저장하도록 `sa_type=DateTime`을 명시한다.

### 설정 (보안 관련)

| 설정 | 기본값 | 의미 |
| :--- | :--- | :--- |
| `API_KEY` | *(없음)* | 미설정 = 개방 모드. 모든 엔드포인트가 열리고 `localhost` 이름으로 온 요청에만 응답한다 |
| `REQUIRE_AUTH_FOR_READS` | `false` | `true`면 조회에도 키 필요 |
| `CORS_ORIGINS` | `http://localhost:8000`, `http://127.0.0.1:8000` | 이 밖의 브라우저 출처에서 온 쓰기는 403. `*`이면 credentials 비활성화 |
| `ALLOWED_HOSTS` | *(없음)* | API 키 유무와 무관하게 추가 허용할 Host 이름 (신뢰하는 네트워크) |
| `ALERT_ALLOW_PRIVATE_WEBHOOKS` | `false` | 기본은 공개 유니캐스트 Webhook 대상만 허용하고 userinfo를 거부한다. `true`면 사설·루프백·링크 로컬·예약·멀티캐스트 주소와 userinfo를 허용한다. 저장·발송 전에 검사하고 리디렉션은 따르지 않는다. DNS 검사와 실제 연결 사이의 rebinding 경쟁은 남는다 ([alerts.md](alerts.md)) |
| `RETENTION_DAYS` | `0` (무제한) | 설정 시 오래된 기록을 주기적으로 삭제 |

전체 목록과 설명은 `.env.example`이 정본이다.

### 배포

API 서버만 따로 띄우기 (데스크톱 없이 수집):

```bash
cp .env.example .env
python -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))" >> .env
docker compose up --build -d   # http://localhost:8000/api/v1, Swagger: /api/v1/docs
```

Docker의 `0.0.0.0` 바인딩 등을 외부에 노출하려면 `API_KEY`와 `REQUIRE_AUTH_FOR_READS=true`를 함께 설정하고, `ALLOWED_HOSTS`에는 사용할 호스트 이름만, `CORS_ORIGINS`에는 신뢰하는 정확한 출처만 허용한 뒤 `docker-compose.yml`의 포트 바인딩을 바꾼다. API 키 설정만으로 임의 Host가 허용되지는 않는다.

- Dockerfile: `python:3.11-slim` API 전용 런타임, non-root `aimonitor` 사용자. 연결 AI API가 사용하는 데스크톱 공유 헬퍼만 포함하며 GUI는 실행하지 않는다.
- Docker 빌드 컨텍스트에서 DB·SQLite WAL/SHM/journal·`.env.*`·`.review/`를 제외한다. `.env.example`은 예외로 포함한다.
- `docker-compose.yml`: 포트 `127.0.0.1:8000`만 바인딩, DB는 볼륨 `/app/data`, readiness 헬스체크.
- 시스템 API: `GET /system/info`(버전·인증 여부·스케줄러 상태), `GET /system/pricing`, `GET /system/ready`(DB 확인), `GET /health`(DB 미접근).
- CI(`.github/workflows/ci.yml`): pytest(Linux Python 3.10/3.11/3.12, Windows 3.11 + 데스크톱 의존성), ruff, Docker 빌드 후 readiness 확인.
- 테스트는 앱 import 전에 `DATABASE_URL`을 임시 SQLite로 고정한다. readiness의 직접 엔진 연결도 사용자 DB를 열지 않는다.
- CI·Docker·Windows 릴리스는 루트 `constraints.txt`를 `pip install -c`로 적용한다. Windows Python 3.11.9에서 전체 테스트·exe smoke를 통과한 런타임·데스크톱·PyInstaller 및 하위 의존성 버전을 고정한다. Windows 환경에 없는 Linux/Python 3.10 조건부 의존성은 공식 PyPI 메타데이터로 호환 버전을 확인하고 CI 행렬에서 실행 검증한다. 도구인 pip·setuptools는 고정하지 않는다.
- Python 3.10 백엔드는 해당 Python을 지원하는 SQLAlchemy 2.0.52·websockets 16.1.1과 조건부 하위 의존성을 사용한다. Python 3.11 이상은 검증된 Windows 기준 버전을 사용한다. 메타데이터상 의존성 해석을 확인했으며 Python 3.10의 실제 실행 확인은 CI가 맡는다.
- 릴리스(`.github/workflows/release.yml`): `v*` 태그에서 Windows exe 빌드 → `--self-test`·백엔드 기동·기록 저장 smoke test → zip·SHA256을 **초안** 릴리스에 첨부. 게시는 사람이 한다.
- CI와 릴리스 빌드는 `contents: read`, checkout은 `persist-credentials: false`다. 태그 push가 성공한 경우만 별도 publish job에 `contents: write`를 주며, 내려받은 zip의 SHA256을 검증한 뒤 초안을 만든다. 수동 실행은 artifact만 남긴다.
- SHA256 파일은 BOM 없는 ASCII·LF로 만들어 Windows 빌드와 Linux 게시 job 사이에서도 같은 형식을 사용한다.
- 외부 Actions는 공식 태그의 전체 커밋 SHA에 고정하고 버전 주석을 남긴다. `pull_request_target`은 사용하지 않는다.
- 의존성 설치·빌드 전에 태그의 기본 버전과 `pyproject.toml`·`Settings.VERSION` 일치를 검사한다. 태그는 `vMAJOR.MINOR.PATCH` 또는 `vMAJOR.MINOR.PATCH-(alpha|beta|rc).N`(N ≥ 1)을 허용하며 선행 0·빌드 메타데이터는 허용하지 않는다. prerelease 태그의 초안에는 prerelease 표시를 붙인다.

## Open questions

- 요청 속도 제한 → 결정: 앱에 넣지 않는다. 모니터링 데이터를 조용히 버리는 부작용이 커서 리버스 프록시 계층에 둔다.
- 사용자별 계정·권한 → 이월 (intent Open questions).

## Areas of concern

- 원문 차단이 **키 이름 기반**이다. `{"note": "<프롬프트 원문>"}`처럼 무해한 키에 원문을 넣으면 통과한다. `error_message`도 업스트림 오류에 프롬프트 일부가 섞이면 그대로 저장된다(2000자 절단만).
- 단일 공유 키라 키를 바꾸면 모든 SDK·데스크톱 앱을 동시에 갱신해야 한다. 키 회전 절차가 문서화돼 있지 않다.
- 다중 워커(`uvicorn --workers N`)에서는 각 워커가 스케줄러를 띄운다. 알림 중복은 CAS로 막지만 보관 작업은 N번 실행된다(멱등이라 결과는 같음).
