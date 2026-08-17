# AGENTS.md

이 저장소에서 작업하는 모든 AI 코딩 에이전트를 위한 지침이다. **이 파일이 정본이며**, `CLAUDE.md` / `GEMINI.md` / `.github/copilot-instructions.md`는 이 파일을 가리킨다. 지침을 고칠 때는 이 파일만 고친다.

## 저장소 현황

**코드가 아직 없다.** `PRD.md`, `PLAN.md`, `README.md`뿐이며 PLAN Phase 0(프로젝트 부트스트랩)이 시작되지 않았다. 두 설계 문서가 유일한 진실 공급원이다.

- `PRD.md` — 요구사항(FR-001~FR-011), 데이터 모델, 수용 기준, 확정된 기술 결정. **무엇을 만들지**의 기준.
- `PLAN.md` — Phase 0~10의 작업·완료 기준·바이브 코딩 프롬프트. **어떤 순서로 만들지**의 기준.

두 문서는 상호 참조하므로 한쪽만 고치면 어긋난다. 요구사항·데이터 모델은 PRD를 먼저 고치고 PLAN을 맞춘다. 문서는 한국어로 작성한다.

문서 상단의 `rev.N`은 문서 개정 번호이고 `v0.1.0` / `v0.2` 등은 제품 버전이다. PLAN §9의 로드맵 버전과 혼동하지 말 것.

## 제품 한 줄 요약

Windows에서 Claude Code의 로컬 JSONL 로그(`~/.claude/projects/<해시>/*.jsonl`)를 감시·파싱해 토큰과 원화 비용을 트레이와 한국어 대시보드에 보여주는 로컬 전용 Tauri 앱. 서버 없음, 원문 저장 없음.

## 아키텍처 (Phase 0 이후 구현될 구조)

단방향 파이프라인이며, 각 단계는 인접 단계만 안다. 파싱·저장·집계·UI를 직접 연결하지 않는다.

```
.claude/projects/*.jsonl
  → collectors/  (notify watcher, file_key 기준 증분 읽기)
  → parser/      (JSONL 1행 → UsageEvent, event_hash 생성)
  → pricing/ + exchange/  (USD/KRW 비용, 같은 트랜잭션에서 계산)
  → storage/     (SQLite: sessions / usage_events / costs / file_offsets
                          / parse_errors / daily_rollups / app_settings)
  → aggregation/ → commands.rs (Tauri command) → React 대시보드 + tray/
```

`UsageCollector` 인터페이스를 두어 MVP의 `ClaudeCodeCollector` 외 수집기를 나중에 붙일 수 있게 한다(PRD §9).

## 깨뜨리기 쉬운 핵심 불변식

이 프로젝트의 가치는 데이터 정확성이다. 아래는 여러 문서를 읽어야 알 수 있고, 어기면 조용히 데이터가 틀어지는 규칙들이다.

- **`event_hash`는 내용 기반이다.** 파일 경로·오프셋을 해시 입력에 절대 넣지 않는다. 중복 방지의 최종 방어선은 애플리케이션 로직이 아니라 `usage_events(event_hash)` UNIQUE 제약 + `ON CONFLICT DO NOTHING`이다. 그래서 파일명이 바뀌거나 전체 재스캔을 해도 행 수가 늘지 않아야 한다.
- **`file_offsets`는 성능 최적화지 정확성 장치가 아니다.** 키는 경로가 아니라 `file_key`(volume serial + file index). 오프셋을 잃어도 중복이 생기면 안 된다.
- **컨텍스트 사용률은 로그에 없다.** `(input + cache_read + cache_write) / context_window`로 앱이 계산하며, 모델 `context_window`는 가격표에서 온다. 파서가 가격표 조회에 의존하는 유일한 지점이다. 계산값은 `is_estimated = true` → UI에 `(추정)`.
- **캐시 읽기와 캐시 쓰기는 단가가 다르다.** 절대 합산해 한 단가로 계산하지 않는다.
- **비용은 제공값 우선.** 로그에 `costUSD`가 있으면 `cost_source = provided`로 그 값을 쓰고, 없을 때만 가격표 추정값을 쓴다. 가격 정보가 없으면 0원이 아니라 `계산 불가`.
- **과거 비용은 불변.** 가격표나 환율을 바꿔도 이미 계산된 `costs` 행은 재계산하지 않는다. 예외는 `is_estimable = false`로 남아 계산 자체를 못 했던 행의 백필뿐이다.
- **KST 집계는 `local_date` / `local_month` 컬럼으로 한다.** SQLite에 타임존 DB가 없으므로 저장 시 UTC+9를 적용해 비정규화한다. 조회 시점에 변환하지 않는다.
- **`delete_all_data`는 `file_offsets`를 지우지 않는다.** 지우면 watcher가 기존 로그를 즉시 재수집해 "삭제 후 빈 상태" 수용 기준이 깨진다. 과거 데이터를 다시 읽는 것은 별도의 `rescan_all` 기능이다.
- **원문 페이로드는 어디에도 남기지 않는다.** 도메인 타입, DB, 로그, `parse_errors` 전부. 원문 저장 기능은 MVP에서 코드 경로 자체가 없다.
- **세션 키는 `(source, session_key)`.** `sessionId`를 UUID로 가정하지 않는다. 세션 종료는 마지막 이벤트 후 30분 무활동으로 판정한다.

## 재논의하지 말 것 (PRD §13에서 확정)

| 항목 | 결정 |
|---|---|
| DB 접근 | `rusqlite` + `refinery` (`sqlx` 아님) |
| 설치 번들러 | NSIS **per-user** 고정 (WiX MSI는 UAC를 유발해 제외) |
| E2E | `tauri-driver` + WebdriverIO (Playwright로 Tauri 앱을 직접 구동할 수 없음) |
| 프론트 린터 | Biome (ESLint + Prettier 아님) |
| 트레이 표시 | Windows 트레이는 아이콘 옆 텍스트가 불가. 아이콘=상태 색상, 수치=툴팁 + 팝업 |
| 환율 | 기본 수동 모드. 자동 조회를 켜야만 외부 요청이 발생한다 |
| 라이선스 / 보관 기간 기본값 | MIT / 무제한 |

MVP에서 앱이 하는 외부 통신은 환율 조회 1건뿐이며 기본값은 비활성이다. 여기에 네트워크 호출을 추가하면 PRD §8의 외부 통신 표와 `docs/privacy.md`를 함께 갱신해야 한다.

## 명령어

Phase 0이 끝나기 전에는 실행 가능한 명령이 없다. PLAN Phase 0에서 아래가 성립하도록 만든다.

```bash
pnpm install
pnpm dev          # Vite 개발 서버
pnpm tauri dev    # 앱 창 실행
pnpm tauri build  # NSIS per-user 설치 파일 생성 (UAC 프롬프트 없어야 함)
```

Rust 쪽은 `apps/desktop/src-tauri`에서 실행한다.

```bash
cargo test                      # 통합 테스트 전체
cargo test --test parser        # 특정 통합 테스트 파일만
cargo test parses_multi_model   # 이름으로 단일 테스트만
cargo clippy -- -D warnings     # 경고 0개 유지
```

프론트엔드는 `apps/desktop`에서 실행한다.

```bash
pnpm vitest run                       # 전체
pnpm vitest run src/lib/format.test.ts  # 단일 파일
pnpm biome check --write .
```

fixture와 Rust 테스트는 `src-tauri/` 아래에 둔다. 루트 `tests/`는 Cargo가 통합 테스트로 인식하지 않고, 루트 `fixtures/`는 `CARGO_MANIFEST_DIR` 기준 경로가 어긋난다.

## 작업 방식

PLAN §7의 작업 템플릿(목표 / 현재 상태 / 변경 범위 / 변경하지 않을 것 / 수용 기준 / 테스트)을 따르고, Phase 순서를 건너뛰지 않는다. 특히 수집기와 파서가 안정되기 전에 대시보드를 먼저 만들지 않는다.

**Phase 3 착수 전제조건: 실제 Claude Code JSONL 샘플 확보.** PLAN에 적힌 샘플 JSON 구조는 가정이며 Claude Code 버전에 따라 필드명이 다를 수 있다. 실제 로그로 `src-tauri/fixtures/claude-code/`를 채운 뒤 파서를 작성한다.
