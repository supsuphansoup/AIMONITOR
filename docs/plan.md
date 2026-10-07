# Plan: v0.6 — 데스크톱 전용 UI

Status: done (2026-10-07, 사용자 승인: 웹 버전 완전 삭제; 린트·전체 테스트·임시 DB API 확인 완료).
근거: [intent.md](intent.md), [기능별 명세](spec/README.md). 이전 v0.5 계획과 검증 기록은 git 이력과 [CHANGELOG](../CHANGELOG.md)에 있다.

## 작업 범위

1. 웹 UI 소스·의존성·빌드 산출물을 제거한다. UI는 Windows 데스크톱 앱만 남긴다.
2. 백엔드 정적 파일 서빙·SPA fallback을 제거하고 `GET /`가 JSON 404를 반환하도록 한다. `/health`와 `/api/v1/**`는 유지한다.
3. 웹 개발 서버 CORS 출처를 제거한다. CORS 미들웨어 순서와 오류·비밀 보호 규칙은 유지한다.
4. Docker를 API 전용 Python 이미지로 바꾸고 웹 CI 작업을 제거한다. 연결 AI API의 공유 데스크톱 헬퍼는 이미지에 포함한다.
5. 지침 정본 AGENTS.md와 README·spec을 데스크톱 UI와 HTTP API 기준으로 갱신한다. CLAUDE.md·GEMINI.md는 포인터만 유지한다.

## 변경 파일

- 백엔드: `backend/app/main.py`, `backend/app/core/config.py`, HTTP·헬스체크 테스트.
- 배포: `Dockerfile`, `.dockerignore`, `.gitignore`, `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`, `pyproject.toml`.
- 문서: `AGENTS.md`, `README.md`, `docs/intent.md`, `docs/plan.md`, `docs/spec/`의 웹 화면 언급. `dashboard.md`는 제거 안내만 남긴다.
- 동시 작업 경계: 데스크톱 코드·실행기·`test_desktop_*.py`·`spec/desktop.md`는 별도 담당자가 수정한다.

## 검증

```bash
ruff check .
python -m pytest -q
```

- 실행 중인 앱·서버를 종료하거나 재시작하지 않는다. `.env`와 운영 DB는 변경하지 않는다.
- 테스트는 임시 DB와 TestClient에서 실행한다. 루트·정적 경로의 JSON 404, 헬스체크, 데스크톱 API·키 관리·연결 AI·CSV 유지, CORS 오류 헤더를 확인한다.
- 웹 기술 문자열 잔존 검색은 작업 보고서에 원문과 의도된 잔존 이유를 기록한다. CHANGELOG의 과거 출시 기록과 데스크톱 환율 조회 URL은 유지한다.
- 완료 여부와 정확한 명령 출력은 코디네이터에 전달한 작업 보고서에서 확인한다.
- 검증(2026-10-07): `ruff check .` → `All checks passed!`; `python -m pytest -q` → `414 passed, 1 warning`. 임시 DB TestClient의 루트·정적 경로는 JSON 404, 헬스체크·데스크톱·공개 API는 정상 응답했다.
- Docker COPY 파일만 별도 임시 디렉터리에 조립한 API 검증도 통과했다. 로컬에 Docker 실행 파일이 없어 실제 이미지 빌드는 CI에서 확인해야 한다.

## 다음 후보 (승인 시 새 plan)

- 2027-01-01 전 단가표 갱신 (Gemini 프로모션 종료) — [spec/cost.md](spec/cost.md)
- 긴 프롬프트 구간·1시간 캐시·Batch 할인 반영 여부 — [spec/cost.md](spec/cost.md)
- 키 이름 기반 원문 차단의 한계, API 키 회전 절차 — [spec/security-ops.md](spec/security-ops.md)
- `cost_hourly` 별칭의 의미 불일치 — [spec/alerts.md](spec/alerts.md)
