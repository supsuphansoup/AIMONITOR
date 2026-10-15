# Plan: v0.6.0 공개 배포

Status: released (2026-10-15). 근거: [intent.md](intent.md), [기능별 명세](spec/README.md). 이전 계획과 검증 기록은 git 이력과 [CHANGELOG](../CHANGELOG.md)에 있다.

## 완료

- CI 복구(SQLModel 0.0.48 호환, Windows 데스크톱 job), MIT `LICENSE`.
- 보안: 교차 출처 쓰기·DNS rebinding 차단, 다른 앱 DB 읽기 전용.
- Windows exe(`build-windows/`)와 릴리스 워크플로 — `v0.6.0` 릴리스로 빌드·smoke test 통과 확인.
- 문서: README(한국어·영어) 랜딩 중심으로 정리, [privacy.md](privacy.md), 명세를 기능별로 분리(desktop·accounts·keys), `CONTRIBUTING.md`·`SECURITY.md`·이슈 템플릿.
- 쓰지 않는 모듈·실행기·헬퍼 제거.
- Claude 사용량 조회 User-Agent를 `AIMONITOR/<버전>`으로, 커밋 작성자 이메일을 GitHub noreply로 변경.
- 공개 전 검증(데이터·계약·개인정보·배포물·계정 수집 종단)과 발견한 결함 수정.
- `v0.6.0` 공개(2026-10-15): 저장소 설명·topics, 비공개 취약점 제보, 릴리스 게시.
- 릴리스 zip 첫 실행 확인(빈 사용자 프로필, 탭 5개)과 README 스크린샷(합성 데모 데이터).
- `v0.6.1`(2026-10-15): 위젯 폭 조절, 카드→한 줄 전환 뒤 갱신이 멈추던 문제 수정.

## 검증

```bash
ruff check .
python -m pytest -q
```

- 릴리스 워크플로의 smoke test(`--self-test`, 백엔드 기동, 기록 저장)가 통과해야 초안을 게시한다.

## 다음 후보 (승인 시 새 plan)

- 2027-01-01 전 단가표 갱신 (Gemini 프로모션 종료) — [spec/cost.md](spec/cost.md)
- 미니 위젯의 기본 폭은 여전히 내용 전체 길이(Claude·Codex만으로 1880px) — 끌어서 줄일 수는 있음(v0.6.1). 차트의 모델 이름이 잘림 — [spec/desktop.md](spec/desktop.md)
- 화면 영어 지원, macOS 지원 여부 — [intent.md](intent.md) Open questions
- 키 이름 기반 원문 차단의 한계, API 키 회전 절차 — [spec/security-ops.md](spec/security-ops.md)
- `cost_hourly` 별칭의 의미 불일치 — [spec/alerts.md](spec/alerts.md)
