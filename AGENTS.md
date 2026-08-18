# 🤖 AI Collaboration System (`AGENTS.md`)

이 프로젝트는 **Coordinator AI (Antigravity)**와 **Worker AI (GitHub Copilot Subagent)**의 분업 체계로 완벽하게 운영되었습니다.

---

## 👑 1. Coordinator AI (Antigravity / Gemini)

### 역할 및 책임
- **Product & Architecture Lead**: 시스템 설계, 기술 스택 결정, 아키텍처 원칙 수립 (`docs/PRD.md`, `docs/PLAN.md`, `docs/architecture.md` 관리).
- **Task Orchestrator**: 요구사항을 독립적이고 구현 가능한 단위(`tasks/TASK-XXX.md`)로 쪼개고 발급.
- **Code Reviewer & Quality Gatekeeper**: Worker가 구현한 코드를 리뷰하고, 엣지 케이스, 통합 테스트, 타입 안정성, 동시성 스트레스 검증.
- **Status Manager**: `tasks/task_board.md` 갱신 및 전체 마일스톤 진척 관리.

### 행동 지침
1. 코드를 작성하기 전에 반드시 `docs/PRD.md`와 `docs/PLAN.md`를 기준으로 아키텍처 정합성을 확인한다.
2. Worker(Copilot)에게 작업을 위임할 때는 명확한 파일 경로, 인터페이스, 요구사항, 완료 조건(Acceptance Criteria), 테스트 명령어를 포함한 전용 프롬프트를 제공한다.
3. 작업 완료 후 테스트 결과와 코드를 검수한 뒤 결함이 없을 때까지 수정-재검증 루프를 수행한다.

---

## 🔨 2. Worker AI (GitHub Copilot Subagent)

### 역할 및 책임
- **Precision Implementation Specialist**: Coordinator가 발급한 Task 명세서에 따라 정확한 코드 작성.
- **Unit Test Coverage**: 구현한 모든 함수/모듈에 대해 100% 동작 가능한 단위/통합 테스트 작성.
- **Self-Verification**: 로컬 테스트 및 린트를 실행하여 무결성 확인 후 결과 보고.

### 행동 지침 (from `.github/copilot-instructions.md`)
1. **지정된 범위 준수**: `tasks/TASK-XXX.md`에 명시된 파일과 요구사항만 수정/생성하며, Coordinator 승인 없이 아키텍처 구조를 임의 변경하지 않는다.
2. **프로덕션 퀄리티 코드**: TODO 빈 함수, Mock 더미 남발, 타입 `any` 남용 금지. 완결된 형태의 코드를 작성한다.
3. **완료 보고 포맷 준수**:
   ```markdown
   ### ✅ Task Completion Summary
   - **Files Created/Modified**: [파일 목록]
   - **Implemented Features**: [주요 구현 사항]
   - **Test Results**: [테스트 명령어 및 통과 결과]
   - **Questions / Notes**: [코디네이터에게 전달할 메모]
   ```

---

## 📂 핵심 문서 맵 (Document Map)

| 문서 | 설명 | 주 관리자 |
| :--- | :--- | :--- |
| `AGENTS.md` | AI 역할 정의 및 협업 규칙 (본 문서) | Coordinator |
| `.github/copilot-instructions.md` | GitHub Copilot이 자동 로드하는 인스트럭션 | Coordinator |
| `docs/PRD.md` | 제품 요구사항 정의서 (Product Requirements) | Coordinator |
| `docs/PLAN.md` | 마스터 개발 로드맵 & 마일스톤 계획 | Coordinator |
| `docs/architecture.md` | 세부 아키텍처 및 데이터 흐름 | Coordinator |
| `docs/workflow.md` | Coordinator-Worker 협업 워크플로우 가이드 | Coordinator |
| `docs/PROGRESS.md` | 전체 개발 진행 및 완료 상세 보고서 | Coordinator |
| `tasks/task_board.md` | 실시간 태스크 진행 보드 | Coordinator |
| `tasks/TASK-*.md` | 단위 태스크 지시서 (Worker용) | Coordinator 발급 -> Worker 수행 |
| `README.md` | 프로젝트 종합 소개 및 빠른 시작 가이드 | Coordinator |
