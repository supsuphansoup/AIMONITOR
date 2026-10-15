# Changelog

동작 규칙의 현재 상태는 [docs/spec/](docs/spec/README.md)에 있다. 이 파일은 버전별 변경과 업그레이드 주의사항만 기록한다.

## v0.6.1 — 2026-10-15 · 위젯 폭 조절, 모드 전환 수정

- 위젯 폭을 좌우 끝을 끌어 조절한다. 한 줄·카드 모드별로 저장하고, 한 줄 모드의 넘치는 문구와 카드 행의 오른쪽 설명은 `…`로 줄인다. 우클릭 `폭 자동 맞춤`으로 되돌린다.
- 수정: 위젯을 카드 모드에서 한 줄 모드로 바꾸면 오류로 갱신이 멈춰, 앱을 다시 켤 때까지 값이 그대로였다.

## v0.6.0 — 2026-10-15 · 데스크톱 전용 UI, 계정별 사용량, Windows exe

**동작 변경 (BREAKING)**
- 웹 대시보드 제거. UI는 Windows 데스크톱 앱(메인 창·트레이·위젯)만 제공한다. `GET /`는 JSON 404이며 `/api/v1/**`·`/health`·SDK·CSV는 유지한다.
- `AIMONITOR.bat`·`AIMONITOR.vbs` 실행기 제거. exe 또는 `pythonw run_desktop.pyw`를 사용한다. 쓰지 않던 `desktop/claude_watcher.py`·`antigravity_syncer.py`·`claude_statusline_hook.py`·`window.py`도 제거.
- API 키 설정과 관계없이 Host가 `localhost`·`127.0.0.1`·`[::1]`·`ALLOWED_HOSTS`인 요청에만 응답한다. 다른 호스트 이름(예: Docker 네트워크의 서비스 이름)으로 쓰던 곳은 `ALLOWED_HOSTS`에 이름을 추가해야 한다. 외부에 공개하려면 `API_KEY`, `REQUIRE_AUTH_FOR_READS=true`, 좁힌 `ALLOWED_HOSTS`·`CORS_ORIGINS`가 모두 필요하다.
- 입력 검증 오류(422)의 `detail` 항목은 `loc`·`type`과 고정 `msg: "Invalid field value"`만 담는다. 조회·CSV의 날짜 범위, 계정 필터 충돌, 상태 필터 오류도 같은 배열이다. 제출한 값과 검증 문맥은 돌려주지 않는다. 메시지 문구를 해석하던 클라이언트는 `loc`·`type`을 쓴다. 키 API의 일반 문자열 오류(`Invalid key request` 등)는 그대로다.
- `GET /keys/spend?force=true`는 400이다. 강제 갱신은 `POST /keys/spend/refresh`(쓰기 인증, 60초에 한 번, 넘으면 429)로 한다.
- 알림 규칙 응답의 `webhook_url`은 `scheme://host/…`로 가리고 `webhook_configured`를 함께 준다. 가린 값을 PATCH에 넣으면 422이며, URL을 유지하려면 필드를 생략한다. 사설·루프백 등 비공개 주소와 `user:pass@`가 든 Webhook은 거부한다(`ALERT_ALLOW_PRIVATE_WEBHOOKS=true`로 허용).
- 지연 표본이 상한에 걸린 창에서는 P50·P95·P99 알림 규칙을 평가하지 않는다. `POST /alerts/evaluate?include_skipped=true`는 `{histories, skipped_rules}`로 건너뛴 사유를 준다(기본 응답은 배열 그대로).
- 수집: `cost_usd`·`latency_ms`의 NaN·±Infinity·`1e309`와 10억을 넘는 값, 10억을 넘는 토큰 수, 지원 범위 밖 시각은 422다.
- SDK `observe`가 자동으로 남기는 오류는 예외 메시지 대신 예외 클래스 이름이다. `latency_ms`를 생략하면 미측정(null)으로 기록한다.
- CSV 내보내기는 UTF-8 BOM으로 시작한다(Excel 한글 인식). 프로그램으로 읽을 때는 `utf-8-sig`로 디코딩한다.
- 단가: Gemini 3.5 Flash·3.7 Flash는 공식 청구 단가를 확인할 수 없어 `계산 불가`가 된다.

**보안**
- 웹페이지가 브라우저를 통해 로컬 API를 조작하지 못하게 했다. `CORS_ORIGINS` 밖의 `Origin`을 가진 쓰기 요청(예: `POST /system/purge`)은 403이고, 개방 모드의 비로컬 Host(DNS rebinding)도 403이다.
- Antigravity·Copilot·VS Code 데이터베이스를 읽기 전용으로 연다.
- Claude 사용량 조회가 Claude Code를 흉내 내던 User-Agent(`claude-code/…`) 대신 `AIMONITOR/<버전>`으로 자신을 밝힌다.
- Webhook 대상을 저장·발송 전에 검사하고, 리디렉션과 환경 변수 프록시를 쓰지 않으며, 요청 전체를 5초에 끊는다.
- 길이를 밝히지 않은(chunked) 요청에도 8 MiB 본문 상한을 적용한다.
- 오류·DB·HTTP 클라이언트 로그에 예외 문구·SQL 매개변수·요청 URL을 남기지 않고, Uvicorn 접근 로그를 끈다. 스케줄러·자동 실행 설정·exe self-test의 실패도 예외 종류만 남긴다. 수집 메타데이터는 중첩 리스트까지 정제하고 깊이·원소 수를 제한한다.
- 저장된 서버 인증 키를 이 Windows 계정에서 복호화할 수 없으면 설정 화면에 알리고, 다른 설정을 저장해도 암호문을 지우지 않는다. 키를 다시 입력하거나 삭제할 때만 바꾼다.
- 데스크톱(수집기·메인 창·위젯)은 서버 인증 키 없이 `/health`로 AIMONITOR임을 확인한 뒤에만 키를 붙여 조회·수집한다. 설정 포트를 다른 프로그램이 쓰고 있으면 키와 사용 기록을 보내지 않고, 모아 둔 기록은 충돌이 풀린 뒤 보낸다.
- 공급자 키 파일을 원자적으로 저장하고 데스크톱·서버 사이에 잠금을 건다. 검증은 지정한 키에만 보낸다.
- CI·Docker·릴리스가 `constraints.txt`로 의존성을 고정하고, Actions를 커밋 SHA로 고정한다. 빌드는 읽기 권한만 쓰고 태그 게시만 쓰기 권한을 가지며, 태그 버전과 산출물 SHA256을 검사한다. Docker 빌드 문맥에서 `.env.*`·SQLite 부속 파일을 빼고, Git도 `.env.*`(`.env.example` 제외)·SQLite 부속 파일·`.review/`를 무시한다.
- [개인정보 문서](docs/privacy.md)에 사용자 지정 DB(`DATABASE_URL`)·Docker 볼륨·원격 서버·백업의 별도 삭제, 서버 `.env`·환경 변수, 수집기 잠금 파일, 단일 인스턴스 로컬 통신을 추가했다.

**추가**
- Windows exe(`AIMONITOR.exe`, PyInstaller 단일 폴더). Python 설치 없이 실행하며 데이터는 `~/.aimonitor`에 둔다. `v*` 태그에서 빌드·smoke test 후 초안 릴리스에 첨부.
- 계정별 사용량: Claude·Codex 설정 폴더(기본, `CLAUDE_CONFIG_DIR`·`CODEX_HOME`, `~/.claude-*`·`~/.codex-*`, 수동 추가)를 모두 수집하고 계정별로 나눈다. 트레이스에 `account_id`(SDK 포함), 조회·CSV·집계에 계정 필터.
- 공급자별 API 키 여러 개(라벨·키별 청구액), 기존 단일 키는 "기본"으로 이관.
- 설정의 Windows 시작 시 자동 실행 토글.
- 수집 `dedupe_key`(선택): 같은 키는 한 번만 저장하고 중복에도 201과 기존 행을 돌려준다. 데스크톱 수집기는 안정적인 키를 보내 재시도 중복을 막는다.
- `POST /keys/{provider}/{key_id}/verify`와 메인 창의 키별 `검증` 버튼. 키 상태에 `storage_error`·`unreadable`·`invalid`·`verification_unavailable`을 구별한다.
- 시계열 버킷별 `cost_unknown_count`. `GET /system/pricing`에 1시간 캐시 쓰기·장문 구간·시행 기간.
- `active_contexts`·`connected_ais`의 사용 창별 `record_count`(기록 수). Copilot의 `requests`는 세션의 실제 요청 수다.
- exe 배포 폴더에 `THIRD_PARTY_NOTICES.txt`·`licenses/`(번들 구성요소의 라이선스 원문과 고지).
- `LICENSE`(MIT). CI에 Windows 데스크톱 job — GUI·DPAPI 테스트가 CI에서 실행된다.

**수정**
- SQLModel 0.0.48 이상에서 서버가 모든 기록 저장에 실패하던 문제. 새 버전은 `datetime` 컬럼을 시간대 필수 형식으로 만들어 naive UTC 값을 거부한다. 시각 컬럼을 `DateTime`으로 명시해 버전과 관계없이 기존 저장 방식을 유지한다.
- Linux에서 단일 인스턴스 포트가 해제되지 않던 문제.
- 비용: 공식 캐시 단가와 과거 모델 매칭을 바로잡고, 확인된 장문 구간과 Gemini 3.6·3.8 Flash 프로모션 종료(2027-01-01 00:00 UTC)를 반영했다. 비용은 기록 시각에 유효한 단가로 계산하며, 비유한 결과는 `계산 불가`로 둔다.
- 집계: 동시 수집 중에도 KPI가 한 시점의 값으로 계산되고, 시계열 `partial`이 실제로 잘린 버킷에만 붙는다. rolling 창의 미래 행 제외, DST 경계·날짜 범위 검증, 도구·계정 집계의 쿼리 수 감소. SDK로 기록한 Ollama 사용량이 연결 AI·메인 창·위젯에서 0으로 보이던 문제와 Copilot 요청 수를 기록 수로 세던 문제.
- 알림: 쿨다운 선점과 발송 대기 이력을 한 트랜잭션으로 저장해 다중 워커 중복과 유실을 막고, 결과 저장이 실패해도 대기 이력을 남긴다. 집계 직후의 SQLite 잠금 오류, 일시정지 경합, 입력 null·비유한 임계값, Discord URL 판별, 스케줄러 종료를 고쳤다.
- 수집: 응답 검증을 커밋 전에 끝내 저장 뒤 오류로 재전송되던 경계를 없앴다. 단건 조회에서 같은 시각의 행은 최신 id를 고른다. CSV의 전체 건수·부분 표시 헤더와 행을 같은 읽기 시점으로 맞췄다.
- SDK: `flush`가 전송 중인 기록까지 기다리고 결과(bool)와 `pending_count`·`dropped_count`를 준다. `close`·`a_close`는 남은 기록을 보내고 종료한다. 잘못된 usage extractor가 사용자 함수의 결과·예외를 바꾸지 않는다.
- 키: 공식 청구값을 검증하고, UTC 달이 바뀌면 월간 캐시를 다시 조회한다.
- 데스크톱: 설정을 원자적으로 저장하고 잘못된 값은 그 항목만 복구한다. 위젯에 기준 시각·오래됨·추정을 표시하고, 연결이 끊기면 이전 값임을 알린다. 단가 미상 모델을 0원으로 그리지 않는다. 백엔드 자식 프로세스 종료 경합, 다른 프로그램의 포트 점유 구별, 모니터 작업 영역 밖 위젯 복구, 폴링 중복, 환율 신선도(7일·미래 1일) 검사, exe 로그 크기 제한을 고쳤다. 설정 포트에 다른 프로그램이 응답하면 AIMONITOR로 착각하지 않고 포트 충돌을 안내하며(재시작·조회 중단), 메인 창·위젯 모두 형식이 다른 사용량 응답을 0으로 표시하지 않는다. 공식 사용액 갱신이 실패하면 이전 금액에 실패 표시와 마지막 성공 시각을 붙이고, 복수 계정 한도 카드에도 기준 시각·오래됨을 표시한다.
- 계정 수집: 로그 파일 교체·잘림·부분 쓰기·손상된 줄을 구별하고, 실패한 전송을 같은 `dedupe_key`로 다시 보낸다. 서버가 거부할 기록(토큰·합계·시각·지연 범위 밖)은 건너뛰고 건수만 남겨 다른 기록의 수집이 멈추지 않으며, 서버가 개별 기록을 거부하면 그 기록만 격리하고, 형식이 다른 성공 응답에는 읽은 위치를 진행하지 않는다. Codex는 새로 쓴 부분만 읽고 기록 시각으로 최신 한도를 고르며, Copilot의 읽기 실패·비용 누락과 잘못된 한도 창은 `확인 불가`로 둔다. Antigravity는 확인 가능한 본문 길이로만 추정하고 모델·공급자를 추측하지 않는다. 같은 Claude 계정의 여러 폴더는 한도 조회를 공유하며, 폴더 이동·계정 전환 뒤에도 현재 계정의 로그인으로만 조회한다. 한도 캐시·재시도 대기는 시스템 시각 변경의 영향을 받지 않고, 복사된 Copilot 세션 파일을 월 프리미엄 한도에 두 번 세지 않는다. Claude·Codex는 계정별 로그인 근거로 API 청구·구독 환산을 구분하며, 근거 미상·기간 중 방식 변경의 금액은 `구분 미상`으로 따로 표시한다.
- 운영: 보관 기간을 날짜 계산 가능 범위로 검증하고, SQLite 스키마 업그레이드를 프로세스 사이에 직렬화하며 실패하면 되돌린다. 테스트가 사용자 DB를 열지 않는다.

**업그레이드**: `trace_events.account_id` 컬럼과 `(account_id, timestamp)` 인덱스, `dedupe_key` 컬럼과 유일 부분 인덱스는 시작 시 자동 추가된다. 과거 행은 `account_id=null`(계정 미상). 이전 DB에 남은 무한대 비용·지연은 `cost_source='unknown'`·`latency_measured=false`로 정리하며 유한 값은 그대로 둔다. 과거 행의 비용은 재계산하지 않는다. exe는 소스 실행과 다른 DB(`~/.aimonitor/aimonitor.db`)를 쓴다.

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
