# RaybanPT 디자인 리서치 (빠른 적용용)

## 목표
- 현재 기능 유지
- 1~2일 내 시각 품질/신뢰감 개선
- iPhone 현장 사용성(큰 터치 타겟, 상태 가독성) 우선

## 참고 소스
- Apple Human Interface Guidelines: https://developer.apple.com/design/human-interface-guidelines
- Design Systems Repo(레퍼런스 모음): https://designsystemsrepo.com/design-systems/
- Radix UI (웹 컴포넌트 품질 참고): https://www.radix-ui.com/
- shadcnblocks (레이아웃/섹션 패턴 참고): https://www.shadcnblocks.com/

## 현재 UI 문제(요약)
- 상태/행동 우선순위가 한눈에 안 보임
- 중요한 버튼(시작/재연결/전송) 시각적 계층 약함
- 카드/텍스트 밀도 높아 피로감 큼

## 바로 적용할 디자인 방향
1) 정보 계층
- 상단: 연결 상태 + 환자 컨텍스트
- 중단: 카메라/미리보기
- 하단: 1차 행동(촬영/녹음/전송)

2) 상태 색 규칙 (의료 현장형)
- 정상: Green
- 주의/대기: Amber
- 실패/차단: Red
- 중립 정보: Gray

3) 컴포넌트 규격
- 주요 버튼 높이 48~56
- 상태 pill 최소 높이 32
- 본문 대비 14~16pt, 보조 12~13pt

4) 텍스트 톤
- 기술 문구 대신 행동 유도형 문구
  - "기기 없음" -> "Ray-Ban 연결 필요"
  - "스트리밍 중지됨" -> "스트리밍 시작 버튼을 눌러주세요"

## 오픈소스 적용 전략
- iOS는 HIG 우선(직접 구현)
- 웹(rayban-local-bridge)은 shadcn 스타일 토큰 차용(간격/타이포/카드)

## 제안 스프린트 (MVP)
- D1: 디자인 토큰(색/폰트/간격) + 상태 pill 리디자인
- D2: StreamView 레이아웃 재배치(상태/행동 우선순위)
- D3: 라벨링/차트 화면 통일 + 스크린샷 리뷰

## 다음 액션
- StreamView에 DesignToken.swift 도입
- 상태 문구 10개 표준화
- 주요 버튼 스타일 1종으로 통일
