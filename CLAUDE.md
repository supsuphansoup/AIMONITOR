# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

이 저장소의 지침은 **[AGENTS.md](AGENTS.md)에 정본이 있다.** 작업을 시작하기 전에 먼저 읽을 것.

지침을 수정할 때는 `AGENTS.md`만 고친다. 이 파일에는 내용을 복사해 두지 않는다 — 사본을 두면 반드시 어긋난다.

## 가장 자주 어기는 규칙 (전문은 AGENTS.md)

- `event_hash`는 **내용 기반**이다. 파일 경로·오프셋을 해시 입력에 넣지 않는다.
- 캐시 **읽기와 쓰기는 단가가 다르다.** 합산해 한 단가로 계산하지 않는다.
- 컨텍스트 사용률은 로그에 없다. 앱이 `context_window`로 계산하는 **추정값**이다.
- 가격 정보가 없으면 0원이 아니라 `계산 불가`로 표시한다.
- 프롬프트·응답 원문은 DB·로그·오류 기록 어디에도 남기지 않는다.
- 아직 코드가 없는 저장소다. `PRD.md` / `PLAN.md`가 진실 공급원이며 Phase 순서를 건너뛰지 않는다.
