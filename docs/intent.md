# Intent: AIMONITOR

Author: supsuphansoup. Status: accepted (v0.3.0 구현 기준으로 역작성, 2026-10-06; v0.6 데스크톱 전용 UI, 2026-10-07; 공개 배포 기준으로 갱신, 2026-10-08).

## Problem — 무엇을 해결/확인하고 싶은지
Claude Code·Codex·Copilot 같은 AI 코딩 도구는 5시간·주간 한도가 있지만, 남은 양을 보려면 도구마다 따로 확인해야 한다.
계정을 여러 개(개인·회사) 쓰면 어디서 얼마나 썼는지 더 헷갈린다.
직접 만든 서비스의 LLM 호출(OpenAI·Anthropic·Gemini·로컬 모델)도 비용·지연·에러를 보고 싶지만, 외부 SaaS에 프롬프트를 넘기고 싶지 않다.

## Proposed outcome — 완성되면 무엇이 가능해지는지
Windows 트레이·항상 위 위젯에서 도구별·계정별 남은 한도와 사용량·비용을 한눈에 본다. 설치는 exe 하나로 끝난다.
SDK 몇 줄이면 내 앱의 LLM 호출도 같은 화면에 쌓이고, 조건을 넘으면 Slack/Discord로 한 번만 알림이 오며, 정산용 CSV를 받을 수 있다.
숫자를 모를 때는 모른다고 표시한다 — 틀린 숫자보다 `계산 불가`·`확인 불가`가 낫다.

## Affected users and systems — 누가, 어떤 환경에서 쓰는지
AI 코딩 도구를 쓰는 개인 개발자, LLM 기능을 운영하는 소규모 팀. 공개 오픈소스(MIT)로 배포한다.
Windows 10/11 데스크톱 앱(exe 또는 소스 실행)과 같은 PC의 로컬 API 서버(`127.0.0.1`). 서버만 Docker로 따로 띄울 수도 있다.
웹 대시보드는 없다(v0.6에서 제거). SDK와 HTTP API는 유지한다.

## Constraints — 기술 스택, 꼭 지킬 조건
FastAPI + SQLModel(SQLite WAL) / Windows 데스크톱(customtkinter·트레이·위젯) / Python SDK(httpx) / PyInstaller exe. Python 3.10+.
다른 도구의 로그·로그인 파일은 읽기만 하고, 읽고 보내는 것은 [privacy.md](privacy.md)에 전부 공개한다. 토큰은 발급처에만 보낸다.
프롬프트·응답 원문은 어디에도 저장하지 않는다. 시각은 UTC로 저장하고 오프셋을 붙여 응답한다.
단가 미상은 0이 아니라 `계산 불가`. 캐시 읽기·쓰기 단가는 분리한다. 외부 노출 전 API 키 필수.

## Open questions — 아직 결정 못 한 것
macOS·Linux 데스크톱을 지원할 것인가? 화면을 다국어(영어)로 낼 것인가?
Claude 한도 조회가 비공식 엔드포인트에 의존하는데, 공식 수단이 생기면 바꿀 것인가?
PostgreSQL을 정식 지원할 것인가? 단일 공유 키를 넘어 사용자별 계정·권한이 필요한가? 단가표를 코드 대신 설정/DB로 관리할 것인가?
