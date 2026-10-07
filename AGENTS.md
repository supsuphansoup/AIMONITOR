# AGENTS.md — AIMONITOR

모든 AI 코딩 에이전트가 작업 전에 읽는 프로젝트 설명서다. `CLAUDE.md`·`GEMINI.md`·`.github/copilot-instructions.md`는 이 파일을 가리키기만 한다. 지침은 이 파일만 고친다.

문서 순서: [docs/intent.md](docs/intent.md) (무엇을·왜) → [docs/spec/](docs/spec/README.md) (기능별 규칙과 수치) → [docs/plan.md](docs/plan.md) (지금 할 작업). 다음 단계는 항상 앞 단계 문서를 읽고 시작한다.

## Commands

```bash
pip install -r backend/requirements.txt          # 백엔드 의존성
pip install -r desktop/requirements.txt          # 데스크톱 의존성
python -m pytest -q                               # 백엔드 + SDK + 데스크톱 테스트 (루트에서 실행)
ruff check .                                      # 파이썬 린트
python run_desktop.py                              # 데스크톱 앱 (사람이 직접 실행)
python -m uvicorn backend.app.main:app --port 8000 --reload   # 개발 서버 (사람이 직접 실행)
python scripts/simulate.py --batches 20 --spread-hours 6      # 데모 트래픽
```

## Conventions

- Python 3.10+, 타입 힌트는 `typing.Optional`/`Dict` 스타일 유지 (ruff `UP` 규칙 의도적으로 제외). 줄 길이 110.
- 시각: **저장은 naive UTC, 응답은 오프셋 포함 ISO-8601.** 변환은 `backend/app/core/timeutil.py`만 사용한다.
- 비용: 단가를 모르면 0이 아니라 `cost_source='unknown'` → 화면 `계산 불가`, CSV 빈 칸.
- 캐시 **읽기·쓰기 토큰은 단가가 다르다.** 합산해 한 단가로 계산하지 않는다.
- 프롬프트·응답 원문은 DB·로그·오류 응답·CSV 어디에도 남기지 않는다 (`core/sanitize.py`).
- 오류 응답에 예외 문자열을 싣지 않는다. 서버 로그로만 남긴다.
- 의존성은 `backend/requirements.txt`, `desktop/requirements.txt`에 있는 것만 쓴다. 추가는 사람 승인 후.

## Architecture

```
SDK(sdk/python) ─POST /api/v1/traces→ routers/traces → services/cost → DB(SQLite WAL, SQLModel)
DB → services/aggregator → routers/metrics → desktop(Windows 메인 창·트레이·위젯, HTTP API)
DB → services/alert ← services/scheduler(60초) → Slack/Discord Webhook
core/: config · security(API 키) · sanitize · timeutil · metrics_catalog   db/session.py: ensure_schema
```

단일 컨테이너(Dockerfile)는 API만 서빙한다. UI는 데스크톱 앱이며 웹 대시보드는 제공하지 않는다. 기능별 상세는 `docs/spec/`.

## Verifying your work

- 완료 보고 전 위 Commands의 테스트·린트·빌드 중 변경 범위에 해당하는 것을 실행하고 **출력을 그대로 보여줄 것.** 실패하면 코드를 고친다.
- 동작을 바꿨는데 테스트가 없으면 테스트를 추가한다 (`backend/tests/`, 데스크톱은 `backend/tests/test_desktop_*.py`, SDK는 `sdk/python/tests/`).
- 스키마를 바꾸면 기존 DB 업그레이드 테스트(`test_schema_upgrade.py`)를 함께 확인한다.
- 개발 서버·데스크톱 앱처럼 계속 켜져 있는 프로그램은 에이전트가 띄우지 말고 사람에게 실행을 요청한다.

## Things the agent gets wrong

- `ORDER BY ... ASC LIMIT n`으로 상한을 걸어 **최신 데이터를 잘라냄.** 상한은 항상 최신 우선(`DESC`)으로, 잘렸으면 `partial`/`latency_sampled`로 알린다.
- 오프셋 없는 datetime을 응답해 데스크톱 화면의 시각이 9시간 어긋남.
- 모르는 모델에 임의 단가를 붙이거나, 유료 API를 오픈 웨이트 모델명만 보고 `local_free`로 처리함.
- 단가를 기억으로 적거나 provider 캐시 배수를 가정함. 공식 가격 페이지(`PRICING_SOURCES`)에서 확인하고 `PRICING_AS_OF`를 갱신한다.
- 쿨다운을 파이썬에서 읽고 나중에 써서 다중 워커에서 알림 중복. 발동 권한은 조건부 UPDATE(`claim_rule`)로만 얻는다.
- 수동 평가(`ignore_cooldown`)가 다른 규칙의 쿨다운 상태를 건드림. 수동 평가는 상태를 바꾸지 않는다.
- `ALTER TABLE ADD COLUMN`만 하고 인덱스를 빠뜨림.
- CORS 미들웨어보다 바깥에서 오류 응답을 만들어 CORS 헤더 누락.
- 누락 값·`null`·`None`을 숫자로 변환하면서 "값 없음"을 0으로 취급함.
