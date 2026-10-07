# 웹 대시보드 제거 안내

웹 대시보드는 v0.6에서 제거되었다. UI는 [Windows 데스크톱 앱](desktop.md)만 제공한다.
백엔드는 정적 파일이나 SPA 경로를 제공하지 않으며 `GET /`는 JSON 404를 반환한다.
`/health`와 `/api/v1/**` 공개 API(키 관리·연결 AI·CSV 포함)는 유지한다.
API 계약은 [기능별 명세](README.md)를 따른다.
