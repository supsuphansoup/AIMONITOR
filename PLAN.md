# K-LLM Monitor: 개발 계획

- 문서 개정: rev.4 (2026-08-16)
- 기준 문서: `PRD.md` (rev.4)
- 목표 제품 버전: v0.1.0 (MVP)
- 목표: Windows용 Claude Code 사용량 모니터 MVP 구현
- 개발 방식: 단계별 바이브 코딩
- 우선순위: 동작하는 수집기 → 정확한 집계 → 사용 가능한 UI → 배포 안정화

> 문서 개정 번호(rev.N)와 제품 버전(v0.1.0 등)은 별개다. §9의 v0.2 / v0.3은 제품 버전을 가리킨다.

## 1. 개발 원칙

1. 한 번에 큰 기능을 만들지 않고 작은 수직 슬라이스로 구현한다.
2. 각 단계가 끝날 때마다 실제 실행 가능한 상태를 유지한다.
3. Claude Code 원본 로그에 의존하는 부분과 제품 내부 로직을 분리한다.
4. 모든 파서에는 샘플 데이터와 회귀 테스트를 추가한다.
5. 원문 프롬프트와 응답은 기본적으로 저장하지 않는다.
6. 현재 MVP는 Claude Code만 지원하고, 다른 AI 수집기는 확장 지점만 만든다.
7. 추정값과 실제 제공값을 코드와 UI에서 구분한다.

## 2. MVP 목표

최종 사용자는 다음 경험을 가져야 한다.

```text
설치 파일 실행
→ 앱이 Claude Code를 자동 발견
→ 기존 사용량을 불러옴
→ 새 세션을 자동 감시
→ 트레이에서 토큰·컨텍스트·원화 비용 확인
→ 클릭하면 한국어 대시보드 확인
```

MVP 완료 조건:

- Windows에서 설치·실행된다.
- Claude Code 로컬 데이터를 자동 발견한다.
- 기존 및 신규 세션을 읽는다.
- 입력·출력·캐시·총 토큰을 집계한다.
- 세션·일·월·프로젝트별 통계를 표시한다.
- USD와 KRW 비용을 표시한다.
- 시스템 트레이에서 요약값을 보여준다.
- 기본 설정에서 원문을 저장하지 않는다.
- 앱 재시작 후 데이터가 유지된다.

## 3. 기술 스택

- 데스크톱 앱: Tauri 2
- 백엔드: Rust (edition 2021)
- 프론트엔드: React 18 + TypeScript 5
- 번들러: Vite
- 스타일: Tailwind CSS v3
- 차트: ECharts
- 데이터베이스: SQLite (WAL 모드, `PRAGMA foreign_keys = ON`)
- DB 접근: `rusqlite` + `refinery` 마이그레이션 (확정, `sqlx` 미사용)
- 파일 감시: Rust `notify` v6
- 패키지 매니저: pnpm (`pnpm-workspace.yaml` 사용)
- 린터/포맷터: Biome (프론트엔드, 확정), `rustfmt` + `clippy` (Rust)
- 프론트엔드 테스트: Vitest
- E2E 테스트: `tauri-driver` + WebdriverIO (Playwright는 Tauri 앱을 직접 구동할 수 없음)
- CI/CD: GitHub Actions
- 패키징: Tauri Bundler — **NSIS per-user 고정** (WiX MSI는 UAC를 유발하므로 미사용)

## 4. 권장 디렉터리 구조

```text
k-llm-monitor/
├── PRD.md
├── PLAN.md
├── README.md
├── LICENSE                       # MIT
├── package.json
├── pnpm-workspace.yaml           # apps/* 를 워크스페이스로 선언
├── pnpm-lock.yaml
├── .node-version
├── rust-toolchain.toml
├── apps/
│   └── desktop/
│       ├── src/
│       │   ├── app/
│       │   ├── components/
│       │   ├── charts/           # ECharts 래퍼 (FR-008 차트 4종)
│       │   ├── pages/
│       │   ├── hooks/
│       │   ├── lib/
│       │   ├── types/
│       │   └── main.tsx
│       ├── tests/                # Vitest 컴포넌트 테스트
│       └── src-tauri/
│           ├── src/
│           │   ├── collectors/
│           │   │   ├── mod.rs
│           │   │   └── claude_code.rs
│           │   ├── parser/
│           │   │   ├── mod.rs
│           │   │   └── jsonl.rs
│           │   ├── pricing/
│           │   │   ├── mod.rs
│           │   │   ├── claude.rs
│           │   │   └── claude-pricing.json   # 번들 가격표 (pricing_version 포함)
│           │   ├── exchange/
│           │   │   ├── mod.rs                # ExchangeRateProvider 트레이트
│           │   │   ├── manual.rs
│           │   │   └── online.rs
│           │   ├── storage/
│           │   │   ├── mod.rs
│           │   │   └── sqlite.rs
│           │   ├── aggregation/
│           │   ├── tray/
│           │   ├── commands.rs
│           │   └── main.rs
│           ├── migrations/
│           ├── fixtures/                     # Rust 테스트에서 CARGO_MANIFEST_DIR 기준으로 접근
│           │   └── claude-code/
│           │       ├── basic-session.jsonl
│           │       ├── multi-model-session.jsonl
│           │       ├── malformed-lines.jsonl
│           │       └── unicode-project.jsonl
│           ├── tests/                        # Cargo 통합 테스트 (parser, aggregation, watcher, commands)
│           └── tauri.conf.json
├── e2e/                          # tauri-driver + WebdriverIO
└── docs/
    ├── privacy.md
    └── troubleshooting.md
```

> fixture와 Rust 테스트는 `src-tauri/` 아래에 둔다. 루트 `tests/`는 Cargo가 통합 테스트로 인식하지 않고, 루트 `fixtures/`는 `CARGO_MANIFEST_DIR` 기준 상대 경로가 어긋난다.

## 5. 개발 규칙

### 코드 작성 규칙

- 타입을 먼저 정의한 뒤 구현한다.
- 파일 하나에 책임을 하나만 둔다.
- 파싱, 저장, 집계, UI를 직접 연결하지 않는다.
- 외부 데이터 필드는 nullable로 처리한다.
- 알 수 없는 JSON 필드는 무시한다.
- 오류를 조용히 삼키지 말고 구조화된 로그를 남긴다.
- 데이터 수집 함수는 멱등적으로 동작해야 한다.
- 모든 날짜·시간은 내부적으로 UTC 또는 ISO 8601로 저장한다.
- 화면 표시 시 한국 시간대와 한국식 숫자 형식을 적용한다.
- DB 컨커런트 쓰기에는 트랜잭션을 사용하여 데이터 일관성을 보장한다.
- 중복 방지는 애플리케이션 로직이 아니라 **DB의 UNIQUE 제약**을 최종 방어선으로 삼는다.
- 이벤트 식별 해시에는 파일 경로·오프셋 등 위치 정보를 넣지 않는다 (내용만으로 결정되어야 한다).
- 원문 페이로드는 도메인 타입·DB·로그·오류 기록 어디에도 넣지 않는다.
- Rust `#[allow(unused)]`를 프로덕션 코드에 사용하지 않는다 (컴파일러 경고 0개 목표).

### 바이브 코딩 규칙

AI 코딩 에이전트에게 한 번에 다음 전체를 요청하지 않는다.

```text
나머지 앱 전체를 완성해줘
```

대신 다음 형식을 사용한다.

```text
목표:
현재 상태:
변경 범위:
변경하지 않을 것:
수용 기준:
테스트 방법:
```

각 작업이 끝날 때마다 AI 에이전트에게 다음을 확인시킨다.

- 변경된 파일 목록.
- 실행한 테스트.
- 남은 오류.
- 다음 작업에 필요한 전제조건.

## 6. 개발 단계

### Phase 0. 프로젝트 부트스트랩

### 목표

실행 가능한 빈 Tauri 앱과 개발 문서 기반을 만든다.

### 작업

- [ ] 저장소 생성.
- [ ] `PRD.md`와 `PLAN.md` 추가.
- [ ] `LICENSE` 추가 (MIT).
- [ ] Tauri + React + TypeScript 프로젝트 생성 (`pnpm create tauri-app`).
- [ ] 생성 결과를 `apps/desktop/`으로 이동하고 `pnpm-workspace.yaml` 작성.
      (`pnpm create tauri-app`은 모노레포 구조를 만들어주지 않으므로 수동 재배치가 필요하다.)
- [ ] Rust 버전 고정 (`rust-toolchain.toml`에 channel = "stable" 명시).
- [ ] Node 버전 고정 (`.node-version`).
- [ ] 린터와 포맷터 설정 (Biome, `rustfmt` + `clippy`).
- [ ] `tauri.conf.json`에서 번들 타겟을 NSIS로, 설치 모드를 per-user로 설정.
- [ ] GitHub Actions에 Windows 빌드 작업 추가.
- [ ] Windows 로컬 빌드 확인.

### 완료 기준

```text
pnpm install
pnpm dev
pnpm tauri dev
```

명령으로 앱 창이 열리고, Rust와 프론트엔드 빌드가 모두 성공해야 한다.
`pnpm tauri build`를 실행하여 **NSIS per-user 설치 파일**이 생성되고, 설치 시 UAC 프롬프트가 뜨지 않는지 확인한다 (PRD FR-001).

이 단계에서 트레이는 구현하지 않는다. 트레이는 Phase 8 범위다.

### 바이브 코딩 프롬프트

```text
PRD.md의 MVP 범위를 기준으로 Tauri 2 + React + TypeScript 프로젝트의 기본 구조를 만들어줘.
apps/desktop 아래에 배치하고 루트에 pnpm-workspace.yaml을 만들어줘.
번들러는 NSIS per-user 모드로 설정한다 (WiX 사용 안 함).
이번 작업에서는 화면에 "K-LLM Monitor"만 표시하고, 데이터 수집·DB·트레이 기능은 구현하지 마.
실행 명령과 변경 파일 목록을 함께 알려줘.
```

### Phase 1. SQLite와 도메인 모델

### 목표

수집기와 UI가 의존할 내부 데이터 구조를 먼저 고정한다.

### 작업

- [ ] `sessions` 테이블 생성 (`external_session_id`, `session_key`, `last_model` 포함).
- [ ] `usage_events` 테이블 생성 (`local_date`, `local_month`, `event_hash` 포함).
- [ ] `costs` 테이블 생성 (`cost_source`, `provided_usd_amount`, `estimated_usd_amount` 포함).
- [ ] `file_offsets` 테이블 생성 (`file_key` 기준, 파일별 마지막 처리 오프셋 추적).
- [ ] `parse_errors` 테이블 생성 (FR-011의 오류 행 수를 재시작 후에도 유지).
- [ ] `daily_rollups` 테이블 생성 (보관 기간 만료 후 장기 추이 보존).
- [ ] `app_settings` 테이블 생성 (환율 마지막 정상값, `collector_paused_at` 키 포함).
- [ ] **인덱스와 제약 생성** — PRD §7 "인덱스와 제약 요약"을 그대로 반영:
      `usage_events(event_hash)` UNIQUE, `costs(usage_event_id)` UNIQUE,
      `sessions(source, session_key)` UNIQUE, `file_offsets(file_key)` UNIQUE,
      `usage_events(local_date)` / `(session_id)` / `(model)` 인덱스, FK ON DELETE CASCADE.
- [ ] `PRAGMA journal_mode = WAL`, `PRAGMA foreign_keys = ON` 연결 설정.
- [ ] 마이그레이션 실행 코드 작성 (멱등적 실행 보장, 스키마 버전 확인 후 필요한 것만 실행).
- [ ] Rust 도메인 타입 정의 (`Session`, `UsageEvent`, `Cost`, `FileOffset`, `ParseError`, `DailyRollup`).
- [ ] 프론트엔드 TypeScript 타입 정의 (Rust 타입과 1:1 대응).
- [ ] `local_date` / `local_month` 계산 유틸 (UTC → KST+9 고정 오프셋).
- [ ] DB 초기화 테스트 작성.
- [ ] 모든 날짜·시간 필드 UTC ISO 8601 저장 확인.

### 완료 기준

- 앱 최초 실행 시 DB가 자동 생성된다.
- 앱 재실행 시 기존 데이터가 유지된다.
- 마이그레이션이 두 번 실행되어도 오류가 발생하지 않는다.
- `file_offsets` CRUD가 정상 동작한다.
- 동일한 `event_hash`를 두 번 INSERT하면 두 번째는 조용히 무시되고 행 수가 늘지 않는다.
- 세션을 삭제하면 연결된 `usage_events`와 `costs`가 CASCADE로 함께 삭제된다.
- UTC `2026-08-13T15:30:00Z` 이벤트의 `local_date`가 `2026-08-14`로 계산된다 (KST 날짜 경계 검증).

### 바이브 코딩 프롬프트

```text
PRD.md rev.4의 §7 데이터 모델을 기준으로 SQLite 마이그레이션과 Rust 타입을 구현해줘.
테이블은 sessions, usage_events, costs, file_offsets, parse_errors, daily_rollups, app_settings를 만든다.
PRD의 '인덱스와 제약 요약'에 있는 UNIQUE 제약과 인덱스를 빠짐없이 생성해줘.
특히 usage_events(event_hash) UNIQUE는 중복 방지의 핵심이니 반드시 포함한다.
모든 날짜/시간은 UTC ISO 8601로 저장하고, 집계용 local_date/local_month(KST +9 고정)를 함께 저장한다.
rusqlite + refinery를 사용하고, WAL 모드와 foreign_keys ON을 설정해줘.
아직 Claude Code 파서는 만들지 말고, 마이그레이션 멱등성 테스트·CRUD 테스트·KST 날짜 경계 테스트를 추가해줘.
```

### Phase 2. Claude Code 경로 탐색

### 목표

사용자 설정 없이 Claude Code 로컬 데이터 위치를 찾는다.

### 작업

- [ ] Windows 사용자 홈 경로 확인.
- [ ] 기본 `.claude` 경로 탐색.
- [ ] `projects` 하위 디렉터리 탐색.
- [ ] 접근 권한 오류 처리.
- [ ] 사용자 지정 경로 설정 추가.
- [ ] 감지 상태 모델 정의.
- [ ] 감지 결과 UI 표시.

### 상태

```text
Searching       -- 탐색 중
Found           -- 발견됨
NotFound        -- 경로 없음
PermissionDenied -- 접근 불가
InvalidPath     -- 경로 형식 오류
```

### 완료 기준

- 경로가 있을 때 발견 결과를 반환한다.
- 경로가 없을 때 앱이 종료되지 않는다.
- 한글과 공백이 있는 경로를 처리한다.
- 잘못된 경로를 설정해도 오류 안내를 표시한다.
- `InvalidPath` 상태일 때 오류 원인과 수정 방법이 UI에 표시된다.
- `PermissionDenied` 상태일 때 권한 문제임을 알리고 대응 방법(경로 직접 지정 등)을 안내한다.
- 경로 문자열은 정규화하여 저장한다 (대소문자·후행 구분자 차이로 중복 인식되지 않도록).

### 바이브 코딩 프롬프트

```text
Windows 사용자 홈 디렉터리에서 Claude Code의 `.claude/projects` 경로를 자동 탐색하는 Rust 모듈을 만들어줘.
감지 상태는 Searching / Found / NotFound / PermissionDenied / InvalidPath 5가지로 정의한다.
한글과 공백이 포함된 경로를 처리하고, 사용자 지정 경로 설정도 지원해야 한다.
각 상태에 대한 단위 테스트를 최소 5개 작성해줘.
```

### Phase 3. JSONL 파서

### 목표

Claude Code 로컬 세션 파일을 내부 `UsageEvent`로 변환한다.

### 작업

- [ ] JSONL 한 줄 단위 파서 작성.
- [ ] 세션 ID(`external_session_id`) 추출 및 `session_key` 생성.
- [ ] 프로젝트 정보 추출.
- [ ] 모델 정보 추출.
- [ ] 토큰 필드 추출 (입력·출력·캐시 읽기·캐시 쓰기).
- [ ] `costUSD` 제공값 추출 (nullable).
- [ ] **컨텍스트 사용률 계산** (로그에 없는 값이므로 파서가 직접 계산, 아래 참조).
- [ ] `event_hash` 생성 (내용 기반, 파일 경로·오프셋 미포함).
- [ ] 알 수 없는 필드 무시.
- [ ] 깨진 행 오류 처리 (`parse_errors`에 기록, 원문 미포함).
- [ ] 샘플 fixture 추가.
- [ ] 파서 회귀 테스트 작성.

### 파서 원칙

```text
알려진 필드 → 변환해서 저장
알 수 없는 필드 → 무시
누락된 선택 필드 → null
필수 식별자 누락 → 해당 이벤트만 건너뜀
깨진 JSON → 오류 기록 후 다음 행 처리
원문 페이로드 → 출력 타입에도 오류 기록에도 포함하지 않음
```

### 컨텍스트 사용률 계산

Claude Code JSONL은 컨텍스트 사용률을 직접 제공하지 않는다. 파서가 계산해야 한다.

```text
컨텍스트 사용 토큰 = input_tokens + cache_read_tokens + cache_write_tokens
컨텍스트 사용률(%) = 컨텍스트 사용 토큰 / 모델 컨텍스트 윈도우 × 100
```

- 모델별 `context_window` 값은 Phase 5의 가격표(`claude-pricing.json`)에서 가져온다.
  따라서 파서는 가격표 조회 인터페이스에만 의존하고, 가격 계산 로직에는 의존하지 않는다.
- 모델을 찾을 수 없으면 `context_used_percentage = null` (UI에서 `계산 불가`).
- 계산으로 얻은 값은 `is_estimated = true`로 기록한다.

### `event_hash` 생성 규칙

PRD §7의 정의를 그대로 구현한다. 파일 경로와 오프셋은 **해시 입력에서 제외**한다.

```text
sha256(source | session 식별자 | event_time | model |
       input | output | cache_read | cache_write)
```

### 완료 기준

- 기본 세션 fixture가 파싱된다.
- 다중 모델 fixture가 파싱된다 (한 세션 안에서 이벤트별 모델이 다르게 기록된다).
- 한글 프로젝트명이 보존된다.
- 깨진 행이 있어도 나머지 이벤트가 파싱된다.
- 같은 이벤트를 두 번 파싱하면 **동일한 `event_hash`**가 생성된다.
- 같은 이벤트를 다른 파일 경로/오프셋에서 읽어도 `event_hash`가 동일하다.
- `costUSD`가 있는 행은 제공값으로, 없는 행은 추정 대상으로 분류된다.
- 파서 출력 타입과 `parse_errors` 기록 어디에도 프롬프트·응답 원문이 포함되지 않는다.

### 샘플 fixture JSONL 구조 힌트

Claude Code는 `~/.claude/projects/<프로젝트해시>/` 디렉터리에 `.jsonl` 파일을 생성한다.
각 줄은 JSON 객체이며, 사용량 정보를 포함하는 행은 다음과 같은 필드를 사용한다.

```json
{
  "type": "assistant",
  "message": {
    "usage": {
      "input_tokens": 1234,
      "output_tokens": 567,
      "cache_read_input_tokens": 89,
      "cache_creation_input_tokens": 0
    },
    "model": "claude-opus-4-5"
  },
  "sessionId": "abc-123",
  "timestamp": "2026-08-13T02:30:00.000Z",
  "costUSD": 0.0234
}
```

> 주의: 실제 필드명은 Claude Code 버전에 따라 다를 수 있다.
> 위 예시에 **컨텍스트 사용률 필드는 없다.** 사용률은 위의 계산 규칙으로 직접 구한다.
> `apps/desktop/src-tauri/fixtures/claude-code/basic-session.jsonl`에 실제 로그 샘플을 채워 넣어 파서를 검증한다.
> 실제 로그 샘플 확보는 이 Phase의 **착수 전제조건**이다.

### 바이브 코딩 프롬프트

```text
src-tauri/fixtures/claude-code의 샘플 JSONL을 기준으로 Claude Code 사용량 파서를 만들어줘.
파서의 출력은 UsageEvent 도메인 타입이어야 한다.
event_hash는 PRD §7 정의대로 내용 기반으로 생성하고, 파일 경로와 오프셋은 해시 입력에서 제외한다.
컨텍스트 사용률은 로그에 없으므로 (input + cache_read + cache_write) / 모델 컨텍스트 윈도우로 계산하고,
모델을 모르면 null로 둔다. 계산값에는 is_estimated = true를 붙인다.
costUSD가 있으면 제공값으로 따로 보관한다.
프롬프트와 응답 원문은 출력 타입에도 오류 기록에도 포함하지 마.
깨진 JSON 한 줄은 전체 파싱을 중단하지 않아야 하며, 최소 7개의 단위 테스트를 추가해줘.
(기본/다중모델/한글경로/깨진행/동일이벤트 해시일치/다른경로 해시일치/costUSD 유무)
```

### Phase 4. 파일 감시와 증분 수집

### 목표

새로운 사용량 이벤트를 실시간으로 수집한다.

### 작업

- [ ] 디렉터리 watcher 구현.
- [ ] 신규 파일 감지.
- [ ] 기존 파일 변경 감지.
- [ ] `file_key` 산출 (Windows volume serial + file index, 실패 시 정규화 경로).
- [ ] 파일별 마지막 offset 저장 (항상 개행 경계까지만 전진).
- [ ] 파일 잠금 재시도 및 부분 행(개행 미완료) 보류 처리.
- [ ] 이벤트 중복 방지 (`INSERT ... ON CONFLICT(event_hash) DO NOTHING`).
- [ ] 파일 이름 변경 처리 (`file_key` 동일 → 오프셋 유지, `file_path`만 갱신).
- [ ] 파일 삭제 처리 (5초 유예 후에도 복구되지 않으면 offset 레코드 정리).
- [ ] 파일 잘림 처리 (`file_size < last_offset` → 오프셋 0으로 리셋 후 전체 재스캔).
- [ ] 세션 upsert 및 `ended_at` 판정 스케줄러 (마지막 이벤트 후 30분, 5분 주기 점검).
- [ ] 수집 일시정지/재개 제어 (`collector_paused_at` 반영).
- [ ] 앱 시작 시 기존 파일 초기 스캔.
- [ ] 수집 상태 이벤트 발행.

### 완료 기준

- 새 파일이 생성되면 자동 처리된다.
- 기존 파일에 행이 추가되면 추가된 행만 처리한다.
- 앱을 재시작해도 중복 집계되지 않는다.
- **파일 이름을 변경해도 재수집이 일어나지 않는다** (오프셋 유지).
- **오프셋을 강제로 0으로 되돌려 전체 재스캔해도 `usage_events` 행 수가 늘지 않는다** (`event_hash` 중복 제거 검증).
- 파일이 일시적으로 잠겨도 watcher가 계속 동작한다.
- 개행으로 끝나지 않는 부분 행은 다음 감지 때 온전히 처리된다.
- 새 이벤트가 10초 이내 DB에 반영된다.
- 감시 중인 파일이 삭제되어도 watcher가 오류 없이 계속 동작한다.
- 마지막 이벤트로부터 30분이 지난 세션의 `ended_at`이 채워진다.
- 일시정지 상태에서는 새 이벤트가 저장되지 않고, 재개하면 그동안 추가된 행이 처리된다.

### 바이브 코딩 프롬프트

```text
Rust `notify` 크레이트를 사용하여 Claude Code `.claude/projects` 디렉터리를 감시하는 모듈을 만들어줘.
신규 파일과 기존 파일 변경(append)을 감지하고, Phase 1의 file_offsets 테이블로 마지막 처리 위치를 관리한다.
오프셋 키는 파일 경로가 아니라 file_key(Windows volume serial + file index)로 한다.
파일 이름이 바뀌면 오프셋을 유지하고 file_path만 갱신하며,
파일이 잘리면(file_size < last_offset) 오프셋을 0으로 리셋해 전체 재스캔한다.
중복 저장은 INSERT ... ON CONFLICT(event_hash) DO NOTHING으로 막는다.
파일 삭제 시 5초 유예를 두고, 그 후에도 복구되지 않으면 offset 레코드를 정리한다.
세션 upsert는 (source, session_key) 기준으로 하고,
마지막 이벤트 후 30분이 지나면 ended_at을 채우는 스케줄러를 5분 주기로 돌려줘.
수집 일시정지/재개도 지원하고, 수집 상태 이벤트를 프론트엔드에 발행해줘.
테스트: append 증분 처리, 파일명 변경 후 재수집 없음, 전체 재스캔 후 행 수 불변, 부분 행 보류, 파일 삭제 내성.
```

### Phase 5. 비용·환율 엔진

### 목표

토큰 사용량을 USD와 KRW 비용으로 변환한다.

### 작업

- [ ] 모델 가격표 구조 정의 (`claude-pricing.json` 번들, `pricing_version` 포함).
      필드: `model`, `input_per_mtok`, `output_per_mtok`, `cache_write_per_mtok`, `cache_read_per_mtok`, `context_window`.
- [ ] 입력 가격 계산.
- [ ] 출력 가격 계산.
- [ ] **캐시 쓰기 가격 계산** (입력 단가보다 비쌈).
- [ ] **캐시 읽기 가격 계산** (입력 단가보다 훨씬 쌈).
- [ ] 제공값(`costUSD`) 우선 사용 로직 (`cost_source` 결정).
- [ ] 가격 버전 저장.
- [ ] Phase 3 파서가 쓸 `context_window` 조회 함수 노출.
- [ ] 환율 인터페이스 정의 (`ExchangeRateProvider` 트레이트).
- [ ] 수동 환율 모드 구현 (앱 설정에서 입력). **기본 모드.**
- [ ] 온라인 환율 조회 구현 (ExchangeRate-API). 사용자가 명시적으로 켤 때만 동작.
- [ ] 환율 실패 시 `app_settings`의 마지막 정상값(`last_known_exchange_rate` 등) 사용.
- [ ] 마지막 정상값도 없으면 KRW를 `계산 불가`로 두고 USD만 저장·표시.
- [ ] 환율 출처(`rate_source`)와 기준 시각(`rate_fetched_at`) 저장.
- [ ] 비용을 `usage_events` 삽입과 **같은 트랜잭션**에서 계산·저장.
- [ ] `is_estimable = false`로 남은 행의 백필 함수 (가격표·환율 확보 시 해당 행만 재계산).

### 비용 계산 규칙

캐시 쓰기와 캐시 읽기는 단가가 다르므로 반드시 분리한다.

```text
입력 비용      = input_tokens       / 1,000,000 × input_per_mtok
출력 비용      = output_tokens      / 1,000,000 × output_per_mtok
캐시 쓰기 비용 = cache_write_tokens / 1,000,000 × cache_write_per_mtok
캐시 읽기 비용 = cache_read_tokens  / 1,000,000 × cache_read_per_mtok

추정 USD = 입력 비용 + 출력 비용 + 캐시 쓰기 비용 + 캐시 읽기 비용

표시 USD = costUSD 가 있으면 costUSD (cost_source = provided)
           없으면 추정 USD           (cost_source = estimated)

KRW = 표시 USD × 적용 환율   (환율이 없으면 null → '계산 불가')
```

### 완료 기준

- 고정된 샘플 가격으로 예상값과 일치한다.
- **캐시 읽기와 캐시 쓰기에 서로 다른 단가가 적용된다** (두 단가를 바꿔 넣으면 테스트가 실패한다).
- `costUSD`가 있는 이벤트는 `cost_source = provided`로 저장되고 추정값이 표시에 쓰이지 않는다.
- 가격 정보가 없는 모델은 `is_estimable: false`가 되어 UI에 `계산 불가`로 표시된다.
- 과거 비용은 가격 변경 후에도 변하지 않는다.
- 환율과 환율 시각이 비용 레코드에 저장된다.
- 환율 조회에 실패하면 마지막 정상 환율이 사용되고 그 기준 시각이 저장된다.
- 환율이 전혀 없어도 USD 비용 저장과 집계는 정상 동작한다.
- 환율 자동 조회를 끈 상태에서는 네트워크 요청이 발생하지 않는다.

### 바이브 코딩 프롬프트

```text
Phase 1~4의 도메인 타입을 기반으로 비용·환율 엔진을 구현해줘.
모델 가격표는 pricing/claude-pricing.json에 번들하고 pricing/claude.rs에서 읽는다.
가격표에는 input/output/cache_write/cache_read 단가와 context_window를 각각 둔다.
캐시 쓰기와 캐시 읽기는 단가가 다르므로 절대 합산해서 계산하지 마.
로그의 costUSD가 있으면 cost_source=provided로 그 값을 쓰고, 없으면 추정값을 쓴다.
ExchangeRateProvider 트레이트로 수동 모드(기본)와 온라인 조회(ExchangeRate-API)를 구현하고,
조회 실패 시 app_settings의 last_known_exchange_rate를 사용한다.
환율이 아예 없으면 KRW는 null로 두고 USD만 저장한다.
비용은 usage_events 삽입과 같은 트랜잭션에서 계산해 costs에 기록한다.
각 계산 함수에 단위 테스트를 추가하고, 캐시 읽기/쓰기 단가를 뒤바꾸면 실패하는 테스트를 포함해줘.
```

### Phase 6. 집계 API

### 목표

프론트엔드가 사용할 로컬 조회 API를 제공한다.

### 필요한 명령 또는 API

조회 API:

```text
get_app_status()        -- 수집 상태, 마지막 갱신 시각, Claude 경로 감지 결과
get_current_session()   -- 현재 진행 중 세션 정보
get_summary(range)      -- 기간별 토큰·비용 요약
get_daily_usage(range)  -- 일별 사용량
get_usage_by_model(range)
get_usage_by_project(range)
get_sessions(filter)    -- 세션 목록 (페이지네이션 지원)
get_session_detail(session_id)
get_settings()
get_parse_errors(limit) -- 파싱 오류 목록 (원문 미포함)
```

제어 API (FR-007 트레이 메뉴·FR-010 설정과 연결):

```text
update_settings(input)
pause_collection()      -- collector_paused_at 기록
resume_collection()
refresh_exchange_rate() -- 수동 환율 갱신 트리거
open_data_folder()      -- OS 파일 탐색기로 데이터 폴더 열기
rescan_all()            -- file_offsets 초기화 후 전체 재스캔
delete_all_data()       -- file_offsets는 유지 (PRD §7 삭제 정책)
```

`get_app_status()` 반환 필드:

```json
{
  "collector_state": "Running | Paused | NoData | Error",
  "warnings": ["StaleUpdate", "ParseErrors", "NoPricing", "NoExchangeRate", "PermissionDenied"],
  "last_updated_at": "2026-08-13T03:00:00Z",
  "paused_at": null,
  "claude_path_status": "Found | NotFound | PermissionDenied | InvalidPath",
  "parse_error_count": 0,
  "watched_file_count": 3,
  "exchange_rate": 1385.5,
  "rate_source": "manual",
  "rate_fetched_at": "2026-08-13T00:00:00Z"
}
```

> 상태 모델은 PRD FR-011의 주 상태 + 경고 플래그 구조를 그대로 따른다.
> `Paused`일 때 `paused_at`이 반드시 채워져야 한다 (PRD 수용 기준: 일시정지 시각 표시).

### 날짜 범위 처리

- 범위는 KST 기준으로 해석하고, 쿼리는 `local_date` 컬럼으로 필터링한다.
- `오늘` = KST 오늘 00:00~24:00, `7일` / `30일` = 오늘 포함 직전 N일.
- 보관 기간이 지난 구간은 `daily_rollups`에서 조회하고, 최근 구간은 `usage_events`에서 조회한다. 두 소스를 합칠 때 중복이 없어야 한다.

### 완료 기준

- 모든 조회 API에 빈 데이터 응답이 정의되어 있다.
- 날짜 범위가 명확하게 동작한다 (KST 기준 오늘·7일·30일·사용자 지정).
- KST 날짜 경계 테스트가 있다 (UTC 15:00 = KST 익일 00:00).
- 집계 결과가 SQL 원본 데이터와 일치한다.
- `pause_collection` 후 `get_app_status()`가 `Paused`와 `paused_at`을 반환한다.
- `delete_all_data` 직후 재개해도 기존 로그가 다시 수집되지 않는다.
- `rescan_all` 실행 후 이벤트 수가 중복 없이 복구된다.
- 프론트엔드가 Rust 내부 구조체를 직접 알 필요가 없다.
- 모든 Tauri command에 단위 테스트 또는 통합 테스트가 있다.

### 바이브 코딩 프롬프트

```text
Phase 1~5에서 만든 DB·집계·비용 모듈을 기반으로 Tauri command API를 구현해줘.
조회: get_app_status, get_current_session, get_summary, get_daily_usage,
get_usage_by_model, get_usage_by_project, get_sessions, get_session_detail,
get_settings, get_parse_errors.
제어: update_settings, pause_collection, resume_collection, refresh_exchange_rate,
open_data_folder, rescan_all, delete_all_data.
get_app_status는 PRD FR-011대로 주 상태(collector_state)와 경고 배열(warnings)을 분리해 반환한다.
날짜 범위는 KST 기준으로 해석하고 local_date 컬럼으로 필터링해줘.
delete_all_data는 file_offsets를 지우지 않는다 (삭제 후 재수집 방지).
각 command의 응답 타입은 TypeScript에서도 쓸 수 있도록 직렬화 가능한 구조체로 정의하고,
빈 데이터 케이스와 KST 날짜 경계 테스트를 추가해줘.
```

### Phase 7. 한국어 대시보드

### 목표

첫 화면에서 사용자가 현재 사용량과 비용을 바로 이해하게 한다.

### 화면

#### 홈

- 오늘 비용.
- 이번 달 비용.
- 오늘 토큰.
- 오늘 세션 수.
- 현재 모델.
- 컨텍스트 사용률 (추정값이면 `(추정)` 라벨).
- 마지막 갱신 시각.
- **수집 상태 표시** (FR-011의 주 상태 + 경고 배지, 오류 시 해결 방법 링크).
- 기간 필터 (오늘 / 7일 / 30일 / 사용자 지정) — 아래 차트 전체에 적용.

#### 차트 (FR-008, ECharts)

PRD FR-008이 요구하는 4종을 모두 구현한다. Phase 6의 `get_daily_usage`, `get_usage_by_model`, `get_usage_by_project`를 소비한다.

| 차트 | 데이터 소스 | 형태 |
|---|---|---|
| 날짜별 비용 | `get_daily_usage(range)` | 막대 또는 라인 |
| 날짜별 토큰 | `get_daily_usage(range)` | 스택 막대 (입력/출력/캐시 읽기/캐시 쓰기) |
| 모델별 비용·토큰 비율 | `get_usage_by_model(range)` | 도넛 + 표 |
| 프로젝트별 비용 | `get_usage_by_project(range)` | 가로 막대 |

- ECharts는 필요한 차트·컴포넌트 모듈만 개별 임포트한다 (메모리 목표 대응).
- 차트에도 빈 상태와 `계산 불가` 처리를 적용한다.
- 라이트/다크 테마에 맞는 색상 팔레트를 각각 정의한다.

#### 세션 목록

- 시작 시각 (KST).
- 프로젝트.
- 대표 모델 (`last_model`).
- 총 토큰.
- 비용.
- 상태 (`진행 중` / 종료).
- 페이지네이션.

#### 세션 상세

- 세션 시작·종료 시각 (진행 중이면 `진행 중`).
- 입력·출력·캐시 읽기·캐시 쓰기·총 토큰.
- 세션 내 모델별 내역 (여러 모델을 사용한 경우).
- USD·KRW 비용.
- 비용 출처 (`제공값` / `추정값`), 두 값이 모두 있으면 비교 표시.
- 데이터 출처 (로컬 로그 파서).
- 비용 계산 기준 (가격 버전, 환율, 환율 기준 시각).

#### 설정

PRD FR-010과 항목이 1:1로 일치해야 한다.

- 표시 통화 (KRW / USD).
- 환율 모드 (수동 / 자동) 및 환율 값.
- 트레이 툴팁 표시 항목.
- 시스템 시작 시 자동 실행.
- 테마 (시스템 / 라이트 / 다크).
- 데이터 경로.
- 보관 기간 (30일 / 90일 / 무제한).
- 데이터 폴더 열기.
- 전체 다시 스캔.
- 데이터 전체 삭제.

> 원문 저장 여부와 비용 알림 임계값은 MVP에서 노출하지 않는다 (PRD FR-010).
> 환율을 변경해도 과거 비용은 재계산되지 않는다는 안내 문구를 환율 설정 옆에 표시한다.

### 완료 기준

- [ ] 모든 핵심 화면이 한국어다.
- [ ] FR-008의 차트 4종이 모두 구현되어 있다.
- [ ] 데이터가 없을 때 빈 상태 화면이 있다 (차트 포함).
- [ ] 수집 오류가 있을 때 오류 원인과 해결 방법이 보인다.
- [ ] 파싱 오류 행 수와 대응 방법이 표시된다.
- [ ] 숫자와 날짜가 한국 형식으로 표시된다 (`₩`, `만`, KST 기준 날짜).
- [ ] 테마 설정(시스템/라이트/다크)이 동작하고 두 모드 모두 읽기 쉽다.
- [ ] 추정값에 `(추정)` 라벨이 명확히 표시된다.
- [ ] 비용 계산 불가 모델은 `계산 불가`로 표시된다 (0원으로 표시하지 않는다).
- [ ] 날짜 범위 필터 (오늘 / 7일 / 30일 / 사용자 지정)이 모든 차트와 목록에 적용된다.
- [ ] 설정 화면 항목이 PRD FR-010과 정확히 일치한다.

### 바이브 코딩 프롬프트

```text
Phase 6의 Tauri command API를 사용하여 한국어 대시보드를 구현해줘.
화면: 홈(오늘 비용·월 비용·토큰·세션수·컨텍스트·수집 상태), 차트 4종, 세션 목록, 세션 상세, 설정.
차트는 ECharts로 구현한다: 날짜별 비용, 날짜별 토큰(입력/출력/캐시읽기/캐시쓰기 스택),
모델별 비용·토큰 비율(도넛), 프로젝트별 비용(가로 막대).
ECharts는 필요한 모듈만 개별 임포트해줘.
기간 필터(오늘/7일/30일/사용자 지정)는 상단에 한 번만 두고 모든 차트와 목록에 함께 적용한다.
숫자는 한국 형식(₩, 만 단위)으로, 날짜는 KST 기준으로 표시한다.
추정값에는 (추정) 라벨을, 계산 불가 항목에는 '계산 불가'를 표시한다 (0원 표시 금지).
설정 화면 항목은 PRD FR-010과 1:1로 맞춘다 (원문 저장 여부와 알림 임계값은 넣지 마).
데이터가 없을 때의 빈 상태 화면과 오류 상태 화면도 구현해줘.
Tailwind CSS를 사용하고 시스템/라이트/다크 테마를 지원한다.
```

### Phase 8. Windows 트레이

### 목표

대시보드를 열지 않아도 핵심 수치를 확인하게 한다.

### 작업

- [ ] 트레이 아이콘 등록 (상태별 색상 아이콘 3종: 정상 / 일시정지 / 오류).
- [ ] 트레이 툴팁 갱신 (요약 수치 문자열).
- [ ] 트레이 메뉴 구현.
- [ ] 요약 팝업 구현.
- [ ] 툴팁 표시 항목 설정 연동.
- [ ] 대시보드 열기.
- [ ] 자동 실행 설정 (`HKCU\...\Run`).
- [ ] 종료와 백그라운드 동작 분리 (창 닫을 때 WebView2 리소스 해제).

### 표시 채널

Windows 트레이 아이콘은 16×16 이미지이며 **아이콘 옆에 텍스트를 표시할 수 없다.** macOS 메뉴바와 다르다.

| 채널 | 내용 |
|---|---|
| 아이콘 | 수집 상태 색상만 (정상 / 일시정지 회색 / 오류 빨강) |
| 툴팁 | `42% \| ₩4.8K` (기본값: 토큰과 비용 조합, 127자 제한 내) |
| 팝업 | 모델·컨텍스트·오늘 토큰과 비용·이번 달 비용·마지막 갱신 시각·수집 상태 |

> 아이콘 이미지에 수치를 그려 넣는 동적 렌더링은 16×16에서 판독이 불가능하므로 MVP 범위 밖이다 (PRD §12).

### 트레이 메뉴 (PRD FR-007과 일치)

```text
대시보드 열기
수집 일시정지 / 재개
설정
데이터 폴더 열기
데이터 삭제
앱 종료
```

### 완료 기준

- [ ] 앱이 트레이에 표시된다.
- [ ] 툴팁에 설정한 요약 수치가 표시된다.
- [ ] 수집 상태에 따라 아이콘 색상이 바뀐다.
- [ ] 트레이 아이콘 클릭으로 팝업이 열린다.
- [ ] 팝업에서 현재 세션과 오늘 비용을 확인할 수 있다.
- [ ] 트레이에서 대시보드를 열 수 있다.
- [ ] 트레이 메뉴 6개 항목이 PRD FR-007과 일치한다 (`데이터 삭제` 포함).
- [ ] `데이터 삭제`는 확인 다이얼로그를 거친다.
- [ ] 앱 창을 닫아도 트레이 수집은 계속된다.
- [ ] 앱 창을 닫으면 유휴 메모리가 150MB 이하로 내려간다.
- [ ] 수집 일시정지/재개가 트레이 메뉴에서 동작하고 FR-011 상태와 연동된다 (`paused_at` 포함).

### 바이브 코딩 프롬프트

```text
Tauri 2의 트레이 API를 사용하여 Windows 시스템 트레이를 구현해줘.
Windows 트레이는 아이콘 옆에 텍스트를 표시할 수 없으므로,
수치는 툴팁(set_tooltip)에 '42% | ₩4.8K' 형태로 넣고 아이콘은 상태 색상만 나타낸다.
아이콘은 정상/일시정지/오류 3종을 준비한다.
트레이 클릭시 팝업 창을 열고, 모델·컨텍스트·오늘 비용·이번 달 비용·마지막 갱신 시각·수집 상태를 표시한다.
트레이 메뉴: '대시보드 열기', '수집 일시정지/재개', '설정', '데이터 폴더 열기', '데이터 삭제', '앱 종료'.
일시정지/재개는 Phase 6의 pause_collection/resume_collection command와 연결한다.
앱 창 닫기와 트레이 종료를 분리한다 (앱 창 닫기 = 백그라운드 동작 유지 + WebView2 리소스 해제,
트레이 메뉴 '앱 종료' = 완전 종료).
```

### Phase 9. 개인정보와 데이터 관리

### 작업

- [ ] 원문 저장 코드 경로가 아예 없는지 확인 (MVP에서 기능 자체를 제외).
- [ ] `parse_errors`에 원문 페이로드가 들어가지 않는지 확인.
- [ ] 저장 데이터 안내 화면 작성.
- [ ] 전체 데이터 삭제 구현 (PRD §7 삭제 정책 — `file_offsets` 유지).
- [ ] 전체 다시 스캔 구현 (`file_offsets` 초기화).
- [ ] 데이터 폴더 열기 구현.
- [ ] 보관 기간 설정 구현 (30일/90일/무제한, **기본 무제한**).
- [ ] 보관 기간 만료 데이터 자동 정리 스케줄러 구현 (앱 시작 시 + 일일 1회).
- [ ] **정리 직전 `daily_rollups` 갱신** 후 원본 이벤트 삭제 (장기 추이 보존).
- [ ] 외부 네트워크 요청 목록 점검 (환율 조회 1건뿐인지 확인).
- [ ] 개인정보 테스트 fixture 추가.

### 완료 기준

- [ ] 프롬프트와 응답 원문이 DB에 저장되지 않는다 (저장 코드 경로 자체가 없다).
- [ ] 파싱 오류 기록에도 원문이 남지 않는다.
- [ ] 데이터 삭제 후 대시보드가 빈 상태가 되고, **수집 재개 후에도 빈 상태를 유지한다**.
- [ ] 전체 다시 스캔을 실행하면 과거 데이터가 중복 없이 복구된다.
- [ ] 보관 기간 만료로 이벤트를 삭제해도 `daily_rollups`의 과거 월별 합계가 유지된다.
- [ ] 앱의 외부 통신 여부를 `docs/privacy.md`에 문서화한다 (PRD §8의 외부 통신 표와 동일한 내용).
- [ ] 환율 자동 조회를 끈 상태에서 외부 요청이 0건임을 테스트로 확인한다.
- [ ] 로그에도 원문이 남지 않는다.
- [ ] `docs/privacy.md`에 수집 데이터 전체 목록이 명시된다.

### 바이브 코딩 프롬프트

```text
개인정보 및 데이터 관리 기능을 구현해줘.
1. 원문 저장 코드 경로가 존재하지 않는지 확인한다 (MVP에서 기능 자체를 제외).
   parse_errors에도 실패한 행의 내용을 저장하지 않는다.
2. 전체 데이터 삭제(delete_all_data): 확인 다이얼로그 → 수집 일시정지 → 이벤트/비용/세션/오류/롤업 삭제
   → file_offsets는 유지 → 수집 재개. 삭제 후 기존 로그가 다시 수집되면 안 된다.
3. 전체 다시 스캔(rescan_all): file_offsets를 초기화해 처음부터 재수집한다.
4. 데이터 폴더 열기(OS 파일 탐색기).
5. 보관 기간 설정(30일/90일/무제한, 기본 무제한) — 만료 데이터 자동 정리.
   삭제하기 직전에 daily_rollups를 갱신해 장기 추이를 남긴다.
6. docs/privacy.md에 수집 데이터 목록, 외부 통신 표(환율 조회 1건), 삭제 방법을 문서화한다.
각 기능에 대한 테스트를 추가하고, 환율 자동 조회 OFF일 때 네트워크 요청이 0건인 테스트를 포함해줘.
```

### Phase 10. 테스트와 배포

### 테스트

- [ ] Rust 단위 테스트.
- [ ] JSONL 파서 회귀 테스트.
- [ ] `event_hash` 중복 제거 테스트 (재스캔·파일명 변경·앱 재시작).
- [ ] 비용 계산 테스트 (캐시 읽기/쓰기 단가 분리 검증 포함).
- [ ] 제공값(`costUSD`) vs 추정값 우선순위 테스트.
- [ ] 환율 계산 테스트 (실패 시 마지막 정상값 사용, 환율 없음 케이스).
- [ ] 집계 SQL 테스트 (KST 날짜 경계 포함).
- [ ] 파일 watcher 테스트 (append / 이름 변경 / 삭제 / 잘림 / 부분 행).
- [ ] Tauri command 테스트 (빈 데이터 케이스 포함).
- [ ] 프론트엔드 컴포넌트 테스트 (Vitest).
- [ ] E2E 테스트 (`tauri-driver` + WebdriverIO, Windows 러너).
- [ ] Windows 설치 테스트 (NSIS per-user, UAC 미발생 확인).
- [ ] 앱 재시작 및 데이터 유지 테스트.
- [ ] 대용량 로그 테스트 — PRD 성능 기준 검증: 세션 1,000개 초기 스캔 30초 이내.
- [ ] 메모리 테스트 — 창 닫힘(유휴) 150MB 이하, 창 열림 400MB 이하.
- [ ] 외부 통신 테스트 — 환율 자동 조회 OFF 시 요청 0건.

### 배포 전 체크리스트

- [ ] Windows 10 (빌드 19041 이상) / 11에서 설치된다.
- [ ] NSIS per-user 설치가 UAC 프롬프트 없이 완료된다.
- [ ] Claude Code 미설치 환경에서 정상 안내한다.
- [ ] 한글·공백 포함 경로가 동작한다.
- [ ] 언인스톨 시 DB 기본 보존 + "함께 삭제" 옵션이 동작한다.
- [ ] README에 설치·개인정보·문제 해결 방법이 있다.
- [ ] `docs/privacy.md`가 PRD §8의 외부 통신 표와 일치한다.
- [ ] `docs/troubleshooting.md`에 최소 3가지 케이스가 있다 (Claude Code 미설치, 경로 입력 오류, DB 손상).
- [ ] 샘플 화면과 알려진 제한을 문서화한다 (컨텍스트 사용률은 추정값, 비용은 실제 청구액과 다를 수 있음).
- [ ] GitHub Actions CI가 Windows 빌드를 자동으로 통과시킨다.
- [ ] 코드 서명(code signing) 적용 여부를 결정하고 문서화한다 (미적용 시 SmartScreen 경고 발생 가능성 명시).
- [ ] LICENSE(MIT)가 포함되어 있다.
- [ ] 릴리스 노트에 DB 스키마 버전 내역 포함.

## 7. AI 코딩 에이전트 작업 템플릿

다음 템플릿을 작업마다 사용한다.

```markdown
## 작업 요청

### 목표
[이번 작업에서 달성할 한 가지 목표]

### 참고 문서
- PRD.md의 [관련 섹션]
- PLAN.md의 [현재 Phase]

### 현재 상태
[이미 구현된 내용]

### 변경 범위
- [변경할 파일 또는 모듈]

### 변경하지 않을 것
- [이번 작업에서 건드리지 않을 기능]

### 수용 기준
- [ ] ...
- [ ] ...

### 테스트
- [ ] 단위 테스트
- [ ] 수동 실행 테스트

### 완료 후 보고
- 변경 파일 목록
- 실행한 명령
- 테스트 결과
- 남은 문제
- 다음 작업 제안
```

## 8. 추천 작업 순서

바이브 코딩을 시작할 때는 아래 순서를 그대로 따른다.

1. Phase 0 프로젝트 부트스트랩.
2. Phase 1 SQLite와 타입.
3. Phase 2 경로 탐색.
4. Phase 3 샘플 파서.
5. Phase 4 실제 파일 감시.
6. Phase 5 비용 계산.
7. Phase 6 집계 API.
8. Phase 7 대시보드.
9. Phase 8 트레이.
10. Phase 9 개인정보·데이터 관리.
11. Phase 10 테스트·배포.

수집기와 파서가 안정화되기 전에 예쁜 대시보드를 먼저 만들지 않는다. 데이터 정확성이 이 제품의 핵심 가치이기 때문이다.

## 9. 후속 버전 계획

아래 v0.2 / v0.3 / v0.4 / v1.0은 **제품 버전**이며, 이 문서의 개정 번호(rev.N)와는 무관하다. MVP는 v0.1.0이다.

### v0.2 — 사용성 개선

- Claude Code 상태 표시줄 자동 연동 (공식 Claude Code API 출시 시 대응).
- 컨텍스트 사용률 표시 정확도 개선.
- 트레이 동적 아이콘 렌더링 재검토 (MVP에서는 툴팁만 사용).
- 비용 알림 임계값 설정 (FR-010에서 MVP 제외로 미룬 항목).
- 예산 설정 및 비용 급증 알림.
- CSV / JSON 내보내기.
- 자동 업데이트 (Tauri updater).
- 다국어 지원 기반 구축 (i18n).

### v0.3 — 멀티 수집기

- OpenAI API 수집기 (API 키 기반).
- Anthropic API 수집기 (공식 API 사용량 엔드포인트).
- Gemini API 수집기.
- Ollama 수집기 (로컬 모델).
- 통합 모델 비교 대시보드.
- Cursor / Windsurf 로컬 로그 수집기 검토.

### v0.4 — 팀 및 엔터프라이즈

- 국내 AI 모델 어댑터 (CLOVA, HyperCLOVA X 등).
- 프록시 게이트웨이 모드.
- 프로젝트별 API 키 관리.
- 팀별 사용량 집계.
- 중앙 서버 선택 모드 (로컬 ↔ 팀 서버).

### v1.0 — 플랫폼 확장

- macOS 지원.
- Linux 지원 (Tauri 기반).
- 기업용 폐쇄망 배포 패키지.
- OpenTelemetry 연동.
- 개인정보 마스킹 (로그 내 민감 정보 자동 제거).
- AI 기반 비용 예측 및 절감 모델 추천.
- 팀 권한 관리 및 감사 로그.

## 10. 첫 번째 실제 작업

다음 작업부터 시작한다.

```markdown
목표: 실행 가능한 Tauri + React 프로젝트를 생성한다.

변경 범위:
- Tauri 2
- React
- TypeScript
- Tailwind CSS
- pnpm 워크스페이스 구성 (apps/desktop)
- NSIS per-user 번들 설정

변경하지 않을 것:
- Claude Code 파서
- SQLite
- 비용 계산
- 트레이 (Phase 8에서 구현)
- 외부 네트워크

수용 기준:
- [ ] Windows에서 개발 서버가 실행된다.
- [ ] Tauri 앱 창이 열린다.
- [ ] `pnpm tauri build`가 NSIS per-user 설치 파일을 생성한다.
- [ ] 설치 시 UAC 프롬프트가 뜨지 않는다.
- [ ] 앱 종료가 정상 동작한다.
- [ ] README에 실행 명령이 기록된다.
```
