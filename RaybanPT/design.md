# RaybanPT — Design System

> Apple Human Interface Guidelines 기반 SwiftUI 디자인 토큰

---

## 1. Color

System semantic color 만 사용. 절대 hex 하드코딩 금지.

| 역할 | Token |
|------|-------|
| 배경 | `.systemBackground` |
| 2차 배경 | `.secondarySystemBackground` |
| 카드/시트 | `.tertiarySystemBackground` |
| 기본 텍스트 | `.label` |
| 보조 텍스트 | `.secondaryLabel` |
| 구분선 | `.separator` |
| 활성 강조 | `.systemBlue` (tint) |
| 위험/중지 | `.systemRed` |
| 성공/완료 | `.systemGreen` |
| 경고/녹화 | `.systemOrange` |
| 카메라 배경 | `Color.black` |

---

## 2. Typography

| 용도 | Style |
|------|-------|
| 대형 타이틀 | `.largeTitle` + `.bold` |
| 섹션 헤더 | `.headline` |
| 본문 | `.body` |
| 캡션 | `.caption` `.caption2` |
| 수치 (각도 등) | `.monospacedDigit()` |

---

## 3. Spacing (8pt grid)

```
XS = 4
SM = 8
MD = 16
LG = 24
XL = 32
```

---

## 4. Shape

```swift
// 카드
RoundedRectangle(cornerRadius: 12, style: .continuous)

// 칩/배지
Capsule()

// 버튼
.cornerRadius(10)
```

---

## 5. Shadows

```swift
// 카드 subtle
.shadow(color: .black.opacity(0.06), radius: 8, y: 2)

// 플로팅 버튼
.shadow(color: .black.opacity(0.15), radius: 16, y: 6)
```

---

## 6. Animation

```swift
// 일반 상태 전환
.animation(.spring(response: 0.35, dampingFraction: 0.75), value: state)

// 등장
.transition(.opacity.combined(with: .scale(0.95)))

// 카메라 셔터
.scaleEffect(isCapturing ? 0.92 : 1.0)
.animation(.easeOut(duration: 0.1), value: isCapturing)
```

---

## 7. Haptics

```swift
// 촬영
UIImpactFeedbackGenerator(style: .medium).impactOccurred()

// 완료
UINotificationFeedbackGenerator().notificationOccurred(.success)

// 오류
UINotificationFeedbackGenerator().notificationOccurred(.error)
```

---

## 8. Navigation 구조

```
M2_TestView (TabView)
├── Tab 1: 음성  (mic.fill)
├── Tab 2: 텍스트  (text.bubble.fill)
└── Tab 3: 카메라  (video.fill)
         └── StreamView
                └── ChartDetailView (sheet)
```

---

## 9. 화면별 패턴

### StreamView
- 카메라 피드: 전체 너비, 16:9 비율, `Color.black` 배경
- 컨트롤바: 하단 고정, `ultraThinMaterial` 배경
- 촬영 버튼: 중앙 크게, 녹화 버튼: 좌측 작게
- 캡처 리뷰: `.sheet` 로 올라옴
- 분석 결과: sheet 안에서 표시 후 서버 전송

### ChartDetailView
- 네비게이션 large title
- 섹션별 카드 (F/U, Dx., S, P/E, PTx., Comment)
- 툴바: 공유 버튼 (`.shareSheet`)
- 로딩: `ProgressView` skeleton
