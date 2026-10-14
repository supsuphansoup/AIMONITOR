# Plan: v0.6.0 공개 배포

Status: in progress (2026-10-08). 근거: [intent.md](intent.md), [기능별 명세](spec/README.md). 이전 계획과 검증 기록은 git 이력과 [CHANGELOG](../CHANGELOG.md)에 있다.

## 완료

- CI 복구(SQLModel 0.0.48 호환, Windows 데스크톱 job), MIT `LICENSE`.
- 보안: 교차 출처 쓰기·DNS rebinding 차단, 다른 앱 DB 읽기 전용.
- Windows exe(`build-windows/`)와 릴리스 워크플로 — `v0.6.0-rc.1` 초안 릴리스로 빌드·smoke test 통과 확인.
- 문서: README(한국어·영어) 랜딩 중심으로 정리, [privacy.md](privacy.md), 명세를 기능별로 분리(desktop·accounts·keys), `CONTRIBUTING.md`·`SECURITY.md`·이슈 템플릿.
- 쓰지 않는 모듈·실행기·헬퍼 제거.
- Claude 사용량 조회 User-Agent를 `AIMONITOR/<버전>`으로, 커밋 작성자 이메일을 GitHub noreply로 변경.

## 남은 작업 (사람 확인 필요)

1. exe 첫 실행 화면 확인 — 실행 중인 앱을 끄거나 Windows Sandbox에서 rc 초안 zip으로.
2. 스크린샷(위젯·메인 창) → `docs/images/`, README의 주석 자리에 연결.
3. PR #1 머지 → 저장소 설명·topics → 비공개 취약점 제보 켜기 → `v0.6.0` 태그·초안 게시, rc 태그·초안 삭제 → Public 전환.

## 검증

```bash
ruff check .
python -m pytest -q
```

- 릴리스 워크플로의 smoke test(`--self-test`, 백엔드 기동, 기록 저장)가 통과해야 초안을 게시한다.

## 다음 후보 (승인 시 새 plan)

- 2027-01-01 전 단가표 갱신 (Gemini 프로모션 종료) — [spec/cost.md](spec/cost.md)
- 화면 영어 지원, macOS 지원 여부 — [intent.md](intent.md) Open questions
- 키 이름 기반 원문 차단의 한계, API 키 회전 절차 — [spec/security-ops.md](spec/security-ops.md)
- `cost_hourly` 별칭의 의미 불일치 — [spec/alerts.md](spec/alerts.md)
