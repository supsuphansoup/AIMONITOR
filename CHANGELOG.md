# Changelog

동작 규칙의 현재 상태는 [docs/spec/](docs/spec/README.md)에 있다. 이 파일은 버전별 변경과 업그레이드 주의사항만 기록한다.

## v0.6.0 — 2026-10-08 · 데스크톱 전용 UI, 계정별 사용량, Windows exe

**동작 변경 (BREAKING)**
- 웹 대시보드 제거. UI는 Windows 데스크톱 앱(메인 창·트레이·위젯)만 제공한다. `GET /`는 JSON 404이며 `/api/v1/**`·`/health`·SDK·CSV는 유지한다.
- `AIMONITOR.bat`·`AIMONITOR.vbs` 실행기 제거. exe 또는 `pythonw run_desktop.pyw`를 사용한다. 쓰지 않던 `desktop/claude_watcher.py`·`antigravity_syncer.py`·`claude_statusline_hook.py`·`window.py`도 제거.
- `API_KEY`가 없으면 `localhost`·`127.0.0.1`·`[::1]`로 들어온 요청에만 응답한다. 다른 호스트 이름(예: Docker 네트워크의 서비스 이름)으로 쓰던 곳은 `API_KEY`를 설정하거나 `ALLOWED_HOSTS`에 이름을 추가해야 한다.

**보안**
- 웹페이지가 브라우저를 통해 로컬 API를 조작하지 못하게 했다. `CORS_ORIGINS` 밖의 `Origin`을 가진 쓰기 요청(예: `POST /system/purge`)은 403이고, 개방 모드의 비로컬 Host(DNS rebinding)도 403이다.
- Antigravity·Copilot·VS Code 데이터베이스를 읽기 전용으로 연다.

**추가**
- Windows exe(`AIMONITOR.exe`, PyInstaller 단일 폴더). Python 설치 없이 실행하며 데이터는 `~/.aimonitor`에 둔다. `v*` 태그에서 빌드·smoke test 후 초안 릴리스에 첨부.
- 계정별 사용량: Claude·Codex 설정 폴더(기본, `CLAUDE_CONFIG_DIR`·`CODEX_HOME`, `~/.claude-*`·`~/.codex-*`, 수동 추가)를 모두 수집하고 계정별로 나눈다. 트레이스에 `account_id`(SDK 포함), 조회·CSV·집계에 계정 필터.
- 공급자별 API 키 여러 개(라벨·키별 청구액), 기존 단일 키는 "기본"으로 이관.
- 설정의 Windows 시작 시 자동 실행 토글.
- `LICENSE`(MIT). CI에 Windows 데스크톱 job — GUI·DPAPI 테스트가 CI에서 실행된다.

**수정**
- SQLModel 0.0.48 이상에서 서버가 모든 기록 저장에 실패하던 문제. 새 버전은 `datetime` 컬럼을 시간대 필수 형식으로 만들어 naive UTC 값을 거부한다. 시각 컬럼을 `DateTime`으로 명시해 버전과 관계없이 기존 저장 방식을 유지한다.
- Linux에서 단일 인스턴스 포트가 해제되지 않던 문제.

**업그레이드**: `trace_events.account_id` 컬럼과 `(account_id, timestamp)` 인덱스는 시작 시 자동 추가된다. 과거 행은 `account_id=null`(계정 미상). exe는 소스 실행과 다른 DB(`~/.aimonitor/aimonitor.db`)를 쓴다.

## v0.5.0 — 2026-10-06 · 알림·내보내기·집계 정합성, 단가표 갱신

**수정**
- 알림: 요청이 없는 시간 창에서 지연·에러율 규칙을 평가하지 않는다 (0 ms로 보고 `p95 < 100` 같은 규칙이 오발동하던 문제). 요청 수·비용·토큰 규칙은 계속 평가 — 무트래픽 감시는 동작.
- 알림: `계산 불가` 건이 있는 창에서 비용 규칙은 알려진 비용을 하한값으로 본다. `>`·`>=`는 평가하고 메시지에 미상 건수를 붙이며, 나머지 연산자는 건너뛴다.
- CSV: `=` `+` `-` `@` 탭·CR로 시작하는 문자열 값 앞에 `'`를 붙여 스프레드시트 수식 실행을 막는다.
- 집계: `cost_source='unknown'` 행의 비용을 overview·timeseries·breakdown 합계에서 제외 (v0.1.0에서 업그레이드한 DB의 추정 비용이 섞이던 문제).
- 대시보드: `ENVIRONMENT=production`이거나 환경을 알 수 없으면 데모 트래픽 버튼을 숨긴다.
- 단가표: Anthropic·OpenAI·Gemini 최신 모델을 공식 가격으로 추가하고 모델별 캐시 단가를 명시. 공식 가격과 다르던 `o3`($10/$40→$2/$8), `gemini-2.5-flash`($0.075/$0.30→$0.30/$2.50), `gpt-4o-2024-05-13`($2.50/$10→$5/$15) 수정. 확인할 수 없는 `gemini-2.5-flash-lite`, `gemini-2.0-pro-exp` 제거.

**추가**
- `GET /system/pricing`에 `as_of`(단가 확인일), `sources`(공식 가격 페이지).

**동작 변경**: 일부 알림 규칙이 전보다 덜 울린다(위 두 항목). `gemini-2.5-flash-lite`·`gemini-2.0-pro-exp`는 `계산 불가`가 된다.
Gemini 3.6–3.8 Flash는 2026-12-31까지 프로모션 가격 — **2027-01-01 전에 단가표를 갱신해야 한다.**

**업그레이드**: 스키마 변경 없음. 과거 행의 비용은 재계산되지 않는다.

## v0.4.0 — 2026-10-06 · 비용 정확도

"모르면 `계산 불가`, 알면 정확히" 원칙을 깨던 결함 4건을 고쳤다. 모두 재현 후 수정.

**수정**
- 모델명 부분 문자열 매칭이 다른 모델 단가를 씌움 (`gpt-4.5-preview` → `gpt-4` 단가, `o3-pro` → `o3`).
  이제 단가표 키 뒤에는 날짜·리비전·`-latest`·Bedrock/Vertex 버전 태그만 허용하고, 그 외는 `unknown`.
- 유료 API의 오픈 웨이트 모델(`mistral/mistral-large-latest`, `deepseek/deepseek-chat`)이 `local_free` $0으로 처리됨.
  오픈 웨이트 0원 항목을 단가표에서 제거했다. 무료 여부는 provider(`ollama`, `vllm`, `llamacpp`, `lmstudio`, `local`)로만 판단한다.
- SDK: OpenAI의 `prompt_tokens_details.cached_tokens`(Responses API는 `input_tokens_details`)를 읽지 못해 캐시 할인이 빠짐.
- SDK: Gemini의 `prompt_token_count`에 포함된 캐시 토큰을 이중 계산함. OpenAI·Google 모두 `prompt_tokens`에서 캐시분을 뺀다.

**추가**
- `GET /system/pricing`에 `local_free_providers`.

**동작 변경**: 지금까지 단가가 붙던 일부 모델명(`gpt-4-32k`, `*-preview` 등)이 `계산 불가`로 바뀐다.
provider가 로컬이 아닌 오픈 웨이트 호출도 `계산 불가`가 된다 — 필요하면 SDK에서 `cost_usd`를 직접 전달할 것.

**업그레이드**: 스키마 변경 없음. 과거 행의 비용은 재계산되지 않는다. SDK 보정은 SDK를 0.4.0으로 올려야 적용된다.

## v0.3.0 — 2026-08-28 · 2차 하드닝

v0.2.0에서 새로 넣은 코드를 재감사했다. 수정한 결함은 모두 오류 없이 **조용히 틀린 결과**를 내는 종류였다.

**수정**
- 시계열 상한이 `ASC LIMIT`라 최신 구간이 잘림 → 최신 우선, 잘린 버킷은 `partial: true`.
- 백분위수 표본이 오래된 행에서 추출됨 → 최신순 표본.
- `ignore_cooldown` 수동 평가가 모든 규칙의 쿨다운을 초기화 → 상태를 바꾸지 않는 평가로 변경.
- 다중 워커에서 같은 알림 중복 발송 → 조건부 UPDATE(`claim_rule`)로 발동 권한 선점.
- 500 응답에 CORS 헤더 누락 → CORS 미들웨어를 최외곽으로.
- 업그레이드 시 인덱스 미생성, `ensure_schema`의 import 순서 의존 → 인덱스 생성·메타데이터 명시 로드.
- 프론트엔드: `X-Total-Count` 헤더가 없을 때 0건으로 표시되던 문제.

**추가**
- `MAX_REQUEST_BYTES`(8 MiB) 본문 크기 제한(413), `X-Total-Count` 기반 페이지네이션, CSV 내보내기.
- 같은 시간 창 규칙의 집계 공유, Vitest 프론트엔드 테스트 53개, Postgres/MySQL dialect 컴파일 테스트.

**검증**: 백엔드 153 + 프론트엔드 53 테스트 통과, ruff·빌드 통과.

**업그레이드**: 스키마 변경 없음. 첫 기동 시 누락 인덱스 생성으로 몇 초 지연될 수 있다.
"Evaluate Now"가 더 이상 쿨다운을 초기화하지 않는다 — 이 부작용에 의존했다면 `cooldown_minutes`를 조정할 것.

## v0.2.0 — 2026-08-28 · 실사용 준비

**수정**
- 오프셋 없는 타임스탬프 응답으로 대시보드가 9시간 어긋남, 입력 오프셋이 버려짐 → UTC 저장·오프셋 포함 응답.
- 미등록 모델에 임의 단가($1/$3) 적용 → `cost_source` 도입, 미상은 `계산 불가`.
- 알림이 수동 평가로만 동작 → 백그라운드 스케줄러(60초) + 규칙별 쿨다운.
- 집계가 전체 행을 메모리로 적재 → SQL 집계 + 표본 상한(`latency_sampled`).
- 오류 응답의 예외 문자열 노출, 배치 크기 무제한, 규칙 삭제 시 고아 이력, CORS `*` + credentials 등.

**추가**
- API 키 인증, 캐시 읽기/쓰기 단가 분리, 메타데이터 원문 자동 제거, `RETENTION_DAYS` 보관 정책, 무중단 컬럼 추가.
- `/system/ready`·`/system/info`·`/system/pricing`, `X-Request-ID`.
- SDK: 응답에서 토큰 자동 추출, 배경 전송, API 키. 대시보드: 기간 전환, 연결 표시등, 키 입력, 규칙 일시 중지.
- GitHub Actions CI.

**검증**: 113 테스트 통과(v0.1.0: 30).

**업그레이드**: 기존 SQLite 파일 그대로 사용 가능, 누락 컬럼 자동 추가.
**기존 비용 값은 재계산되지 않는다** — v0.1.0의 추정 금액은 남고 `cost_source`만 `unknown`이 된다.
외부 노출 전 반드시 `API_KEY`를 설정할 것.

## v0.1.0 — 2026-08-18 · 최초 구현

FastAPI 수집·집계 API, 모델별 비용 계산, Webhook 알림, React 대시보드, Python SDK, Docker 단일 컨테이너.
