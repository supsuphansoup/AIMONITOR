# Spec: 대시보드 (Dashboard)

## Requirements

- UI-1 기간을 1H / 6H / 24H / 7D / 30D로 전환하며 KPI·차트·트레이스를 본다.
- UI-2 백엔드 장애와 "트래픽 없음"을 구분해 보여준다 (연결 표시등 + 실패 배너).
- UI-3 모르는 값은 모른다고 표시한다: 비용 `계산 불가`, 백분위 `표본 추정`, 시계열 `구간 일부 누락`.
- UI-4 트레이스 표에서 provider·status 필터, 페이지 이동, 상세 모달, CSV 내보내기를 한다.
- UI-5 알림 규칙 생성·일시 중지·삭제, 수동 평가, 발동 이력을 대시보드에서 관리한다.
- UI-6 인증이 켜진 서버에서 401이 나면 API 키 입력을 안내한다.

## Design

### 화면 구성 (`frontend/src/`)

| 컴포넌트 | 내용 |
| :--- | :--- |
| `Header.tsx` | 기간 선택, 연결 표시등, 새로고침, 데모 트래픽, 알림·설정 버튼 |
| `ConnectionBanner.tsx` | API 실패·401 안내 |
| `KPICards.tsx` | 총 호출, 평균/P95 지연, 누적 비용(+ `N건 계산 불가`), 에러율, 캐시 토큰 |
| `Charts.tsx` | 지연 시간 추이(영역), 모델별 비용·호출(막대). 지연 로딩(초기 번들 축소) |
| `TraceTable.tsx` | 라이브 트레이스, 에러 행 강조, `1–50 / 158건 · 1/4 페이지` 형식, CSV 버튼 |
| `TraceDetailModal.tsx` | 토큰 분해·에러 메시지, `Esc`로 닫기 |
| `AlertsModal.tsx` | 규칙 CRUD, 지표 목록은 `GET /alerts/metrics`에서 조회 |
| `SettingsModal.tsx` | API 키 입력 (브라우저 `localStorage`의 `aimonitor.apiKey`) |
| `api.ts` | 유일한 HTTP 클라이언트. 인증 헤더, 오류 변환, `X-Total-Count` 폴백 |

### 기간별 버킷

| 기간 | `hours_back` | `interval_minutes` |
| :--- | ---: | ---: |
| 1H | 1 | 1 |
| 6H | 6 | 5 |
| 24H | 24 | 15 |
| 7D | 168 | 60 |
| 30D | 720 | 360 |

### 동작 규칙

- 5초 주기 폴링. 탭이 숨겨지면 멈추고, 다시 보이면 즉시 갱신 후 재개.
- 모든 시각은 API의 UTC 오프셋 문자열을 브라우저 로컬 시각으로 표시한다.
- `X-Total-Count` 헤더가 없으면(프록시가 숨긴 경우) 현재 페이지 행 수로 추정 — 헤더 없음을 0건으로 취급하지 않는다.
- 개발 서버 `http://localhost:3000`이 `/api`를 8000으로 프록시. 운영은 백엔드가 `frontend/dist`를 서빙.

## Open questions

- WebSocket 실시간 푸시 → 이월: 5초 폴링으로 충분.
- E2E 브라우저 테스트 자동화 → 이월: 컴포넌트 테스트(Vitest)까지만 CI에 있다.

## Areas of concern

- **데모 트래픽 버튼이 운영 DB에 가짜 트레이스를 쓴다.** 환경과 무관하게 노출되며, 쓰인 데이터는 `demo-feed` 태그만 붙고 집계·알림·CSV에 그대로 섞인다. 운영(`ENVIRONMENT=production`)에서 숨길지 결정 필요.
- API 키를 `localStorage`에 평문 저장한다. XSS가 생기면 키가 노출된다. 신뢰된 네트워크 전제의 편의 기능으로 유지할지 결정 필요.
