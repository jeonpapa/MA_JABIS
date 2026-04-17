# MA AI Dossier — Dashboard Design System

참고 레퍼런스: MeetCraft 스타일의 SaaS 대쉬보드 (보라·라벤더 파스텔 톤, 카드형 레이아웃, 좌측 고정 사이드바).
이 문서는 `dashboard/` 하위 모든 HTML·CSS·JS 생성물이 따라야 할 **디자인 표준**이다.

---

## 1. 디자인 철학

- **Soft Professional**: 제약 업계의 신뢰감 + SaaS의 친근함. 무채색 엔터프라이즈 대쉬보드 지양.
- **Glanceable**: MA 담당자가 2~3초 내 핵심 지표(가격 변동·편차·누락)를 파악 가능해야 함.
- **Card-first Layout**: 모든 정보는 `rounded-2xl` 카드 단위로 캡슐화. 표 단독 페이지 금지.
- **Pastel Accent**: 배경·카드는 저채도, 액션·하이라이트만 고채도 보라(#7C5CFC 계열).

---

## 2. 컬러 팔레트

### Primary (보라 계열 — 주요 CTA, 액티브 상태)
- `--primary-600: #6C4DF6`   (버튼·선택된 메뉴)
- `--primary-500: #7C5CFC`   (호버·링크)
- `--primary-100: #EBE5FF`   (선택 배경)
- `--primary-50:  #F5F1FF`   (가장 옅은 틴트)

### Surface (배경 그라디언트·카드)
- `--bg-gradient: linear-gradient(135deg, #F3EEFF 0%, #FBF5FF 50%, #F0ECFF 100%)`
- `--surface-0: #FFFFFF`           (카드 본체)
- `--surface-1: #FAFAFD`           (서브 카드·입력)
- `--sidebar-bg: #F7F3FF`          (사이드바)

### Semantic (상태·약가 컨텍스트)
- `--success: #22C55E`  가격 안정·검증 완료
- `--warning: #F59E0B`  편차 감지·리뷰 필요
- `--danger:  #EF4444`  누락·급등락·스크레이핑 실패
- `--info:    #3B82F6`  정보성 배지

### Category Tint (카드·이벤트 유형 구분 — 레퍼런스의 파스텔 블록)
- `--tint-lavender: #E8DEFF`  (국내 약가)
- `--tint-peach:    #FFE4D6`  (해외 약가)
- `--tint-mint:     #D6F3E4`  (환율·지표)
- `--tint-sky:      #DCEBFF`  (편차·알림)
- `--tint-pink:     #FFDDEC`  (보완 작업)

### Text
- `--text-900: #1A1830`  제목
- `--text-700: #3C3A52`  본문
- `--text-500: #6B6880`  서브·캡션
- `--text-300: #A9A6BD`  비활성

---

## 3. 타이포그래피

- **Font Stack**: `"Pretendard Variable", "Inter", -apple-system, "Apple SD Gothic Neo", sans-serif`
- **Scale**
  - `display` 28/36 700 — 대쉬보드 상단 타이틀
  - `h1` 22/30 700 — 섹션 헤더
  - `h2` 18/26 600 — 카드 헤더
  - `body` 14/22 400 — 본문·표
  - `caption` 12/18 500 — 라벨·메타
  - `metric` 32/40 700 tabular-nums — 약가 숫자
- **숫자**: 가격·편차는 `font-variant-numeric: tabular-nums` 필수. 통화 기호는 숫자보다 1단계 작게.

---

## 4. 레이아웃

### 전체 구조
```
┌───────────────────────────────────────────────────────┐
│ Sidebar (240px, fixed) │  Topbar (64px, sticky)       │
│                        ├──────────────────────────────┤
│  · Dashboard           │                              │
│  · 국내 약가           │    Main Content              │
│  · 해외 약가           │    (max-width 1440px,        │
│  · 편차 로그           │     gutter 32px,             │
│  · 스케줄러            │     grid gap 24px)           │
│  · 설정                │                              │
│                        │                              │
│  [Upgrade Card]        │                              │
└────────────────────────┴──────────────────────────────┘
```

- **Sidebar**: 240px 고정, `--sidebar-bg`, 메뉴 아이템 좌측에 4px 보라 바(액티브), 하단에 프로모/버전 카드.
- **Topbar**: 64px, 검색바(중앙) + 알림/프로필(우측). 그림자 대신 1px `rgba(26,24,48,0.06)` border-bottom.
- **Grid**: 12-col. 카드 최소 높이 140px. 섹션 간 여백 32px, 카드 간 24px.

### 카드 규격
- `border-radius: 20px`
- `background: var(--surface-0)`
- `box-shadow: 0 4px 20px rgba(108, 77, 246, 0.06)`
- `padding: 20px 24px`
- hover 시 `translateY(-2px)` + shadow 강화.

---

## 5. 컴포넌트 표준

### KPI / Upcoming Card (레퍼런스 상단 블록)
- 파스텔 tint 배경 + 좌상단 아이콘 원형 배지 + 우상단 "N days left" 알약.
- MA Dossier 적용: **주요 약제 모니터링 카드** (약제명 · 최신가 · 전월 대비 Δ · 마지막 업데이트).

### Table (Manage Events 영역)
- 헤더: `--surface-1`, 12px caps, 텍스트 500.
- Row: 64px 높이, hover `--primary-50`.
- Status 셀: 알약형 배지 — Upcoming=보라, Completed=민트, Deviation=앰버, Missing=레드.
- 약제/국가 셀: 아바타 대신 **국가 플래그 원형 32px** + 약제명.

### Buttons
- Primary: `--primary-600`, 텍스트 흰색, radius 12px, 14px / 600, padding 10px 16px.
- Secondary: 흰 배경 + 1px `--primary-100` 보더 + `--primary-600` 텍스트.
- Ghost: 텍스트만, hover 시 `--primary-50` 배경.

### Chart
- 라인차트: `--primary-500` 메인, 보조 시리즈는 `--tint-peach` 등 파스텔.
- 그리드: `rgba(26,24,48,0.06)` 점선.
- Tooltip: 흰 카드 + shadow + 14px 본문.

### Badge / Chip
- Radius 999px, 12px / 500, padding 4px 10px.
- 의미별 tint 배경 + 해당 semantic 텍스트 컬러.

---

## 6. 데이터 시각화 규칙

- **가격 변동**: 상승 = 레드, 하락 = 그린 (투자 도메인과 반대 — MA 관점에서 **인하 = 긍정**으로 해석하지 않음. 단순 방향만 표시하고 해석은 배지로).
- **국가 구분색**: JP/IT/FR/CH/UK/DE/US는 플래그 사용. 차트 라인 색상은 팔레트 순환(보라→피치→민트→스카이→핑크→라벤더→앰버).
- **결측값**: `—` + `--text-300`, 툴팁에 "비급여" 또는 "스크레이핑 실패 사유" 노출.

---

## 7. Motion

- Transition 기본: `cubic-bezier(0.2, 0.8, 0.2, 1)` 180ms.
- 카드 진입: fade+up 8px, stagger 40ms.
- 숫자 카운트업: 400ms (메트릭만).
- 과도한 애니메이션 금지 — 대쉬보드는 정적 안정감 우선.

---

## 8. 디렉터리·파일 구성

```
dashboard/
├── index.html
├── assets/
│   ├── css/
│   │   ├── tokens.css      # 위 컬러·폰트 변수 정의 (Single Source of Truth)
│   │   ├── base.css        # reset + 타이포
│   │   └── components.css  # card/button/table/badge
│   ├── js/
│   └── icons/              # lucide 또는 phosphor, 보라 스트로크
```

- **tokens.css** 외 곳에서 HEX 하드코딩 금지. 모든 색은 CSS 변수 참조.
- 신규 색상 필요 시 이 문서 먼저 갱신 → tokens.css 반영.

---

## 9. 접근성 & 반응형

- 본문 텍스트와 배경 명도 대비 ≥ 4.5:1. 파스텔 tint 위 텍스트는 `--text-900` 고정.
- 포커스 링: `2px solid var(--primary-500)` + 2px offset.
- Breakpoint: `≥1280` 12-col, `≥960` 8-col (사이드바 아이콘 전용 72px), `<960` 상단 드로어.
- 표는 960 미만에서 카드 리스트로 리플로우.

---

## 10. 체크리스트 (PR 전 자가검증)

- [ ] 모든 색상이 tokens.css 변수 참조인가
- [ ] 카드 radius 20px, shadow 규격 준수
- [ ] 숫자 셀에 tabular-nums 적용
- [ ] 결측값 `—` 표기 + 사유 툴팁
- [ ] 국가/약제 구분이 컬러 단독이 아닌 아이콘+텍스트 동반
- [ ] 키보드 포커스 링 노출
- [ ] 1280 / 960 / 모바일 3단 확인
