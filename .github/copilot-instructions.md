# Copilot 지침 — K-LLM Monitor

전체 지침은 저장소 루트의 `AGENTS.md`에 있다. 아래는 인라인 제안에서도 지켜야 하는 핵심 규칙만 옮긴 것이며, 규칙을 고칠 때는 `AGENTS.md`를 먼저 고친다.

## 이 저장소의 상태

코드가 아직 없다. `PRD.md`(요구사항·데이터 모델)와 `PLAN.md`(Phase 0~10)가 유일한 진실 공급원이다. 구현은 Phase 순서를 따르며, 수집기와 파서가 안정되기 전에 대시보드를 만들지 않는다.

Windows 전용 Tauri 2 + Rust + React 18 + TypeScript 앱이다. Claude Code의 로컬 JSONL 로그를 읽어 토큰과 원화 비용을 보여준다. 서버 없음, 원문 저장 없음. 문서와 UI 문구는 한국어로 쓴다.

## 데이터 정확성 규칙 (어기면 조용히 틀어진다)

- `event_hash`는 **이벤트 내용만으로** 만든다. 파일 경로·오프셋을 해시 입력에 넣지 않는다. 중복 방지는 `usage_events(event_hash)` UNIQUE 제약 + `ON CONFLICT DO NOTHING`이 최종 방어선이다.
- `file_offsets`는 성능 최적화일 뿐이다. 키는 경로가 아니라 `file_key`(volume serial + file index). 오프셋을 잃어도 중복이 생기면 안 된다.
- 캐시 **읽기 단가와 쓰기 단가는 다르다.** `cache_read_per_mtok`과 `cache_write_per_mtok`을 각각 곱한다. 합산 금지.
- 비용은 로그의 `costUSD`(제공값)를 우선 사용하고, 없을 때만 가격표 추정값을 쓴다(`cost_source`로 구분).
- 가격 정보가 없으면 **0원이 아니라 `계산 불가`**로 표시한다.
- 이미 계산된 `costs` 행은 가격표·환율이 바뀌어도 재계산하지 않는다.
- 컨텍스트 사용률은 로그에 없다. `(input + cache_read + cache_write) / context_window`로 계산하는 추정값이며 UI에 `(추정)` 라벨이 필요하다.
- KST 집계는 저장 시 비정규화한 `local_date` / `local_month` 컬럼으로 한다. 조회 시점에 타임존을 변환하지 않는다.
- `delete_all_data`는 `file_offsets`를 지우지 않는다(지우면 즉시 재수집된다). 재수집은 별도 `rescan_all`.

## 개인정보 규칙

프롬프트·응답 원문은 도메인 타입·DB·로그·`parse_errors` 어디에도 넣지 않는다. 원문 저장 기능은 MVP에 코드 경로 자체가 없다. 외부 통신은 환율 조회 1건뿐이며 기본값은 비활성이다.

## 스택 결정 (대안을 제안하지 말 것)

`rusqlite` + `refinery` (sqlx 아님) / NSIS per-user (WiX 아님) / `tauri-driver` + WebdriverIO (Playwright 아님) / Biome (ESLint + Prettier 아님) / ECharts / `notify` v6 / pnpm 워크스페이스.

Rust 테스트와 fixture는 `apps/desktop/src-tauri/` 아래에 둔다. 루트 `tests/`는 Cargo가 인식하지 않는다.
