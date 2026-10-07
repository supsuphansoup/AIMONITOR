# Intent: AIMONITOR

Author: supsuphansoup. Status: accepted (v0.3.0 구현 기준으로 역작성, 2026-10-06; v0.6 데스크톱 전용 UI로 갱신, 2026-10-07).

## Problem — 무엇을 해결/확인하고 싶은지
LLM 호출(OpenAI·Anthropic·Gemini·로컬 모델)의 비용·지연 시간·에러를 서비스 안에서 바로 보기 어렵다.
외부 SaaS에 프롬프트를 넘기지 않고, 자체 서버에서 "지금 얼마나 쓰고, 얼마나 느리고, 무엇이 깨지는지" 알고 싶다.

## Proposed outcome — 완성되면 무엇이 가능해지는지
코드 몇 줄(SDK)로 호출 기록이 쌓이고, Windows 데스크톱 앱에서 비용·지연·에러와 연결 AI 사용량을 확인한다.
조건을 넘으면 Slack/Discord로 한 번만 알림이 오고, 정산용 CSV를 받을 수 있다.
숫자를 모를 때는 모른다고 표시한다 — 틀린 숫자보다 `계산 불가`가 낫다.

## Affected users and systems — 누가, 어떤 환경에서 쓰는지
LLM 기능을 운영하는 소규모 개발팀·개인 개발자. Python 애플리케이션에서 SDK로 연동한다.
Windows 데스크톱 앱(메인 창·트레이·위젯)과 로컬 Python 또는 Docker API 서버. 기본은 localhost 전용.
웹 대시보드는 v0.6에서 제거하며 UI는 데스크톱 앱만 제공한다. SDK와 공개 HTTP API는 유지한다.

## Constraints — 기술 스택, 꼭 지킬 조건
FastAPI + SQLModel(SQLite WAL) / Windows 데스크톱(customtkinter·트레이·위젯) / Python SDK(httpx). Python 3.10+.
프롬프트·응답 원문은 어디에도 저장하지 않는다. 시각은 UTC로 저장하고 오프셋을 붙여 응답한다.
단가 미상은 0이 아니라 `계산 불가`. 캐시 읽기·쓰기 단가는 분리한다. 외부 노출 전 API 키 필수.

## Open questions — 아직 결정 못 한 것
PostgreSQL을 정식 지원할 것인가? 단일 공유 키를 넘어 사용자별 계정·권한이 필요한가?
단가표를 코드 대신 설정/DB로 관리할 것인가? 실시간 푸시(WebSocket)와 알림 채널 확장이 필요한가?
